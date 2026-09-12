from datetime import UTC, datetime

from common.contracts import (
    HitlMode,
    Playbook,
    RemediationStep,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
)
from services.governance.adapters.playbook_store import InMemoryPlaybookStore
from services.rca.adapters.runbook_selector import NullRunbookSelector
from services.rca.rank import rank_hypotheses, select_runbook

TS = datetime(2026, 9, 5, tzinfo=UTC)


def _situation(metric_name="cpu_usage"):
    ev = TelemetryEvent(
        source="p",
        kind=TelemetryKind.METRIC,
        name=metric_name,
        value=90.0,
        labels={"service": "web"},
        ts=TS,
        fingerprint="fp",
    )
    return Situation(
        id="sit-1",
        status=SituationStatus.DETECTED,
        member_events=[ev],
        severity="high",
        first_seen=TS,
        last_seen=TS,
        signature="sig-1",
    )


def _store():
    s = InMemoryPlaybookStore()
    s.register(
        Playbook(
            id="scale-service",
            name="Scale",
            match_rule="*",
            steps=[RemediationStep(action="scale", replicas=1)],
            hitl_mode=HitlMode.HITL,
            symptoms="high CPU or memory saturation",
        )
    )
    return s


class _FakeSelector:
    def __init__(self, result):  # (id, score) or None
        self._result = result
        self.called = False

    def select(self, situation, hypothesis, store):
        self.called = True
        return self._result


def test_rule_wins_and_selector_not_consulted():
    # cpu_usage triggers the saturation keyword rule -> scale-service
    sit = _situation("cpu_usage")
    hyps = rank_hypotheses(
        sit, __import__("common.contracts", fromlist=["EnrichmentContext"]).EnrichmentContext()
    )
    selector = _FakeSelector(("should-not-be-used", 0.99))
    pb, _score, source = select_runbook(hyps, sit, _store(), selector)
    assert source == "rule"
    assert pb.id == "scale-service"
    assert selector.called is False  # rule fired -> selector skipped


def test_semantic_fallback_when_no_rule_fires():
    # a metric with NO saturation/error/deploy signal -> no rule -> selector runs
    sit = _situation("weird_metric_xyz")
    from common.contracts import EnrichmentContext

    hyps = rank_hypotheses(sit, EnrichmentContext())
    selector = _FakeSelector(("scale-service", 0.88))
    pb, score, source = select_runbook(hyps, sit, _store(), selector)
    assert source == "semantic"
    assert pb.id == "scale-service" and score == 0.88
    assert selector.called is True


def test_no_rule_no_semantic_is_gap():
    sit = _situation("weird_metric_xyz")
    from common.contracts import EnrichmentContext

    hyps = rank_hypotheses(sit, EnrichmentContext())
    selector = _FakeSelector(None)  # selector finds nothing
    pb, _score, source = select_runbook(hyps, sit, _store(), selector)
    assert source == "none"
    assert pb is None


def test_null_selector_reproduces_rule_only_behavior():
    sit = _situation("weird_metric_xyz")
    from common.contracts import EnrichmentContext

    hyps = rank_hypotheses(sit, EnrichmentContext())
    pb, _score, source = select_runbook(hyps, sit, _store(), NullRunbookSelector())
    assert source == "none" and pb is None  # no rule + null selector = gap, exactly as today
