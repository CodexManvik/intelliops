"""ProposedPlaybookStore implementations: in-memory (tests) and Postgres.

AI runbook proposals awaiting human approval are live runtime state — a plain
dict today, lost on restart (issue #56). Postgres makes them durable so a
pending proposal survives a governance restart before a human decides on it.
Errors PROPAGATE — same posture as PostgresApprovalStore/
PostgresAuthorDecisionStore (a lost proposal write is a correctness failure,
not a best-effort side record)."""

from __future__ import annotations

from sqlalchemy import select

from common.contracts import ProposedPlaybook, ProposedPlaybookStatus
from common.db import from_payload, proposed_playbooks, to_payload


class InMemoryProposedPlaybookStore:
    def __init__(self) -> None:
        self._by_id: dict[str, ProposedPlaybook] = {}

    def add(self, proposal: ProposedPlaybook) -> None:
        self._by_id[proposal.id] = proposal

    def get(self, proposal_id: str) -> ProposedPlaybook | None:
        return self._by_id.get(proposal_id)

    def list(self, status: ProposedPlaybookStatus | None = None) -> list[ProposedPlaybook]:
        items = list(self._by_id.values())
        if status is not None:
            items = [p for p in items if p.status == status]
        return items

    def set_status(
        self, proposal_id: str, status: ProposedPlaybookStatus, decided_by: str
    ) -> ProposedPlaybook | None:
        cur = self._by_id.get(proposal_id)
        if cur is None:
            return None
        updated = cur.model_copy(update={"status": status, "decided_by": decided_by})
        self._by_id[proposal_id] = updated
        return updated

    def clear(self) -> None:
        self._by_id.clear()


class PostgresProposedPlaybookStore:
    """ProposedPlaybook store backed by Postgres. `status` is a promoted typed
    column kept consistent with the JSONB payload by doing every status change
    as a read-modify-write within one transaction — never two separate
    statements that could disagree (mirrors PostgresAuthorDecisionStore)."""

    def __init__(self, engine) -> None:
        self._engine = engine

    def add(self, proposal: ProposedPlaybook) -> None:
        # InMemory's `add` overwrites by id in a dict — the faithful Postgres
        # equivalent is replace-by-proposal_id within one transaction (there is
        # no unique constraint on proposal_id to upsert against, matching the
        # promoted-column-plus-index style of author_decisions/agent_runs).
        with self._engine.begin() as conn:
            conn.execute(
                proposed_playbooks.delete().where(proposed_playbooks.c.proposal_id == proposal.id)
            )
            conn.execute(
                proposed_playbooks.insert().values(
                    proposal_id=proposal.id,
                    status=proposal.status.value,
                    source_situation_id=proposal.source_situation_id,
                    ts=proposal.ts,
                    payload=to_payload(proposal),
                )
            )

    def get(self, proposal_id: str) -> ProposedPlaybook | None:
        stmt = select(proposed_playbooks.c.payload).where(
            proposed_playbooks.c.proposal_id == proposal_id
        )
        with self._engine.connect() as conn:
            row = conn.execute(stmt).first()
        return from_payload(row.payload, ProposedPlaybook) if row else None

    def list(self, status: ProposedPlaybookStatus | None = None) -> list[ProposedPlaybook]:
        stmt = select(proposed_playbooks.c.payload).order_by(proposed_playbooks.c.id)
        if status is not None:
            stmt = stmt.where(proposed_playbooks.c.status == status.value)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [from_payload(row.payload, ProposedPlaybook) for row in rows]

    def set_status(
        self, proposal_id: str, status: ProposedPlaybookStatus, decided_by: str
    ) -> ProposedPlaybook | None:
        with self._engine.begin() as conn:
            stmt = select(proposed_playbooks.c.id, proposed_playbooks.c.payload).where(
                proposed_playbooks.c.proposal_id == proposal_id
            )
            row = conn.execute(stmt).first()
            if row is None:
                return None
            current = from_payload(row.payload, ProposedPlaybook)
            updated = current.model_copy(update={"status": status, "decided_by": decided_by})
            conn.execute(
                proposed_playbooks.update()
                .where(proposed_playbooks.c.id == row.id)
                .values(status=status.value, payload=to_payload(updated))
            )
        return updated

    def clear(self) -> None:
        with self._engine.begin() as conn:
            conn.execute(proposed_playbooks.delete())
