"""RunbookSelector implementations: pick a runbook by semantic similarity.

NullRunbookSelector is the CI-safe default (runbook_selector_mode="off"): it
selects nothing, so the runbook comes purely from the keyword rules (today's
behavior). EmbeddingRunbookSelector (added later) does the real semantic match."""

from __future__ import annotations

from common.contracts import RootCauseHypothesis, Situation
from common.interfaces import PlaybookStore


class NullRunbookSelector:
    def select(
        self, situation: Situation, hypothesis: RootCauseHypothesis, store: PlaybookStore
    ) -> tuple[str, float] | None:
        return None
