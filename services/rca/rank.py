"""Rank root-cause hypotheses with deterministic rules, and surface a runbook.

Each rule produces a scored RootCauseHypothesis when it fires; the list is
sorted best-first. A low-confidence fallback guarantees a non-empty result so
downstream always has something to act on (see flow.md 5.3).

An optional `reliability_provider` (situation.signature -> float in [0, 1],
e.g. the correlator's learned worked/total track record) can boost a
hypothesis whose suggested runbook has proven reliable for this signature.
Passing None preserves the original rule-only ranking exactly."""

from __future__ import annotations

from collections.abc import Callable

from common.contracts import (
    EnrichmentContext,
    Playbook,
    RootCauseHypothesis,
    Situation,
)
from common.interfaces import PlaybookStore

_SATURATION_TOKENS = ("cpu", "mem", "memory", "disk", "saturation")

# How much weight the learned reliability signal carries against rule-based
# confidence. Small enough that a proven-reliable low-confidence rule can
# nudge ahead of a slightly-higher rule, but never enough to invert a large
# confidence gap on its own.
_RELIABILITY_WEIGHT = 0.15


def _service_labels(situation: Situation) -> set[str]:
    services: set[str] = set()
    for event in situation.member_events:
        svc = event.labels.get("service")
        if svc:
            services.add(svc)
    return services


def rank_hypotheses(situation: Situation, context: EnrichmentContext) -> list[RootCauseHypothesis]:
    hypotheses: list[RootCauseHypothesis] = []
    services = _service_labels(situation)

    # Rule: a recent deploy touching one of the situation's services.
    deploy_hit = next((d for d in context.recent_deploys if d.get("service") in services), None)
    deploy_hit = next((d for d in context.recent_deploys if d.get("service") in services), None)
    if deploy_hit is not None:
        hypotheses.append(
            RootCauseHypothesis(
                situation_id=situation.id,
                description=f"recent deployment of {deploy_hit.get('service')} "
                f"({deploy_hit.get('version')}) preceded the incident",
                confidence=0.8,
                evidence=[f"deploy {deploy_hit.get('service')}@{deploy_hit.get('version')}"],
                suggested_runbook_id="rollback-deploy",
            )
        )
        hypotheses.append(
            RootCauseHypothesis(
                situation_id=situation.id,
                description=f"recent deployment of {deploy_hit.get('service')} "
                f"({deploy_hit.get('version')}) preceded the incident",
                confidence=0.8,
                evidence=[f"deploy {deploy_hit.get('service')}@{deploy_hit.get('version')}"],
                suggested_runbook_id="rollback-deploy",
            )
        )

    # Rule: resource-saturation metric names.
    names = " ".join(e.name.lower() for e in situation.member_events)
    if any(tok in names for tok in _SATURATION_TOKENS):
        hypotheses.append(
            RootCauseHypothesis(
                situation_id=situation.id,
                description="resource saturation across the affected service",
                confidence=0.6,
                evidence=[f"metrics: {names}"],
                suggested_runbook_id="scale-service",
            )
        )
        hypotheses.append(
            RootCauseHypothesis(
                situation_id=situation.id,
                description="resource saturation across the affected service",
                confidence=0.6,
                evidence=[f"metrics: {names}"],
                suggested_runbook_id="scale-service",
            )
        )

    # Rule: log/error events.
    if any(e.kind.value in ("log",) or "error" in e.name.lower() for e in situation.member_events):
        hypotheses.append(
            RootCauseHypothesis(
                situation_id=situation.id,
                description="error spike in service logs",
                confidence=0.5,
                evidence=["log/error events present"],
                suggested_runbook_id="restart-pod",
            )
        )
    if any(e.kind.value in ("log",) or "error" in e.name.lower() for e in situation.member_events):
        hypotheses.append(
            RootCauseHypothesis(
                situation_id=situation.id,
                description="error spike in service logs",
                confidence=0.5,
                evidence=["log/error events present"],
                suggested_runbook_id="restart-pod",
            )
        )

    # Fallback: always give downstream something.
    if not hypotheses:
        hypotheses.append(
            RootCauseHypothesis(
                situation_id=situation.id,
                description="root cause undetermined from available signals",
                confidence=0.2,
                evidence=[],
                suggested_runbook_id=None,
            )
        )
        hypotheses.append(
            RootCauseHypothesis(
                situation_id=situation.id,
                description="root cause undetermined from available signals",
                confidence=0.2,
                evidence=[],
                suggested_runbook_id=None,
            )
        )

    if reliability_provider is None:
        hypotheses.sort(key=lambda h: h.confidence, reverse=True)
        return hypotheses

    # Reliability-weighted ranking: a hypothesis whose suggested runbook has a
    # proven track record for this situation's signature gets a bounded boost.
    # This only ever re-orders hypotheses that already have a suggested
    # runbook — the fallback (runbook_id=None) is never boosted, so the top
    # suggestion after ranking still resolves to a real playbook id whenever
    # any rule-based hypothesis fired.
    reliability = reliability_provider(situation.signature) if situation.signature else 0.0
    reliability = max(0.0, min(1.0, reliability))

    def _score(h: RootCauseHypothesis) -> float:
        boost = _RELIABILITY_WEIGHT * reliability if h.suggested_runbook_id is not None else 0.0
        return min(1.0, h.confidence + boost)

    hypotheses.sort(key=_score, reverse=True)
    return hypotheses


def surface_runbook(hypotheses: list[RootCauseHypothesis], store: PlaybookStore) -> Playbook | None:
def surface_runbook(hypotheses: list[RootCauseHypothesis], store: PlaybookStore) -> Playbook | None:
    if not hypotheses:
        return None
    runbook_id = hypotheses[0].suggested_runbook_id
    if runbook_id is None:
        return None
    return store.get(runbook_id)
