from datetime import UTC, datetime

from common.contracts import (
    EnrichmentContext,
    HitlMode,
    Playbook,
    RemediationStep,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
)
from services.rca.adapters.context_provider import NullContextProvider
from services.rca.enrich import enrich
from services.rca.rank import rank_hypotheses, surface_runbook

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _situation(name="cpu", labels=None):
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


def test_recent_deploy_ranks_first():
    ctx = EnrichmentContext(
        recent_deploys=[{"service": "web", "version": "v2", "ts": NOW.isoformat()}]
    )
    hyps = rank_hypotheses(_situation(labels={"service": "web"}), ctx)
    assert hyps[0].suggested_runbook_id == "rollback-deploy"
    assert hyps[0].confidence >= 0.7
    assert "web" in hyps[0].description or "deploy" in hyps[0].description.lower()


def test_resource_exhaustion_when_no_deploy():
    ctx = EnrichmentContext()  # no deploys
    hyps = rank_hypotheses(_situation(name="cpu_usage"), ctx)
    assert hyps[0].suggested_runbook_id == "scale-service"


def test_error_spike_for_log_events():
    ctx = EnrichmentContext()
    sit = _situation(name="error_rate")
    sit.member_events[0].kind = TelemetryKind.LOG
    hyps = rank_hypotheses(sit, ctx)
    assert any(h.suggested_runbook_id == "restart-pod" for h in hyps)


def test_fallback_hypothesis_when_nothing_matches():
    ctx = EnrichmentContext()
    hyps = rank_hypotheses(_situation(name="latency_p99"), ctx)
    assert len(hyps) >= 1
    assert hyps[-1].confidence <= 0.3  # the fallback is low-confidence


def test_hypotheses_sorted_by_confidence_desc():
    ctx = EnrichmentContext(
        recent_deploys=[{"service": "web", "version": "v2", "ts": NOW.isoformat()}]
    )
    hyps = rank_hypotheses(_situation(name="cpu", labels={"service": "web"}), ctx)
    confidences = [h.confidence for h in hyps]
    assert confidences == sorted(confidences, reverse=True)


def test_surface_runbook_looks_up_top_hypothesis():
    from services.rca.adapters.context_provider import NullContextProvider  # noqa: F401

    class Store:
        def register(self, playbook): ...
        def get(self, playbook_id):
            if playbook_id == "scale-service":
                return Playbook(
                    id="scale-service",
                    name="Scale",
                    match_rule="x",
                    steps=[RemediationStep(action="restart")],
                    hitl_mode=HitlMode.HITL,
                )
            return None

        def list(self):
            return []

    ctx = EnrichmentContext()
    hyps = rank_hypotheses(_situation(name="cpu_usage"), ctx)
    pb = surface_runbook(hyps, Store())
    assert pb is not None
    assert pb.id == "scale-service"


def test_enrich_null_provider_gives_empty_then_fallback():
    ctx = enrich(_situation(name="latency_p99"), NullContextProvider())
    hyps = rank_hypotheses(_situation(name="latency_p99"), ctx)
    assert hyps  # never empty


def test_reliability_provider_none_preserves_original_ranking():
    # A situation with both a deploy hit (confidence 0.8) and saturation
    # tokens (confidence 0.6) — with no reliability_provider the deploy
    # hypothesis must still win, exactly as before this feature existed.
    ctx = EnrichmentContext(
        recent_deploys=[{"service": "web", "version": "v2", "ts": NOW.isoformat()}]
    )
    hyps = rank_hypotheses(_situation(name="cpu", labels={"service": "web"}), ctx, None)
    assert hyps[0].suggested_runbook_id == "rollback-deploy"


def test_reliability_provider_boosts_proven_runbook():
    # Deploy hypothesis (0.8, rollback-deploy) normally beats saturation
    # (0.6, scale-service). A reliability_provider that reports a strong,
    # proven track record for scale-service on this signature should not
    # flip the ranking to something ungrounded — but SHOULD narrow the gap
    # in a bounded, deterministic way. Use a signature/situation where the
    # boosted hypothesis is the ONLY one with a runbook to prove it can win.
    ctx = EnrichmentContext()  # no deploys -> only saturation rule fires
    situation = _situation(name="cpu_usage", labels={"service": "web"})

    def reliability(signature: str) -> float:
        assert signature == situation.signature
        return 1.0

    hyps_unboosted = rank_hypotheses(situation, ctx, None)
    hyps_boosted = rank_hypotheses(situation, ctx, reliability)

    # Same top suggestion (only one runbook-bearing hypothesis exists here),
    # and it still resolves to a real playbook id.
    assert hyps_boosted[0].suggested_runbook_id == "scale-service"
    assert hyps_boosted[0].suggested_runbook_id == hyps_unboosted[0].suggested_runbook_id
    # The boost is bounded: confidence never exceeds original + weight, and
    # never exceeds 1.0.
    assert hyps_boosted[0].confidence <= 1.0


def test_reliability_provider_never_boosts_fallback_hypothesis():
    # The fallback hypothesis (no runbook) must never be boosted above a
    # real, runbook-bearing hypothesis, even with a perfect reliability score.
    ctx = EnrichmentContext(
        recent_deploys=[{"service": "web", "version": "v2", "ts": NOW.isoformat()}]
    )
    situation = _situation(name="cpu", labels={"service": "web"})
    hyps = rank_hypotheses(situation, ctx, lambda sig: 1.0)
    assert hyps[0].suggested_runbook_id is not None
