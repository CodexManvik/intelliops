import random
from datetime import UTC, datetime

from common.contracts import TelemetryEvent, TelemetryKind
from services.correlation.adapters.river_correlator import RiverCorrelator
from services.correlation.adapters.robust_correlator import RobustCorrelator
from services.correlation.adapters.trained_correlator import TrainedCorrelator
from services.correlation.detection_policy import DetectionPolicy, classify


def _ev(name, value):
    return TelemetryEvent(
        source="p",
        kind=TelemetryKind.METRIC,
        name=name,
        value=value,
        labels={},
        ts=datetime(2026, 9, 6, 12, tzinfo=UTC),
        fingerprint="fp",
    )


def test_classify_kinds():
    assert classify("meridian_error_rate") == "ratio"
    assert classify("latency_p99_ms") == "latency"
    assert classify("cpu_usage") == "saturation"
    assert classify("saturation") == "saturation"
    assert classify("disk_usage_percent") == "saturation"
    assert classify("request_rate") == "default"
    assert classify("queue_depth") == "default"


def test_disabled_policy_is_pure_zscore():
    p = DetectionPolicy(enabled=False)
    # exactly detect() > z_threshold for every kind, value irrelevant
    assert p.is_anomaly(_ev("meridian_error_rate", 0.001), score=5.0, z_threshold=3.0) is True
    assert p.is_anomaly(_ev("meridian_error_rate", 0.99), score=1.0, z_threshold=3.0) is False
    assert p.is_anomaly(_ev("cpu_usage", 10.0), score=4.0, z_threshold=3.0) is True


def test_ratio_fires_on_absolute_even_with_low_zscore():
    p = DetectionPolicy(enabled=True)  # default thresholds
    # error rate 5% with a marginal z-score (noisy baseline) STILL fires
    assert p.is_anomaly(_ev("meridian_error_rate", 0.05), score=1.0, z_threshold=3.0) is True
    # tiny wiggle below 2% does NOT fire even with a high z-score
    assert p.is_anomaly(_ev("meridian_error_rate", 0.005), score=9.0, z_threshold=3.0) is False


def test_saturation_scale_handling():
    p = DetectionPolicy(enabled=True)
    assert p.is_anomaly(_ev("cpu_usage", 95.0), score=0.0, z_threshold=3.0) is True  # 0..100 > 90
    assert p.is_anomaly(_ev("cpu_usage", 40.0), score=9.0, z_threshold=3.0) is False
    assert p.is_anomaly(_ev("saturation", 0.9), score=0.0, z_threshold=3.0) is True  # 0..1 > 0.80
    assert p.is_anomaly(_ev("saturation", 0.2), score=9.0, z_threshold=3.0) is False
    assert p.is_anomaly(_ev("disk_usage_percent", 95.0), score=0.0, z_threshold=3.0) is True


def test_latency_statistical_or_ceiling():
    p = DetectionPolicy(enabled=True)
    assert (
        p.is_anomaly(_ev("latency_p99_ms", 120.0), score=5.0, z_threshold=3.0) is True
    )  # seasonal score fires
    assert (
        p.is_anomaly(_ev("latency_p99_ms", 700.0), score=1.0, z_threshold=3.0) is True
    )  # ceiling fallback
    assert p.is_anomaly(_ev("latency_p99_ms", 120.0), score=1.0, z_threshold=3.0) is False  # normal


def test_default_kind_unchanged():
    p = DetectionPolicy(enabled=True)
    assert p.is_anomaly(_ev("request_rate", 999.0), score=4.0, z_threshold=3.0) is True  # z-score
    assert p.is_anomaly(_ev("request_rate", 999.0), score=1.0, z_threshold=3.0) is False


# --- BaseCorrelator wiring (Task 2): all three correlators route through _policy ---


def _feed_baseline(correlator, name="cpu", n=200, mean=10.0, sigma=1.0, seed=42, ts0=None):
    """Feed a jittered baseline so detect() returns a real (non-warmup-gated) score.

    Mirrors test_river_correlator.py's _feed_baseline: a dead-flat baseline drives
    std/MAD to 0, which either explodes or degenerately zeroes later z-scores.
    """
    rng = random.Random(seed)
    ts0 = ts0 or datetime(2026, 9, 6, 0, 0, 0, tzinfo=UTC)
    for i in range(n):
        correlator.detect(
            TelemetryEvent(
                source="p",
                kind=TelemetryKind.METRIC,
                name=name,
                value=round(rng.gauss(mean, sigma), 3),
                labels={},
                ts=ts0,
                fingerprint="fp",
            )
        )


def test_correlator_default_is_disabled_policy():
    """A correlator built with no detection_policy carries a disabled DetectionPolicy,
    so is_anomaly is byte-identical to the pre-policy pure z-score decision."""
    for correlator in (RiverCorrelator(z_threshold=3.0), RobustCorrelator(z_threshold=3.0)):
        assert correlator._policy is not None
        assert correlator._policy._enabled is False
        _feed_baseline(correlator, n=200)
        outlier = _ev("cpu", 100.0)
        normal = _ev("cpu", 10.1)
        assert correlator.is_anomaly(outlier) == (correlator.detect(outlier) > 3.0)
        assert correlator.is_anomaly(normal) == (correlator.detect(normal) > 3.0)


def test_correlator_with_enabled_policy_uses_thresholds():
    """An enabled policy flags a ratio metric by absolute value, regardless of the
    z-score warm-up state (the underlying correlator is stone cold: score is 0.0,
    which would never cross a z_threshold on its own)."""
    for Cls in (RiverCorrelator, RobustCorrelator, TrainedCorrelator):
        c = Cls(z_threshold=3.0, detection_policy=DetectionPolicy(enabled=True))
        cold_event = _ev("meridian_error_rate", 0.05)  # > 0.02 ratio threshold
        assert c.detect(cold_event) == 0.0  # still cold: confirms this isn't a z-score hit
        assert c.is_anomaly(_ev("meridian_error_rate", 0.05)) is True
        assert c.is_anomaly(_ev("meridian_error_rate", 0.001)) is False


def test_reset_factory_signature_accepts_policy():
    """The engine's reset factory (Task 3/4) will call
    type(c)(z_threshold=, warmup_samples=, detection_policy=) — every correlator
    must accept and forward it to BaseCorrelator."""
    for Cls in (RiverCorrelator, RobustCorrelator, TrainedCorrelator):
        c = Cls(z_threshold=3.0, warmup_samples=50, detection_policy=DetectionPolicy(enabled=True))
        assert c._policy is not None
        assert c._policy._enabled is True


def test_trained_correlator_inner_robust_does_not_receive_policy():
    """TrainedCorrelator forwards detection_policy only to its OWN (outer) _policy;
    the composed inner RobustCorrelator is a plain scorer with a disabled policy,
    since the engine decides anomaly-ness from the outer correlator alone."""
    c = TrainedCorrelator(detection_policy=DetectionPolicy(enabled=True))
    assert c._policy._enabled is True
    assert c._robust._policy._enabled is False


def test_is_anomaly_scored_avoids_second_detect_call():
    """is_anomaly_scored(event, score) applies the policy to an already-computed
    score without calling detect() again (detect() mutates the baseline, so a
    second call would double-count the observation)."""
    c = RiverCorrelator(z_threshold=3.0, detection_policy=DetectionPolicy(enabled=True))
    ratio_event = _ev("meridian_error_rate", 0.05)
    assert c.is_anomaly_scored(ratio_event, score=0.0) is True  # ratio fires on value alone
    assert c.is_anomaly_scored(_ev("meridian_error_rate", 0.001), score=99.0) is False
