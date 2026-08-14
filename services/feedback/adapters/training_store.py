"""TrainingStore implementations: in-memory (tests) and append-only JSONL file.

The training store is the closed-loop seam: feedback appends labeled outcomes;
correlation reads them at retrain time. Postgres is a deferred adapter."""

from __future__ import annotations

import os

from common.contracts import TrainingRecord


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
