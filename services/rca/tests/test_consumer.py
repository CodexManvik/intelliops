import threading
from datetime import UTC, datetime

from common.contracts import (
    DiagnosedSituation,
    HitlMode,
    Playbook,
    RemediationStep,
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
        lambda: TemplateExplanationProvider(),
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
        lambda: TemplateExplanationProvider(),
        stop,
    )
    assert bus.published == []


class _StubExplainer:
    """Returns a fixed advisory string, to prove the explainer is wired
    without letting it influence ranking/confidence/runbook selection."""

    def explain(self, hypothesis, context, situation):
        return "ADVISORY"

    def explain_with_source(self, hypothesis, context, situation):
        return "ADVISORY", "llm"


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


def test_diagnose_sets_explanation_source_from_provider():
    situation = _situation()
    template_result = diagnose(
        situation, NullContextProvider(), InMemoryPlaybookStore(), TemplateExplanationProvider()
    )
    stubbed_result = diagnose(
        situation, NullContextProvider(), InMemoryPlaybookStore(), _StubExplainer()
    )

    assert template_result.hypotheses[0].explanation_source == "template"
    assert stubbed_result.hypotheses[0].explanation_source == "llm"


def _seeded_store():
    """InMemoryPlaybookStore registered with the closed 3-runbook catalog, so
    rank_hypotheses' embedding re-scoring pass can resolve store.get(id) for
    whichever runbook a rule proposes."""
    store = InMemoryPlaybookStore()
    store.register(
        Playbook(
            id="restart-pod",
            name="Restart Pod",
            match_rule="x",
            steps=[RemediationStep(action="restart")],
            hitl_mode=HitlMode.HITL,
            symptoms=(
                "crash loops, wedged or hung process, memory leak trending to OOM, "
                "database connection-pool exhaustion, elevated error rate, stuck workers "
                "— a process that must be recycled"
            ),
        )
    )
    store.register(
        Playbook(
            id="scale-service",
            name="Scale Service Horizontally",
            match_rule="x",
            steps=[RemediationStep(action="scale", replicas=2)],
            hitl_mode=HitlMode.HITL,
            symptoms=(
                "CPU or resource saturation, high latency under load, growing queue depth, "
                "a traffic surge or capacity shortfall — needs more replicas"
            ),
        )
    )
    store.register(
        Playbook(
            id="rollback-deploy",
            name="Rollback Deployment",
            match_rule="x",
            steps=[RemediationStep(action="rollback_deploy")],
            hitl_mode=HitlMode.HITL,
            symptoms=(
                "a regression immediately after a recent deployment or release, "
                "a bad new version misbehaving — revert to the prior revision"
            ),
        )
    )
    return store


class _StubSelector:
    """Modeled on Task 3's test_rank._StubSelector: score() returns a fixed
    known float regardless of playbook, select() abstains (returns None) so
    the semantic-fallback path in select_runbook is never exercised here."""

    def __init__(self, fixed_score):
        self._fixed_score = fixed_score

    def score(self, situation, hypothesis, playbook):
        return self._fixed_score

    def select(self, *a, **k):
        return None


def test_diagnose_uses_embedding_confidence_when_selector_on():
    situation = _situation()  # cpu_usage -> rule proposes scale-service @0.60
    sel = _StubSelector(0.93)
    d = diagnose(
        situation,
        NullContextProvider(),
        _seeded_store(),
        TemplateExplanationProvider(),
        selector=sel,
    )
    top = d.hypotheses[0]
    assert top.confidence_source == "embedding"
    assert abs(top.confidence - 0.93) < 1e-6


def test_diagnose_null_selector_confidence_source_rule_or_none():
    situation = _situation()
    baseline = diagnose(
        situation, NullContextProvider(), InMemoryPlaybookStore(), TemplateExplanationProvider()
    )
    d = diagnose(
        situation,
        NullContextProvider(),
        InMemoryPlaybookStore(),
        TemplateExplanationProvider(),
        # selector omitted -> defaults to None -> NullRunbookSelector inside diagnose
    )
    top = d.hypotheses[0]
    assert top.confidence_source in (None, "rule")
    # off path: unchanged from today (byte-identical suggested_runbook_id/status).
    assert d.suggested_runbook_id == baseline.suggested_runbook_id == "scale-service"
    assert d.situation.status == SituationStatus.DIAGNOSED
    assert top.confidence == baseline.hypotheses[0].confidence
