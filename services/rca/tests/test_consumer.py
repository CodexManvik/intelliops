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
from services.rca.adapters.explanation_provider import TemplateExplanationProvider
from services.rca.consumer import diagnose, run_consumer

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _situation(name="cpu_usage", labels=None):
    return Situation(
        id="sit-1",
        status=SituationStatus.DETECTED,
        member_events=[
            TelemetryEvent(
                source="prom",
                kind=TelemetryKind.METRIC,
                name=name,
                value=99.0,
                labels=labels or {"service": "web"},
                ts=NOW,
                fingerprint="fp",
            )
        ],
        severity="high",
        first_seen=NOW,
        last_seen=NOW,
        signature="sig",
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
    d = diagnose(
        _situation(), NullContextProvider(), InMemoryPlaybookStore(), TemplateExplanationProvider()
    )
    assert isinstance(d, DiagnosedSituation)
    assert d.situation.status == SituationStatus.DIAGNOSED
    assert len(d.hypotheses) >= 1
    assert d.suggested_runbook_id == "scale-service"  # cpu_usage → resource-exhaustion


def test_consumer_publishes_diagnosed_and_audits():
    sit = _situation()
    bus = ScriptedBus([{"data": sit.model_dump_json()}])
    audit = InMemoryAuditSink()
    run_consumer(
        bus,
        NullContextProvider(),
        InMemoryPlaybookStore(),
        audit,
        TemplateExplanationProvider(),
        threading.Event(),
    )

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
    run_consumer(
        bus,
        NullContextProvider(),
        InMemoryPlaybookStore(),
        InMemoryAuditSink(),
        TemplateExplanationProvider(),
        stop,
    )
    assert bus.published == []


class _StubExplainer:
    """Returns a fixed advisory string, to prove the explainer is wired
    without letting it influence ranking/confidence/runbook selection."""

    def explain(self, hypothesis, context, situation):
        return "ADVISORY"


def test_explanation_is_advisory_only_on_top_hypothesis():
    situation = _situation()
    template_result = diagnose(
        situation, NullContextProvider(), InMemoryPlaybookStore(), TemplateExplanationProvider()
    )
    stubbed_result = diagnose(
        situation, NullContextProvider(), InMemoryPlaybookStore(), _StubExplainer()
    )

    assert stubbed_result.hypotheses[0].explanation == "ADVISORY"
    # Advisory text must not affect confidence, ordering, or the suggested
    # runbook — only the explanation field differs from the template run.
    assert stubbed_result.hypotheses[0].confidence == template_result.hypotheses[0].confidence
    assert (
        stubbed_result.hypotheses[0].suggested_runbook_id
        == template_result.hypotheses[0].suggested_runbook_id
    )
    assert stubbed_result.suggested_runbook_id == template_result.suggested_runbook_id
    assert [h.confidence for h in stubbed_result.hypotheses] == [
        h.confidence for h in template_result.hypotheses
    ]
    assert [h.suggested_runbook_id for h in stubbed_result.hypotheses] == [
        h.suggested_runbook_id for h in template_result.hypotheses
    ]
