# tests/test_baseline_codec.py
"""The z-score baseline must survive a snapshot→reload with identical behavior.

This pins river's _from_state contract: Var._from_state(n, m, sig) takes the
VARIANCE as sig, not the raw _S. Storing _S diverges (verified during design).
"""

from river import stats


def _snap(mean: stats.Mean, var: stats.Var) -> dict:
    return {"n": var.mean.n, "mean": mean.get(), "variance": var.get()}


def _load(row: dict) -> tuple[stats.Mean, stats.Var]:
    n = int(row["n"])
    m = stats.Mean._from_state(n, row["mean"])
    v = stats.Var._from_state(n, row["mean"], row["variance"], ddof=1)
    return m, v


def test_snapshot_reload_preserves_zscore():
    orig_m, orig_v = stats.Mean(), stats.Var()
    for x in [10, 12, 11, 13, 9, 14, 8, 11, 12, 10]:
        orig_m.update(x)
        orig_v.update(x)

    new_m, new_v = _load(_snap(orig_m, orig_v))

    # variance reconstructs exactly
    assert abs(new_v.get() - orig_v.get()) < 1e-9
    # the next value's z-score is identical
    test_val = 25.0
    z_orig = abs(test_val - orig_m.get()) / (orig_v.get() ** 0.5)
    z_new = abs(test_val - new_m.get()) / (new_v.get() ** 0.5)
    assert abs(z_orig - z_new) < 1e-9
    # continuing to update stays identical (state, not just a snapshot)
    orig_v.update(test_val)
    new_v.update(test_val)
    assert abs(orig_v.get() - new_v.get()) < 1e-9


def test_correlator_snapshot_reload_no_warmup_blackout():
    from datetime import UTC, datetime

    from common.contracts import TelemetryEvent, TelemetryKind
    from services.correlation.adapters.river_correlator import RiverCorrelator

    def ev(v):
        # TelemetryEvent requires source + kind (see any correlation test's helper)
        return TelemetryEvent(
            source="prom",
            kind=TelemetryKind.METRIC,
            name="cpu_usage",
            value=v,
            labels={},
            ts=datetime(2026, 8, 20, tzinfo=UTC),
            fingerprint="cpu_usage",
        )

    orig = RiverCorrelator(z_threshold=3.0, warmup_samples=50)
    for v in [50.0 + (i % 5) for i in range(60)]:  # settle past warm-up
        orig.detect(ev(v))

    rows = orig.snapshot()
    assert any(r["metric_name"] == "cpu_usage" for r in rows)

    fresh = RiverCorrelator(z_threshold=3.0, warmup_samples=50)
    fresh.load(rows)
    # a genuine outlier fires immediately — NO warm-up blackout after reload
    assert fresh.is_anomaly(ev(500.0))
    # and a normal value does not
    assert not fresh.is_anomaly(ev(51.0))
