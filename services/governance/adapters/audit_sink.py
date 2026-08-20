"""AuditSink implementations: in-memory (tests), append-only JSONL file, and Postgres.

The audit log is the immutable compliance backbone (NIST AI RMF).
"""

from __future__ import annotations

import os

from sqlalchemy import select

from common.contracts import AuditRecord
from common.db import audit_records, from_payload, to_payload


class InMemoryAuditSink:
    def __init__(self) -> None:
        self._records: list[AuditRecord] = []

    def write(self, record: AuditRecord) -> None:
        self._records.append(record)

    def records(self, correlation_id: str | None = None) -> list[AuditRecord]:
        return [r for r in self._records if correlation_id is None or r.correlation_id == correlation_id]


class FileAuditSink:
    def __init__(self, path: str) -> None:
        self._path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def write(self, record: AuditRecord) -> None:
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(record.model_dump_json() + "\n")

    def records(self, correlation_id: str | None = None) -> list[AuditRecord]:
        if not os.path.exists(self._path):
            return []
        out: list[AuditRecord] = []
        with open(self._path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    out.append(AuditRecord.model_validate_json(line))
        return [r for r in out if correlation_id is None or r.correlation_id == correlation_id]


class PostgresAuditSink:
    """AuditSink backed by Postgres. Errors propagate — a failed audit write is a
    compliance failure and must be visible, not silently swallowed."""

    def __init__(self, engine) -> None:
        self._engine = engine

    def write(self, record: AuditRecord) -> None:
        with self._engine.begin() as conn:
            conn.execute(audit_records.insert().values(
                correlation_id=record.correlation_id, actor=record.actor,
                action=record.action, resource=record.resource,
                decision=record.decision, ts=record.ts, payload=to_payload(record)))

    def records(self, correlation_id: str | None = None) -> list[AuditRecord]:
        stmt = select(audit_records.c.payload)
        if correlation_id is not None:
            stmt = stmt.where(audit_records.c.correlation_id == correlation_id)
        stmt = stmt.order_by(audit_records.c.id)
        with self._engine.connect() as conn:
            return [from_payload(row.payload, AuditRecord) for row in conn.execute(stmt)]
