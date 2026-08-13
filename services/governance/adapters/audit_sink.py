"""AuditSink implementations: in-memory (tests) and append-only JSONL file.

The audit log is the immutable compliance backbone (NIST AI RMF). Postgres is a
deferred adapter; the file sink is the running-service default this slice.
"""

from __future__ import annotations

import os

from common.contracts import AuditRecord


class InMemoryAuditSink:
    def __init__(self) -> None:
        self._records: list[AuditRecord] = []

    def write(self, record: AuditRecord) -> None:
        self._records.append(record)

    def records(self) -> list[AuditRecord]:
        return list(self._records)


class FileAuditSink:
    def __init__(self, path: str) -> None:
        self._path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def write(self, record: AuditRecord) -> None:
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(record.model_dump_json() + "\n")

    def records(self) -> list[AuditRecord]:
        if not os.path.exists(self._path):
            return []
        out: list[AuditRecord] = []
        with open(self._path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    out.append(AuditRecord.model_validate_json(line))
        return out
