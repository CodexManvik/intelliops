"""ApprovalStore implementations: in-memory (tests) and Postgres.

Pending HITL approvals are live runtime state — a plain dict today, lost on
restart. Postgres makes them durable so a human's in-flight decision survives a
governance restart mid-incident. Errors PROPAGATE (a lost approval write is a
correctness failure — same posture as the other stores, contrast the best-effort
baseline snapshot)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from common.contracts import ApprovalRequest
from common.db import approvals, from_payload, to_payload


class InMemoryApprovalStore:
    def __init__(self) -> None:
        self._by_id: dict[str, ApprovalRequest] = {}

    def create(self, request: ApprovalRequest) -> ApprovalRequest:
        self._by_id[request.id] = request
        return request

    def get(self, approval_id: str) -> ApprovalRequest | None:
        return self._by_id.get(approval_id)

    def decide(self, approval_id: str, status: str, decided_by: str) -> ApprovalRequest | None:
        req = self._by_id.get(approval_id)
        if req is None:
            return None
        updated = req.model_copy(update={"status": status, "decided_by": decided_by})
        self._by_id[approval_id] = updated
        return updated

    def list_pending(self) -> list[ApprovalRequest]:
        return [a for a in self._by_id.values() if a.status == "pending"]


class PostgresApprovalStore:
    def __init__(self, engine) -> None:
        self._engine = engine

    @staticmethod
    def _values(request: ApprovalRequest) -> dict:
        return {
            "id": request.id,
            "situation_id": request.situation_id,
            "playbook_id": request.playbook_id,
            "status": request.status,
            "payload": to_payload(request),
            "updated_at": datetime.now(UTC),
        }

    def create(self, request: ApprovalRequest) -> ApprovalRequest:
        stmt = pg_insert(approvals).values(**self._values(request))
        stmt = stmt.on_conflict_do_update(
            index_elements=[approvals.c.id],
            set_={
                "situation_id": stmt.excluded.situation_id,
                "playbook_id": stmt.excluded.playbook_id,
                "status": stmt.excluded.status,
                "payload": stmt.excluded.payload,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)
        return request

    def get(self, approval_id: str) -> ApprovalRequest | None:
        stmt = select(approvals.c.payload).where(approvals.c.id == approval_id)
        with self._engine.connect() as conn:
            row = conn.execute(stmt).first()
        return from_payload(row.payload, ApprovalRequest) if row else None

    def decide(self, approval_id: str, status: str, decided_by: str) -> ApprovalRequest | None:
        current = self.get(approval_id)
        if current is None:
            return None
        updated = current.model_copy(update={"status": status, "decided_by": decided_by})
        self.create(updated)  # upsert
        return updated

    def list_pending(self) -> list[ApprovalRequest]:
        stmt = (
            select(approvals.c.payload)
            .where(approvals.c.status == "pending")
            .order_by(approvals.c.updated_at)
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [from_payload(r.payload, ApprovalRequest) for r in rows]
