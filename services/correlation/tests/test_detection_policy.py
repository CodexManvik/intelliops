from datetime import UTC, datetime

from common.contracts import TelemetryEvent, TelemetryKind
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
