import threading
from datetime import UTC, datetime

from common.contracts import (
    DiagnosedSituation,
    HitlMode,
    Playbook,
    RemediationOutcome,
    RemediationResult,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
)
from common.envelope import decode_model
from services.action.adapters.health import AlwaysHealthyChecker
from services.action.adapters.remediator import RecordingRemediator
from services.action.consumer import run_consumer
from services.governance.adapters.playbook_store import InMemoryPlaybookStore

NOW = datetime(2026, 8, 13, tzinfo=UTC)


class FakeGate:
    def check_rbac(self, actor, action, resource): return True
    def request_approval(self, request): return request
    def await_decision(self, approval_id, timeout_seconds): return None
    def write_audit(self, record): ...


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
        id="s1", status=SituationStatus.DIAGNOSED,
        member_events=[TelemetryEvent(source="p", kind=TelemetryKind.METRIC, name="cpu",
                                      value=1.0, labels={}, ts=NOW, fingerprint="f")],
        severity="high", first_seen=NOW, last_seen=NOW, signature="sig",
    )
    d = DiagnosedSituation(situation=sit, hypotheses=[], suggested_runbook_id=runbook_id)
    return {"data": d.model_dump_json()}


def _store():
    s = InMemoryPlaybookStore()
    s.register(Playbook(id="restart-pod", name="Restart", match_rule="x", steps=["do"],
                        hitl_mode=HitlMode.AUTO, reversible=True, rollback_steps=["undo"]))
    return s


def _run(bus):
    run_consumer(bus, _store(), FakeGate(), RecordingRemediator(), AlwaysHealthyChecker(),
                 timeout_seconds=1.0, poll_interval_seconds=0.01, stop_event=threading.Event())


def test_consumer_emits_success_outcome():
    bus = ScriptedBus([_diagnosed("restart-pod")])
    _run(bus)
    outcomes = [m for (t, m) in bus.published if t == "remediation.outcomes"]
    assert len(outcomes) == 1
    o = decode_model(outcomes[0], RemediationOutcome)
    assert o.result == RemediationResult.SUCCESS
    assert o.situation_id == "s1"


def test_consumer_emits_skipped_when_no_playbook():
    bus = ScriptedBus([_diagnosed("unknown-runbook")])
    _run(bus)
    o = decode_model(next(m for (t, m) in bus.published if t == "remediation.outcomes"),
                     RemediationOutcome)
    assert o.result == RemediationResult.FAILURE
    assert o.health_after == "skipped:no-playbook"


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
    run_consumer(bus, _store(), FakeGate(), RecordingRemediator(), AlwaysHealthyChecker(),
                 timeout_seconds=1.0, poll_interval_seconds=0.01, stop_event=stop)
    assert bus.published == []
