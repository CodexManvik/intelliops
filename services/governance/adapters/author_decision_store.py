"""AuthorDecisionStore: the runbook author's memory of its own drafting decisions.

InMemory (tests) here; Postgres in the same module (Task 2). Never raises on a
missing target — updates are no-ops when nothing matches (a missed update just
leaves less signal, never wrong signal)."""

from __future__ import annotations

from sqlalchemy import select

from common.contracts import AuthorDecision, AuthorDecisionDisposition, AuthorDecisionOutcome
from common.db import author_decisions, from_payload, to_payload


class InMemoryAuthorDecisionStore:
    def __init__(self) -> None:
        self._items: list[AuthorDecision] = []

    def record(self, decision: AuthorDecision) -> None:
        self._items.append(decision)

    def by_signature(self, signature: str) -> list[AuthorDecision]:
        return [d for d in self._items if d.signature == signature]

    def update_disposition(
        self, proposal_id: str, disposition: str | AuthorDecisionDisposition, decided_by: str
    ) -> None:
        disposition = AuthorDecisionDisposition(disposition)
        for i, d in enumerate(self._items):
            if d.proposal_id == proposal_id:
                self._items[i] = d.model_copy(
                    update={"disposition": disposition, "decided_by": decided_by}
                )

    def update_outcome(
        self, playbook_id: str, outcome: str | AuthorDecisionOutcome, health_after: str
    ) -> None:
        outcome = AuthorDecisionOutcome(outcome)
        for i, d in enumerate(self._items):
            if d.playbook_id == playbook_id:
                self._items[i] = d.model_copy(update={"outcome": outcome})


class PostgresAuthorDecisionStore:
    """AuthorDecision store backed by Postgres. `record` errors propagate to the
    caller (the caller wraps it best-effort); `by_signature` failures are the
    tool layer's concern (Task 4), not this adapter's.

    disposition/outcome are promoted typed columns kept consistent with the
    JSONB payload by doing every update as a read-modify-write within one
    transaction — never two separate statements that could disagree."""

    def __init__(self, engine) -> None:
        self._engine = engine

    def record(self, decision: AuthorDecision) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                author_decisions.insert().values(
                    signature=decision.signature,
                    proposal_id=decision.proposal_id,
                    playbook_id=decision.playbook_id,
                    disposition=decision.disposition.value,
                    outcome=decision.outcome.value,
                    ts=decision.ts,
                    payload=to_payload(decision),
                )
            )

    def by_signature(self, signature: str) -> list[AuthorDecision]:
        stmt = (
            select(author_decisions.c.payload)
            .where(author_decisions.c.signature == signature)
            .order_by(author_decisions.c.id)
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [from_payload(row.payload, AuthorDecision) for row in rows]

    def update_disposition(
        self, proposal_id: str, disposition: str | AuthorDecisionDisposition, decided_by: str
    ) -> None:
        disposition = AuthorDecisionDisposition(disposition)
        with self._engine.begin() as conn:
            stmt = select(author_decisions.c.id, author_decisions.c.payload).where(
                author_decisions.c.proposal_id == proposal_id
            )
            rows = conn.execute(stmt).all()
            for row in rows:
                decision = from_payload(row.payload, AuthorDecision)
                updated = decision.model_copy(
                    update={"disposition": disposition, "decided_by": decided_by}
                )
                conn.execute(
                    author_decisions.update()
                    .where(author_decisions.c.id == row.id)
                    .values(disposition=disposition.value, payload=to_payload(updated))
                )

    def update_outcome(
        self, playbook_id: str, outcome: str | AuthorDecisionOutcome, health_after: str
    ) -> None:
        outcome = AuthorDecisionOutcome(outcome)
        with self._engine.begin() as conn:
            stmt = select(author_decisions.c.id, author_decisions.c.payload).where(
                author_decisions.c.playbook_id == playbook_id
            )
            rows = conn.execute(stmt).all()
            for row in rows:
                decision = from_payload(row.payload, AuthorDecision)
                updated = decision.model_copy(update={"outcome": outcome})
                conn.execute(
                    author_decisions.update()
                    .where(author_decisions.c.id == row.id)
                    .values(outcome=outcome.value, payload=to_payload(updated))
                )
