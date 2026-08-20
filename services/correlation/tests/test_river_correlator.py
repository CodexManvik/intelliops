import random
from datetime import UTC, datetime

import pytest

from common.contracts import Situation, SituationStatus, TelemetryEvent, TelemetryKind
from services.correlation.adapters.river_correlator import RiverCorrelator


def _event(name="cpu", value=10.0, fp="fp", ts_sec=0):
    return TelemetryEvent(
        source="prom",
        kind=TelemetryKind.METRIC,
        name=name,
        value=value,
        labels={},
        ts=datetime(2026, 8, 13, 0, 0, ts_sec, tzinfo=UTC),
        fingerprint=fp,
    )


def _feed_baseline(correlator, n=200, mean=10.0, sigma=1.0, seed=42):
    """Feed a baseline with realistic jitter.

    A dead-flat baseline (every value identical) drives std dev to ~0, which
    makes the z-score explode on ANY later deviation. Real metrics vary, so the
    baseline must too. Seeded for determinism.
    """
    rng = random.Random(seed)
    for _ in range(n):
        correlator.detect(_event(value=round(rng.gauss(mean, sigma), 3)))


def test_detect_scores_zero_for_none_value():
    c = RiverCorrelator()
    assert c.detect(_event(value=None)) == 0.0


def test_detect_flags_outlier_after_baseline():
    c = RiverCorrelator(z_threshold=3.0)
    _feed_baseline(c)  # jittered baseline around 10
    normal_score = c.detect(_event(value=10.2))
    outlier_score = c.detect(_event(value=100.0))
    assert normal_score < 3.0
    assert outlier_score > 3.0


def test_is_anomaly_matches_threshold():
    c = RiverCorrelator(z_threshold=3.0)
    _feed_baseline(c)
    assert c.is_anomaly(_event(value=100.0)) is True
    # a value near the mean is not anomalous
    assert c.is_anomaly(_event(value=10.1)) is False


def test_correlate_builds_one_situation_with_stable_signature():
    c = RiverCorrelator()
    events = [_event(fp="a", ts_sec=1), _event(fp="b", ts_sec=5), _event(fp="c", ts_sec=3)]
    sit = c.correlate(events, severity="high")
    assert isinstance(sit, Situation)
    assert sit.status == SituationStatus.DETECTED
    assert len(sit.member_events) == 3
    assert sit.severity == "high"
    assert sit.first_seen.second == 1
    assert sit.last_seen.second == 5
    # signature is order-independent and stable
    sit2 = c.correlate(list(reversed(events)), severity="high")
    assert sit.signature == sit2.signature
    assert sit.id == "sit-" + sit.signature


def test_correlate_raises_on_empty():
    with pytest.raises(ValueError):
        RiverCorrelator().correlate([], severity="low")


def test_severity_band():
    c = RiverCorrelator()
    assert c._severity_band(9.0) == "high"
    assert c._severity_band(6.0) == "medium"
    assert c._severity_band(2.0) == "low"


def test_retrain_is_noop():
    RiverCorrelator().retrain([])  # must not raise


def test_detect_suppresses_anomalies_during_warmup():
    """A spike seen before the metric's baseline is warm must NOT flag.

    river.stats.Var is unstable during warm-up (the running variance is tiny
    after only a handful of samples), so a normal-looking value can score a
    huge z. Until the metric has seen `warmup_samples` observations, detect
    returns 0.0 regardless of value — a cold-started service must not emit
    spurious anomalies. See the cold-start note in the Slice-1 review.
    """
    c = RiverCorrelator(z_threshold=3.0, warmup_samples=50)
    # Feed a few jittered baseline samples, then inject a large spike while
    # still inside the warm-up window.
    rng = random.Random(42)
    for _ in range(10):
        c.detect(_event(value=round(rng.gauss(10.0, 1.0), 3)))
    assert c.detect(_event(value=1000.0)) == 0.0
    assert c.is_anomaly(_event(value=1000.0)) is False


def test_detect_flags_outlier_once_warm():
    """After warmup_samples observations, a genuine outlier scores normally."""
    c = RiverCorrelator(z_threshold=3.0, warmup_samples=50)
    _feed_baseline(c, n=60)  # > warmup_samples jittered baseline samples
    assert c.is_anomaly(_event(value=100.0)) is True
    assert c.is_anomaly(_event(value=10.1)) is False


def test_warmup_baseline_keeps_learning():
    """Values fed during warm-up still train the baseline, so it's ready the
    moment warm-up ends (the metric isn't scored, but it is learned)."""
    c = RiverCorrelator(z_threshold=3.0, warmup_samples=50)
    rng = random.Random(7)
    for _ in range(50):  # exactly warmup_samples jittered values around 10
        c.detect(_event(value=round(rng.gauss(10.0, 1.0), 3)))
    # The 51st observation is scored against a baseline built from all 50.
    near_mean = c.detect(_event(value=10.0))
    outlier = c.detect(_event(value=100.0))
    assert near_mean < 3.0
    assert outlier > 3.0


def test_warmup_is_per_metric():
    """Each metric name warms up independently."""
    c = RiverCorrelator(z_threshold=3.0, warmup_samples=50)
    _feed_baseline(c, n=60)  # warms only "cpu"
    # "cpu" is warm and flags; a fresh metric "mem" is still in warm-up.
    assert c.is_anomaly(_event(name="cpu", value=100.0)) is True
    assert c.detect(_event(name="mem", value=1000.0)) == 0.0
