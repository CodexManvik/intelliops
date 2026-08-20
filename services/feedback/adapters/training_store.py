"""TrainingStore implementations: in-memory (tests), append-only JSONL file, and Postgres.

The training store is the closed-loop seam: feedback appends labeled outcomes;
correlation reads them at retrain time."""

from __future__ import annotations

import os

from sqlalchemy import select

from common.contracts import TrainingRecord
from common.db import from_payload, to_payload, training_records


class InMemoryTrainingStore:
    def __init__(self) -> None:
        self._records: list[TrainingRecord] = []

    def append(self, record: TrainingRecord) -> None:
        self._records.append(record)

    def read_all(self) -> list[TrainingRecord]:
        return list(self._records)


class FileTrainingStore:
    def __init__(self, path: str) -> None:
        self._path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def append(self, record: TrainingRecord) -> None:
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(record.model_dump_json() + "\n")

    def read_all(self) -> list[TrainingRecord]:
        if not os.path.exists(self._path):
            return []
        out: list[TrainingRecord] = []
        with open(self._path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    out.append(TrainingRecord.model_validate_json(line))
        return out


class PostgresTrainingStore:
    """TrainingRecord store backed by Postgres. Errors propagate — a lost
    training record is a real error, not something to swallow silently."""

    def __init__(self, engine) -> None:
        self._engine = engine

    def append(self, record: TrainingRecord) -> None:
        result = record.result.value if hasattr(record.result, "value") else str(record.result)
        with self._engine.begin() as conn:
            conn.execute(training_records.insert().values(
                situation_id=record.situation_id, signature=record.signature,
                playbook_id=record.playbook_id, result=result, worked=record.worked,
                ts=record.ts, payload=to_payload(record)))

    def read_all(self) -> list[TrainingRecord]:
        stmt = select(training_records.c.payload).order_by(training_records.c.id)
        with self._engine.connect() as conn:
            return [from_payload(row.payload, TrainingRecord) for row in conn.execute(stmt)]
