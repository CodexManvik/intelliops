import threading
from datetime import UTC, datetime

from common.contracts import (
    DiagnosedSituation,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
)
from common.envelope import decode_model
from services.governance.adapters.audit_sink import InMemoryAuditSink
from services.governance.adapters.playbook_store import InMemoryPlaybookStore
from services.rca.adapters.context_provider import NullContextProvider
from services.rca.consumer import diagnose, run_consumer

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _situation(name="cpu_usage", labels=None):
    return Situation(
        id="sit-1", status=SituationStatus.DETECTED,
        member_events=[TelemetryEvent(
            source="prom", kind=TelemetryKind.METRIC, name=name, value=99.0,
            labels=labels or {"service": "web"}, ts=NOW, fingerprint="fp",
        )],
        severity="high", first_seen=NOW, last_seen=NOW, signature="sig",
    )


class ScriptedBus:
    def __init__(self, script):
        self._script = script
        self.published = []

    def publish(self, topic, message):
        self.published.append((topic, message))

    def consume(self, topic, group):
        yield from self._script


def test_diagnose_sets_status_and_hypotheses():
    d = diagnose(_situation(), NullContextProvider(), InMemoryPlaybookStore())
    assert isinstance(d, DiagnosedSituation)
    assert d.situation.status == SituationStatus.DIAGNOSED
    assert len(d.hypotheses) >= 1
    assert d.suggested_runbook_id == "scale-service"  # cpu_usage → resource-exhaustion


def test_consumer_publishes_diagnosed_and_audits():
    sit = _situation()
    bus = ScriptedBus([{"data": sit.model_dump_json()}])
    audit = InMemoryAuditSink()
    run_consumer(bus, NullContextProvider(), InMemoryPlaybookStore(), audit, threading.Event())

    diagnosed = [m for (t, m) in bus.published if t == "situations.diagnosed"]
    assert len(diagnosed) == 1
    d = decode_model(diagnosed[0], DiagnosedSituation)
    assert d.situation.id == "sit-1"
    assert d.situation.status == SituationStatus.DIAGNOSED
    # audit record written, threaded by correlation_id == situation id
    records = audit.records()
    assert len(records) == 1
    assert records[0].action == "diagnose"
    assert records[0].correlation_id == "sit-1"


def test_consumer_stops_on_stop_event():
    def infinite():
        while True:
            yield {"data": _situation().model_dump_json()}

    class InfBus(ScriptedBus):
        def consume(self, topic, group):
            return infinite()

    bus = InfBus([])
    stop = threading.Event()
    stop.set()
    run_consumer(bus, NullContextProvider(), InMemoryPlaybookStore(),
                 InMemoryAuditSink(), stop)
    assert bus.published == []
