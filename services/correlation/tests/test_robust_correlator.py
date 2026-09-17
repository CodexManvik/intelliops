import math
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


def test_flat_metric_unchanged_does_not_flag():
    """MAD == 0 with an UNCHANGED value must score 0.0, never inf/nan."""
    c = RobustCorrelator(z_threshold=3.0, warmup_samples=10)
    _feed_flat(c, value=10.0, n=40)
    score = c.detect(_event(value=10.0, ts=datetime(2026, 8, 13, 0, 1, 0, tzinfo=UTC)))
    assert score == 0.0


def test_step_off_a_flat_baseline_is_detected():
    """Regression: this correlator was permanently blind to the most obvious
    anomaly there is.

    MAD == 0 means every sample in the window is identical, so the old code
    returned 0.0 for ANY value - a metric pinned at 0.4 that jumped to 46.4
    scored 0.00 forever while RiverCorrelator scored 10.91. It was measured
    against the real tls_handshake_failures series, whose baseline is exactly
    constant, which meant the whole escalation path could never fire under
    `robust`. The previous version of this test asserted the broken behaviour,
    which is why nothing caught it.

    The inf/nan guard that test was really protecting is still asserted below.
    """
    c = RobustCorrelator(z_threshold=3.0, warmup_samples=10)
    _feed_flat(c, value=10.0, n=40)
    score = c.detect(_event(value=999.0, ts=datetime(2026, 8, 13, 0, 1, 1, tzinfo=UTC)))
    assert score > 3.0, "a step off a perfectly flat baseline must be an anomaly"
    assert math.isfinite(score), "must never be inf/nan"


def test_small_real_variation_off_a_flat_baseline_does_not_flag():
    """Regression: a constant series that starts reporting real variation is a
    measurement getting better, not an incident.

    The simulated workloads pinned every healthy metric to a constant, so their
    learned windows had MAD == 0. When they began emitting bounded idle jitter
    the flat-baseline rule scored an 18.0 -> 17.34 tick at 6.0 and opened four
    "database connection-pool exhaustion" incidents at once, none of which had
    happened. A zero-MAD window has no spread to normalise by, so the move has
    to be large relative to the baseline itself before it counts.
    """
    c = RobustCorrelator(z_threshold=3.0, warmup_samples=10)
    _feed_flat(c, value=18.0, n=40)
    assert c.detect(_event(value=17.34, ts=datetime(2026, 8, 13, 0, 1, 3, tzinfo=UTC))) == 0.0
    assert c.detect(_event(value=20.31, ts=datetime(2026, 8, 13, 0, 1, 4, tzinfo=UTC))) == 0.0


def test_crash_off_a_flat_baseline_still_flags():
    """The floor above must not blunt the case the flat-baseline rule exists
    for: service_up is exactly 1.0 until the process dies."""
    c = RobustCorrelator(z_threshold=3.0, warmup_samples=10)
    _feed_flat(c, name="service_up", value=1.0, n=40)
    score = c.detect(
        _event(name="service_up", value=0.0, ts=datetime(2026, 8, 13, 0, 1, 5, tzinfo=UTC))
    )
    assert score > 3.0, "a service going down must still be an anomaly"


def test_tiny_float_noise_on_a_flat_baseline_does_not_flag():
    """A re-published identical value must not read as an anomaly."""
    c = RobustCorrelator(z_threshold=3.0, warmup_samples=10)
    _feed_flat(c, value=10.0, n=40)
    score = c.detect(_event(value=10.0 + 1e-12, ts=datetime(2026, 8, 13, 0, 1, 2, tzinfo=UTC)))
    assert score == 0.0


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


def test_baseline_snapshot_is_exposed_so_remediation_can_be_verified():
    """The engine only attaches Situation.baseline if the correlator offers one.

    Only RiverCorrelator implemented baseline_snapshot, while values-live.yaml
    runs CORRELATOR_KIND=robust - so in the live posture every Situation carried
    baseline=None and services/action/verify.py could not confirm recovery for
    any score-only metric. On a real cluster that turned every successful
    restart-pod remediation into a reported `rolled_back`.
    """
    c = RobustCorrelator(window_size=50, warmup_samples=3)
    ts0 = datetime(2026, 8, 13, 0, 0, 0, tzinfo=UTC)
    for i in range(20):
        c.detect(_event(name="cpu_usage", value=10.0 + (i % 2), ts=ts0 + timedelta(seconds=i)))

    snap = c.baseline_snapshot()
    assert "cpu_usage" in snap
    assert set(snap["cpu_usage"]) == {"mean", "std"}
    assert 10.0 <= snap["cpu_usage"]["mean"] <= 11.0
    assert snap["cpu_usage"]["std"] > 0.0


def test_baseline_snapshot_of_a_flat_metric_reports_zero_std():
    """service_up never moves, so its spread really is 0.

    That is a meaningful baseline, not a missing one - verify.py relies on it to
    decide the metric is recovered only when it returns to exactly that value.
    """
    c = RobustCorrelator(window_size=50, warmup_samples=3)
    _feed_flat(c, name="service_up", value=1.0, n=20)

    snap = c.baseline_snapshot()
    assert snap["service_up"]["mean"] == 1.0
    assert snap["service_up"]["std"] == 0.0


def test_baseline_snapshot_pools_across_seasonal_buckets():
    c = RobustCorrelator(window_size=50, warmup_samples=3)
    _feed_flat(c, name="cpu_usage", value=10.0, n=10, hour=0)
    _feed_flat(c, name="cpu_usage", value=10.0, n=10, hour=5)
    assert c.baseline_snapshot()["cpu_usage"]["mean"] == 10.0


def test_baseline_snapshot_is_empty_before_any_observation():
    assert RobustCorrelator(window_size=50, warmup_samples=3).baseline_snapshot() == {}
