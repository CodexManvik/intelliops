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


def _situation_with_metric(name, value=99.0):
    """A Situation with one TelemetryEvent of the given metric name/value,
    labeled to a single service — for asserting per-metric-family routing."""
    return Situation(
        id="sit-1",
        status=SituationStatus.DETECTED,
        member_events=[
            TelemetryEvent(
                source="prom",
                kind=TelemetryKind.METRIC,
                name=name,
                value=value,
                labels={"service": "web"},
                ts=NOW,
                fingerprint="fp",
            )
        ],
        severity="high",
        first_seen=NOW,
        last_seen=NOW,
        signature="sig",
    )


def _situation_multi(*names, value=99.0):
    """A Situation whose member_events carry multiple co-occurring metric
    names (all on the same service) — for asserting confidence-table
    routing when a fault profile emits more than one metric at once
    (e.g. Phase 1's dependency_outage = error + latency, or db_exhaustion =
    db_pool + latency)."""
    return Situation(
        id="sit-1",
        status=SituationStatus.DETECTED,
        member_events=[
            TelemetryEvent(
                source="prom",
                kind=TelemetryKind.METRIC,
                name=name,
                value=value,
                labels={"service": "web"},
                ts=NOW,
                fingerprint=f"fp-{i}",
            )
            for i, name in enumerate(names)
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
    hyps = rank_hypotheses(_situation(name="unrecognized_metric"), ctx)
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
    ctx = enrich(_situation(name="unrecognized_metric"), NullContextProvider())
    hyps = rank_hypotheses(_situation(name="unrecognized_metric"), ctx)
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


# --- Phase 3: refined + new metric-family rules (selector OFF / not passed) ---
#
# The runbook set is closed at 3: restart-pod / scale-service / rollback-deploy.
# These tests pin the deterministic candidate-layer diagnosis per metric family
# using only the hardcoded fallback confidences (Task 3 later lets an embedding
# selector recompute confidence; these off-path invariants must hold either way).


def test_memory_leak_maps_to_restart_not_scale():
    # Corrected mapping: a memory leak/pressure metric must route to
    # restart-pod (0.65), NOT scale-service — new pods spun up by scaling
    # leak too, so restart is the right fix, not capacity.
    sit = _situation_with_metric("memory_usage_mb", value=800.0)
    hyps = rank_hypotheses(sit, EnrichmentContext())
    assert hyps[0].suggested_runbook_id == "restart-pod"
    assert hyps[0].confidence == 0.65


def test_db_pool_exhaustion_maps_to_restart():
    sit = _situation_with_metric("db_pool_in_use", value=20.0)
    hyps = rank_hypotheses(sit, EnrichmentContext())
    assert hyps[0].suggested_runbook_id == "restart-pod"
    assert hyps[0].confidence == 0.62


def test_latency_maps_to_scale():
    sit = _situation_with_metric("latency_p99_ms", value=700.0)
    hyps = rank_hypotheses(sit, EnrichmentContext())
    assert hyps[0].suggested_runbook_id == "scale-service"
    assert hyps[0].confidence == 0.55


def test_queue_depth_maps_to_scale():
    sit = _situation_with_metric("queue_depth", value=50.0)
    hyps = rank_hypotheses(sit, EnrichmentContext())
    assert hyps[0].suggested_runbook_id == "scale-service"


def test_request_rate_maps_to_scale():
    sit = _situation_with_metric("request_rate", value=5000.0)
    hyps = rank_hypotheses(sit, EnrichmentContext())
    assert hyps[0].suggested_runbook_id == "scale-service"


def test_error_still_maps_to_restart_not_scale():
    # The load-bearing error->restart invariant, with the selector off.
    sit = _situation_with_metric("meridian_error_rate", value=0.5)
    hyps = rank_hypotheses(sit, EnrichmentContext())
    assert hyps[0].suggested_runbook_id == "restart-pod"


def test_cpu_saturation_still_maps_to_scale():
    sit = _situation_with_metric("cpu_usage", value=95.0)
    hyps = rank_hypotheses(sit, EnrichmentContext())
    assert hyps[0].suggested_runbook_id == "scale-service"
    assert hyps[0].confidence == 0.6


def test_existing_deploy_rule_unchanged():
    # A recent-deploy context still wins outright (rollback-deploy at 0.8,
    # top), unaffected by the new metric-family rules.
    ctx = EnrichmentContext(
        recent_deploys=[{"service": "web", "version": "v2", "ts": NOW.isoformat()}]
    )
    hyps = rank_hypotheses(_situation(labels={"service": "web"}), ctx)
    assert hyps[0].suggested_runbook_id == "rollback-deploy"
    assert hyps[0].confidence == 0.8


def test_memory_metric_no_longer_fires_saturation_candidate():
    # _SATURATION_TOKENS no longer includes "mem"/"memory": a pure memory
    # metric must propose exactly one candidate (restart-pod), not also a
    # scale-service saturation candidate.
    sit = _situation_with_metric("memory_usage_mb", value=800.0)
    hyps = rank_hypotheses(sit, EnrichmentContext())
    assert not any(h.suggested_runbook_id == "scale-service" for h in hyps)


def test_selector_param_accepted_but_unused_for_confidence():
    # Task 2 adds `selector` to the signature for Task 3's benefit only —
    # passing one here must not change the off-path fallback confidences.
    sit = _situation_with_metric("memory_usage_mb", value=800.0)
    hyps_without = rank_hypotheses(sit, EnrichmentContext())
    hyps_with = rank_hypotheses(sit, EnrichmentContext(), None, object())
    assert hyps_with[0].suggested_runbook_id == hyps_without[0].suggested_runbook_id
    assert hyps_with[0].confidence == hyps_without[0].confidence == 0.65


def test_dependency_outage_maps_to_restart_not_scale():
    # This is the `dependency_outage` Phase-1 fault profile (error_rate up +
    # latency_p99 up, cpu held flat) — Phase-3 AC #5, the load-bearing
    # error->restart invariant under co-occurrence. Both the error rule
    # (0.58) and the latency rule (0.55) fire here; error must outrank
    # latency so the incident routes to restart-pod (recycle the process
    # behind the failing dependency), not scale-service (which would just
    # spin up new pods that hit the same down dependency).
    sit = _situation_multi("meridian_error_rate", "latency_p99_ms")
    hyps = rank_hypotheses(sit, EnrichmentContext())
    assert hyps[0].suggested_runbook_id == "restart-pod"


def test_db_exhaustion_with_latency_maps_to_restart():
    # This is the `db_exhaustion` Phase-1 fault profile (db_pool_in_use ->
    # db_pool_max + latency up) — Phase-3 AC #4. Both the db_pool rule
    # (0.62) and the latency rule (0.55) fire here; db_pool must outrank
    # latency so the incident routes to restart-pod (recycle connections),
    # not scale-service (which would just re-exhaust the same pool).
    sit = _situation_multi("db_pool_in_use", "latency_p99_ms")
    hyps = rank_hypotheses(sit, EnrichmentContext())
    assert hyps[0].suggested_runbook_id == "restart-pod"


# --- Task 3: embedding-computed confidence + provenance ---
#
# When a store + selector are both passed and the selector returns a float
# score for a candidate's playbook, that score REPLACES the rule-based
# fallback confidence (clamped to [0, 1]) and confidence_source is set to
# "embedding". Off path (store/selector None), on error, or when the
# selector abstains (returns None) the rule confidence stands unchanged and
# confidence_source is "rule". This never changes WHICH runbook a rule
# proposes — only how confident we say we are in it, and it only re-scores
# already-vetted, rule-proposed candidates among the closed 3-runbook
# catalog (never invents an id).


class _StubSelector:
    """A selector stub returning a canned {playbook_id: score} table."""

    def __init__(self, scores):
        self._scores = scores

    def score(self, situation, hypothesis, playbook):
        return self._scores.get(playbook.id)

    def select(self, *a, **k):
        return None


class _PlaybookStore:
    """Minimal in-memory store, seeded with the closed 3-runbook catalog —
    mirrors the inline Store pattern in test_surface_runbook_looks_up_top_hypothesis."""

    def __init__(self):
        self._playbooks = {
            "restart-pod": Playbook(
                id="restart-pod",
                name="Restart Pod",
                match_rule="x",
                steps=[RemediationStep(action="restart")],
                hitl_mode=HitlMode.HITL,
                symptoms="memory leak, wedged process, db pool exhaustion, error spike",
            ),
            "scale-service": Playbook(
                id="scale-service",
                name="Scale Service",
                match_rule="x",
                steps=[RemediationStep(action="scale", replicas=2)],
                hitl_mode=HitlMode.HITL,
                symptoms="cpu saturation, latency under load, request surge",
            ),
            "rollback-deploy": Playbook(
                id="rollback-deploy",
                name="Rollback Deploy",
                match_rule="x",
                steps=[RemediationStep(action="rollback_deploy")],
                hitl_mode=HitlMode.HITL,
                symptoms="recent deploy preceded the incident",
            ),
        }

    def register(self, playbook):
        self._playbooks[playbook.id] = playbook

    def get(self, playbook_id):
        return self._playbooks.get(playbook_id)

    def list(self):
        return list(self._playbooks.values())


def test_embedding_score_becomes_confidence():
    # cpu_usage rule proposes scale-service @0.60; the stub selector scores
    # scale-service's playbook at 0.91 — that becomes the top confidence.
    sit = _situation_with_metric("cpu_usage", value=95.0)
    sel = _StubSelector({"scale-service": 0.91})
    hyps = rank_hypotheses(sit, EnrichmentContext(), store=_PlaybookStore(), selector=sel)
    top = hyps[0]
    assert top.suggested_runbook_id == "scale-service"
    assert abs(top.confidence - 0.91) < 1e-6  # embedding score, not the 0.60 constant
    assert top.confidence_source == "embedding"


def test_falls_back_to_rule_confidence_when_selector_none():
    sit = _situation_with_metric("cpu_usage", value=95.0)
    hyps = rank_hypotheses(sit, EnrichmentContext(), store=None, selector=None)
    assert hyps[0].confidence == 0.60
    assert hyps[0].confidence_source in (None, "rule")


def test_embedding_error_keeps_rule_confidence():
    # If a selector's score() raises, rank_hypotheses must guard it -> rule
    # confidence stands and ranking never raises.
    class _Raises:
        def score(self, *a, **k):
            raise RuntimeError("boom")

        def select(self, *a, **k):
            return None

    sit = _situation_with_metric("cpu_usage", value=95.0)
    hyps = rank_hypotheses(sit, EnrichmentContext(), store=_PlaybookStore(), selector=_Raises())
    assert hyps[0].confidence == 0.60  # unchanged; ranking never raised
    assert hyps[0].confidence_source == "rule"


def test_memory_leak_restart_wins_on_embedding_fit():
    # Embedding scores restart-pod higher than scale for a memory-leak
    # incident — same winner as the rule invariant, now embedding-confirmed.
    sit = _situation_with_metric("memory_usage_mb", value=900.0)
    sel = _StubSelector({"restart-pod": 0.88, "scale-service": 0.40})
    hyps = rank_hypotheses(sit, EnrichmentContext(), store=_PlaybookStore(), selector=sel)
    assert hyps[0].suggested_runbook_id == "restart-pod"


def test_error_restart_invariant_holds_on():
    # The load-bearing error->restart invariant, with the selector ON: it
    # must hold with embedding scoring active, not just when off.
    sit = _situation_with_metric("meridian_error_rate", value=0.5)
    sel = _StubSelector({"restart-pod": 0.80, "scale-service": 0.30})
    hyps = rank_hypotheses(sit, EnrichmentContext(), store=_PlaybookStore(), selector=sel)
    assert hyps[0].suggested_runbook_id == "restart-pod"  # never scale, on
