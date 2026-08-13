"""Enrich a Situation with change/deploy/topology context.

Context is what makes a root-cause suggestion credible rather than a guess
(see flow.md 5.3). Pure function of (situation, provider).
"""

from __future__ import annotations

from common.contracts import EnrichmentContext, Situation
from common.interfaces import ContextProvider


def _merged_labels(situation: Situation) -> dict[str, str]:
    labels: dict[str, str] = {}
    for event in situation.member_events:
        labels.update(event.labels)
    return labels


def enrich(situation: Situation, provider: ContextProvider) -> EnrichmentContext:
    return EnrichmentContext(
        recent_deploys=provider.recent_deploys(),
        topology=provider.topology_for(_merged_labels(situation)),
        config_changes=provider.config_changes(),
    )
