from datetime import UTC, datetime, timedelta

from common.contracts import TelemetryEvent, TelemetryKind
from services.correlation.adapters.robust_correlator import RobustCorrelator


def _event(name="cpu", value=10.0, fp="fp", ts=None):
    return TelemetryEvent(
        source="prom",
        kind=TelemetryKind.METRIC,
        name=name,
        value=value,
        labels={},
        ts=ts or datetime(2026, 8, 13, 0, 0, 0, tzinfo=UTC),
        fingerprint=fp,
    )


def _feed_flat(c, name="cpu", value=10.0, n=40, hour=0):
    ts0 = datetime(2026, 8, 13, hour, 0, 0, tzinfo=UTC)
    for i in range(n):
        c.detect(_event(name=name, value=value, ts=ts0 + timedelta(seconds=i)))


def test_flat_metric_never_flags():
    """MAD == 0 (all-same values) must score 0.0, never inf/nan."""
    c = RobustCorrelator(z_threshold=3.0, warmup_samples=10)
    _feed_flat(c, value=10.0, n=40)
    score = c.detect(_event(value=10.0, ts=datetime(2026, 8, 13, 0, 1, 0, tzinfo=UTC)))
    assert score == 0.0
    # even a different value against a flat baseline must not blow up
    score2 = c.detect(_event(value=999.0, ts=datetime(2026, 8, 13, 0, 1, 1, tzinfo=UTC)))
    assert score2 == 0.0


def test_spike_after_stable_window_scores_high():
    c = RobustCorrelator(z_threshold=3.0, warmup_samples=30)
    ts0 = datetime(2026, 8, 13, 0, 0, 0, tzinfo=UTC)
    # jittered-but-tight baseline so MAD isn't zero
    values = [10.0, 10.1, 9.9, 10.2, 9.8] * 8  # 40 samples, all within [9.8, 10.2]
    for i, v in enumerate(values):
        c.detect(_event(value=v, ts=ts0 + timedelta(seconds=i)))
    spike_score = c.detect(_event(value=500.0, ts=ts0 + timedelta(seconds=100)))
    assert spike_score > 3.0


def test_robustness_second_spike_after_earlier_spike_still_scores_high():
    """The whole point vs plain z-score: one earlier spike must not desensitize
    detection of a second, same-size spike (a z-score baseline's variance would
    be inflated by the first spike, damping the second spike's z)."""
    c = RobustCorrelator(z_threshold=3.0, warmup_samples=30, window_size=128)
    ts0 = datetime(2026, 8, 13, 0, 0, 0, tzinfo=UTC)
    values = [10.0, 10.1, 9.9, 10.2, 9.8] * 8  # 40 stable samples
    for i, v in enumerate(values):
        c.detect(_event(value=v, ts=ts0 + timedelta(seconds=i)))

    first_spike_score = c.detect(_event(value=500.0, ts=ts0 + timedelta(seconds=100)))
    assert first_spike_score > 3.0

    # feed a few more stable samples after the spike (still within warmup window
    # size so the spike value remains in the deque)
    for i, v in enumerate(values[:10]):
        c.detect(_event(value=v, ts=ts0 + timedelta(seconds=200 + i)))

    second_spike_score = c.detect(_event(value=500.0, ts=ts0 + timedelta(seconds=300)))
    assert second_spike_score > 3.0
    # the two spike scores should be close (robust MAD doesn't get desensitized)
    assert abs(second_spike_score - first_spike_score) < 1.0


def test_hour_buckets_keep_independent_baselines():
    c = RobustCorrelator(z_threshold=3.0, warmup_samples=10)
    ts0 = datetime(2026, 8, 13, 0, 0, 0, tzinfo=UTC)
    ts5 = datetime(2026, 8, 13, 5, 0, 0, tzinfo=UTC)
    jitter_10 = [10.0, 10.1, 9.9, 10.2, 9.8] * 8  # 40 samples, bucket 0 baseline
    jitter_200 = [200.0, 200.1, 199.9, 200.2, 199.8] * 8  # 40 samples, bucket 5 baseline
    for i, v in enumerate(jitter_10):
        c.detect(_event(value=v, ts=ts0 + timedelta(seconds=i)))
    for i, v in enumerate(jitter_200):
        c.detect(_event(value=v, ts=ts5 + timedelta(seconds=i)))

    # a value near bucket-0's baseline, scored in bucket 0, should not flag
    score_b0 = c.detect(_event(value=10.0, ts=ts0 + timedelta(seconds=100)))
    assert score_b0 < 3.0

    # a value near bucket-5's baseline, scored in bucket 5, should not flag either
    score_b5 = c.detect(_event(value=200.0, ts=ts5 + timedelta(seconds=100)))
    assert score_b5 < 3.0

    # confirm the buckets are actually independent: a value drawn from bucket 0's
    # baseline (10) is a massive outlier when scored against bucket 5's baseline
    # (200), proving the two buckets do not share state.
    outlier_in_b5 = c.detect(_event(value=10.0, ts=ts5 + timedelta(seconds=200)))
    assert outlier_in_b5 > 3.0


def test_snapshot_load_round_trips_and_reproduces_identical_scores():
    c = RobustCorrelator(z_threshold=3.0, warmup_samples=30)
    ts0 = datetime(2026, 8, 13, 0, 0, 0, tzinfo=UTC)
    values = [10.0, 10.1, 9.9, 10.2, 9.8] * 8
    for i, v in enumerate(values):
        c.detect(_event(value=v, ts=ts0 + timedelta(seconds=i)))

    snap = c.snapshot()
    assert snap  # non-empty
    for row in snap:
        assert set(row.keys()) == {"metric_name", "bucket", "n", "window"}

    c2 = RobustCorrelator(z_threshold=3.0, warmup_samples=30)
    c2.load(snap)

    probe_ts = ts0 + timedelta(seconds=100)
    score_original = c.detect(_event(value=500.0, ts=probe_ts))
    score_reloaded = c2.detect(_event(value=500.0, ts=probe_ts))
    assert score_original == score_reloaded


def test_reset_factory_compat_extra_kwargs_default():
    """engine.py's reset factory calls type(c)(z_threshold=..., warmup_samples=...)
    with ONLY those two kwargs — seasonal_buckets/window_size must default."""
    c = RobustCorrelator(z_threshold=3.0, warmup_samples=30, seasonal_buckets=12, window_size=64)
    c2 = type(c)(z_threshold=3.0, warmup_samples=30)  # must not raise TypeError
    assert c2._z_threshold == 3.0
    assert c2._warmup_samples == 30


def test_warmup_gate_scores_zero():
    c = RobustCorrelator(z_threshold=3.0, warmup_samples=30)
    ts0 = datetime(2026, 8, 13, 0, 0, 0, tzinfo=UTC)
    for i in range(10):  # fewer than warmup_samples
        c.detect(_event(value=10.0, ts=ts0 + timedelta(seconds=i)))
    assert c.detect(_event(value=1000.0, ts=ts0 + timedelta(seconds=50))) == 0.0
