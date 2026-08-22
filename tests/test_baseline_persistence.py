"""Postgres baseline-store adapter + the headline restart-survival test.

The z-score baseline is a slowly-settling statistic. Persisting it means a
correlation-service restart mid-run reloads a warm detector rather than starting
cold — so a genuine outlier fires IMMEDIATELY after restart, with no warm-up
blackout. These run against a real throwaway Postgres (the `postgres` marker +
`clean_db`).
"""

from datetime import UTC, datetime

import pytest

from common.contracts import TelemetryEvent, TelemetryKind
from services.correlation.adapters.baseline_store import PostgresBaselineStore
from services.correlation.adapters.river_correlator import RiverCorrelator
from services.correlation.engine import CorrelationEngine


def _event(value, ts_sec=0):
    return TelemetryEvent(
        source="prom",
        kind=TelemetryKind.METRIC,
        name="cpu_usage",
        value=value,
        labels={},
        ts=datetime(2026, 8, 20, 0, 0, ts_sec, tzinfo=UTC),
        fingerprint="cpu_usage",
    )


@pytest.mark.postgres
def test_save_load_all_roundtrip(clean_db):
    store = PostgresBaselineStore(clean_db)
    rows = [{"metric_name": "cpu_usage", "n": 60.0, "mean": 52.0, "variance": 4.0, "count": 60}]
    store.save(rows)
    got = {r["metric_name"]: r for r in store.load_all()}
    assert "cpu_usage" in got
    # variance and count survive the round-trip
    assert got["cpu_usage"]["variance"] == 4.0
    assert got["cpu_usage"]["count"] == 60
    assert got["cpu_usage"]["mean"] == 52.0
    assert got["cpu_usage"]["n"] == 60.0


@pytest.mark.postgres
def test_baseline_survives_restart_no_warmup_blackout(clean_db):
    """The headline test: a settled baseline persisted to a REAL container is
    reloaded into a FRESH engine, and a genuine outlier fires immediately —
    proving the restart skipped the cold-start warm-up blackout entirely."""
    store = PostgresBaselineStore(clean_db)

    # 1. Settle a baseline well past warm-up (warmup_samples default is 50).
    engine = CorrelationEngine(RiverCorrelator(z_threshold=3.0, warmup_samples=50))
    for v in [50.0 + (i % 5) for i in range(60)]:  # ~60 events, values ~50-54
        engine.add(_event(v))

    # 2. Persist its snapshot to the real Postgres container.
    store.save(engine.snapshot())

    # 3. Build a FRESH engine (simulates a restart) and reload from durable state.
    fresh = CorrelationEngine(RiverCorrelator(z_threshold=3.0, warmup_samples=50))
    fresh.load(store.load_all())

    # 4. A genuine outlier fires IMMEDIATELY — no warm-up blackout after reload.
    #    detect() mutates the baseline, so score the normal value BEFORE folding
    #    any outlier in, keeping each assertion on the reloaded (unpolluted) state.
    correlator = fresh._correlator
    assert not correlator.is_anomaly(_event(51.0))  # a normal value stays quiet
    assert correlator.detect(_event(500.0)) > 3.0  # the outlier's z clears threshold
    assert correlator.is_anomaly(_event(500.0))  # ...and reads as an anomaly
