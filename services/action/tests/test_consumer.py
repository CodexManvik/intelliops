import threading
from datetime import UTC, datetime

from common.contracts import (
    DiagnosedSituation,
    HitlMode,
    Playbook,
    RemediationOutcome,
    RemediationResult,
    RemediationStep,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
)
from common.envelope import decode_model
from services.action.adapters.health import AlwaysHealthyChecker
from services.action.adapters.remediator import RecordingRemediator
from services.action.adapters.sandbox import NullSandbox
from services.action.consumer import run_consumer
from services.governance.adapters.playbook_store import InMemoryPlaybookStore

NOW = datetime(2026, 8, 13, tzinfo=UTC)


class FakeGate:
    def __init__(self):
        self.audits = []

    def check_rbac(self, actor, action, resource):
        return True

    def request_approval(self, request):
        return request

    def await_decision(self, approval_id, timeout_seconds):
        return None

    def write_audit(self, record):
        self.audits.append(record)


class ScriptedBus:
    def __init__(self, script):
        self._script = script
        self.published = []

    def publish(self, topic, message):
        self.published.append((topic, message))

    def consume(self, topic, group):
        yield from self._script


def _diagnosed(runbook_id):
    sit = Situation(
        id="s1",
        status=SituationStatus.DIAGNOSED,
        member_events=[
            TelemetryEvent(
                source="p",
                kind=TelemetryKind.METRIC,
                name="cpu",
                value=1.0,
                labels={},
                ts=NOW,
                fingerprint="f",
            )
        ],
        severity="high",
        first_seen=NOW,
        last_seen=NOW,
        signature="sig",
    )
    d = DiagnosedSituation(situation=sit, hypotheses=[], suggested_runbook_id=runbook_id)
    return {"data": d.model_dump_json()}


def _store():
    s = InMemoryPlaybookStore()
    s.register(
        Playbook(
            id="restart-pod",
            name="Restart",
            match_rule="x",
            steps=[RemediationStep(action="restart")],
            hitl_mode=HitlMode.AUTO,
            reversible=True,
            rollback_steps=[RemediationStep(action="restart")],
        )
    )
    return s


def _run(bus, gate=None, remediator=None):
    run_consumer(
        bus,
        _store(),
        gate or FakeGate(),
        remediator or RecordingRemediator(),
        AlwaysHealthyChecker(),
        NullSandbox(),
        timeout_seconds=1.0,
        poll_interval_seconds=0.01,
        stop_event=threading.Event(),
    )


def test_consumer_emits_success_outcome():
    bus = ScriptedBus([_diagnosed("restart-pod")])
    _run(bus)
    outcomes = [m for (t, m) in bus.published if t == "remediation.outcomes"]
    assert len(outcomes) == 1
    o = decode_model(outcomes[0], RemediationOutcome)
    assert o.result == RemediationResult.SUCCESS
    assert o.situation_id == "s1"


def _only_outcome(bus):
    return decode_model(
        next(m for (t, m) in bus.published if t == "remediation.outcomes"), RemediationOutcome
    )


def test_consumer_escalates_when_no_playbook():
    # An id was suggested, but the store cannot resolve it.
    bus = ScriptedBus([_diagnosed("unknown-runbook")])
    _run(bus)
    o = _only_outcome(bus)
    assert o.result == RemediationResult.ESCALATED
    assert o.health_after == "escalated:unknown-runbook"
    # Provenance is kept: an escalation frequently carries a real id, so
    # downstream filters must key on the result enum, not on an empty id.
    assert o.playbook_id == "unknown-runbook"
    assert o.mode == "none"
    assert o.hitl_mode == HitlMode.DISABLED


def test_consumer_escalates_when_no_diagnosis():
    bus = ScriptedBus([_diagnosed(None)])
    _run(bus)
    o = _only_outcome(bus)
    assert o.result == RemediationResult.ESCALATED
    assert o.health_after == "escalated:no-diagnosis"
    assert o.playbook_id == ""


def test_consumer_escalation_writes_audit_record():
    bus = ScriptedBus([_diagnosed("unknown-runbook")])
    gate = FakeGate()
    _run(bus, gate=gate)
    assert len(gate.audits) == 1
    record = gate.audits[0]
    assert record.action == "escalate"
    assert record.resource == "situation:s1"
    assert record.correlation_id == "s1"


def test_consumer_escalation_never_touches_the_remediator():
    bus = ScriptedBus([_diagnosed("unknown-runbook")])
    remediator = RecordingRemediator()
    _run(bus, remediator=remediator)
    assert remediator.executed_plan is None
    assert remediator.rolled_back_plan is None


def test_consumer_escalation_survives_audit_sink_failure():
    class FailingGate(FakeGate):
        def write_audit(self, record):
            raise RuntimeError("sink down")

    bus = ScriptedBus([_diagnosed("unknown-runbook")])
    _run(bus, gate=FailingGate())
    assert _only_outcome(bus).result == RemediationResult.ESCALATED


def test_consumer_stops_on_stop_event():
    def infinite():
        while True:
            yield _diagnosed("restart-pod")

    class InfBus(ScriptedBus):
        def consume(self, topic, group):
            return infinite()

    bus = InfBus([])
    stop = threading.Event()
    stop.set()
    run_consumer(
        bus,
        _store(),
        FakeGate(),
        RecordingRemediator(),
        AlwaysHealthyChecker(),
        NullSandbox(),
        timeout_seconds=1.0,
        poll_interval_seconds=0.01,
        stop_event=stop,
    )
    assert bus.published == []
