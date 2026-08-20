"""Rank root-cause hypotheses with deterministic rules, and surface a runbook.

Each rule produces a scored RootCauseHypothesis when it fires; the list is
sorted best-first. A low-confidence fallback guarantees a non-empty result so
downstream always has something to act on (see flow.md 5.3).
"""

from __future__ import annotations

from common.contracts import (
    EnrichmentContext,
    Playbook,
    RootCauseHypothesis,
    Situation,
)
from common.interfaces import PlaybookStore

_SATURATION_TOKENS = ("cpu", "mem", "memory", "disk", "saturation")


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

    hypotheses.sort(key=lambda h: h.confidence, reverse=True)
    return hypotheses


def surface_runbook(hypotheses: list[RootCauseHypothesis], store: PlaybookStore) -> Playbook | None:
    if not hypotheses:
        return None
    runbook_id = hypotheses[0].suggested_runbook_id
    if runbook_id is None:
        return None
    return store.get(runbook_id)
