"""AuthorDecisionStore: the runbook author's memory of its own drafting decisions.

InMemory (tests) here; Postgres in the same module (Task 2). Never raises on a
missing target — updates are no-ops when nothing matches (a missed update just
leaves less signal, never wrong signal)."""

from __future__ import annotations

from common.contracts import AuthorDecision, AuthorDecisionDisposition, AuthorDecisionOutcome


class InMemoryAuthorDecisionStore:
    def __init__(self) -> None:
        self._items: list[AuthorDecision] = []

    def record(self, decision: AuthorDecision) -> None:
        self._items.append(decision)

    def by_signature(self, signature: str) -> list[AuthorDecision]:
        return [d for d in self._items if d.signature == signature]

    def update_disposition(self, proposal_id: str, disposition: str | AuthorDecisionDisposition, decided_by: str) -> None:
        disposition = AuthorDecisionDisposition(disposition)
        for i, d in enumerate(self._items):
            if d.proposal_id == proposal_id:
                self._items[i] = d.model_copy(
                    update={"disposition": disposition, "decided_by": decided_by}
                )

    def update_outcome(self, playbook_id: str, outcome: str | AuthorDecisionOutcome, health_after: str) -> None:
        outcome = AuthorDecisionOutcome(outcome)
        for i, d in enumerate(self._items):
            if d.playbook_id == playbook_id:
                self._items[i] = d.model_copy(update={"outcome": outcome})
