"""Metric-kind-aware anomaly decision, layered over the per-metric z-score.

A z-score is right for an unbounded utilization signal against a stable
baseline, but wrong for a bounded ratio (an error rate must fire on an
absolute level, not standard deviations) and for seasonal latency. This
policy classifies a metric by name and applies the right rule. `enabled=False`
reproduces the pure z-score decision (detect() > z_threshold) EXACTLY —
config-switched off is byte-identical to pre-policy behavior."""

from __future__ import annotations

from common.contracts import TelemetryEvent

_KIND_PATTERNS = [
    ("ratio", ("error_rate", "error_ratio", "_ratio")),
    ("latency", ("latency", "duration", "_ms")),
    ("saturation", ("saturation", "utilization", "disk_usage", "cpu_usage", "_percent")),
]

_DEFAULTS = {
    "ratio": 0.02,
    "saturation_ratio": 0.80,
    "saturation_percent": 90.0,
    "latency_ceiling_ms": 500.0,
}


def classify(metric_name: str) -> str:
    n = metric_name.lower()
    for kind, tokens in _KIND_PATTERNS:
        if any(tok in n for tok in tokens):
            return kind
    return "default"


def _is_percent_scale(name: str) -> bool:
    n = name.lower()
    return n.endswith("_percent") or n == "cpu_usage"


class DetectionPolicy:
    def __init__(self, enabled: bool, thresholds: dict | None = None) -> None:
        self._enabled = enabled
        self._t = {**_DEFAULTS, **(thresholds or {})}

    @property
    def enabled(self) -> bool:
        return self._enabled

    def is_anomaly(self, event: TelemetryEvent, score: float, z_threshold: float) -> bool:
        if not self._enabled:
            return score > z_threshold
        if event.value is None:
            return False
        kind = classify(event.name)
        if kind == "ratio":
            return event.value > self._t["ratio"]
        if kind == "saturation":
            cutoff = (
                self._t["saturation_percent"]
                if _is_percent_scale(event.name)
                else self._t["saturation_ratio"]
            )
            return event.value > cutoff
        if kind == "latency":
            return score > z_threshold or event.value > self._t["latency_ceiling_ms"]
        return score > z_threshold  # default
