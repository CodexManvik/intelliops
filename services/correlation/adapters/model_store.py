"""ModelStore: persist the trained correlator's serialized model artifact.

Like BaselineStore, this is best-effort — a trained model is a slowly-earned
statistic, and losing one flush just means the correlator re-fits from live data.
Persistence errors here are swallowed by the *caller* (the /retrain handler and
the boot reload), never raised, mirroring BaselineStore's posture.

The artifact is an opaque joblib blob (see TrainedCorrelator.serialize) keyed by
a logical name (e.g. "trained"). save() upserts on the name PK so the latest fit
replaces the previous one; load_latest() returns the bytes or None when absent."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from common.db import model_artifacts


class InMemoryModelStore:
    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    def save(self, name: str, blob: bytes) -> None:
        self._blobs[name] = bytes(blob)

    def load_latest(self, name: str) -> bytes | None:
        return self._blobs.get(name)


class PostgresModelStore:
    def __init__(self, engine) -> None:
        self._engine = engine

    def save(self, name: str, blob: bytes) -> None:
        now = datetime.now(UTC)
        stmt = pg_insert(model_artifacts).values(name=name, artifact=blob, updated_at=now)
        stmt = stmt.on_conflict_do_update(
            index_elements=[model_artifacts.c.name],
            set_={
                "artifact": stmt.excluded.artifact,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def load_latest(self, name: str) -> bytes | None:
        stmt = select(model_artifacts.c.artifact).where(model_artifacts.c.name == name)
        with self._engine.connect() as conn:
            row = conn.execute(stmt).first()
        return bytes(row.artifact) if row is not None else None
