"""BaselineStore: persist the correlator's per-metric z-score baseline.

Unlike the audit/approval stores, a baseline snapshot is best-effort — it is a
slowly-settling statistic, and losing one flush is recoverable. Persistence
errors here are logged and swallowed by the caller (the flusher), never raised
(contrast the propagate-loudly audit sink)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from common.db import correlation_baseline


class InMemoryBaselineStore:
    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}

    def save(self, rows: list[dict]) -> None:
        for r in rows:
            self._rows[r["metric_name"]] = dict(r)

    def load_all(self) -> list[dict]:
        return [dict(r) for r in self._rows.values()]


class PostgresBaselineStore:
    def __init__(self, engine) -> None:
        self._engine = engine

    def save(self, rows: list[dict]) -> None:
        if not rows:
            return
        now = datetime.now(UTC)
        with self._engine.begin() as conn:
            for r in rows:
                stmt = pg_insert(correlation_baseline).values(
                    metric_name=r["metric_name"],
                    n=r["n"],
                    mean=r["mean"],
                    variance=r["variance"],
                    count=r["count"],
                    updated_at=now,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=[correlation_baseline.c.metric_name],
                    set_={
                        "n": stmt.excluded.n,
                        "mean": stmt.excluded.mean,
                        "variance": stmt.excluded.variance,
                        "count": stmt.excluded.count,
                        "updated_at": stmt.excluded.updated_at,
                    },
                )
                conn.execute(stmt)

    def load_all(self) -> list[dict]:
        cols = correlation_baseline.c
        stmt = select(cols.metric_name, cols.n, cols.mean, cols.variance, cols.count)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [
            {
                "metric_name": r.metric_name,
                "n": r.n,
                "mean": r.mean,
                "variance": r.variance,
                "count": r.count,
            }
            for r in rows
        ]
