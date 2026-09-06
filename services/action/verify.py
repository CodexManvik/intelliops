"""Per-metric recovery check for post-remediation verification.

A metric is 'recovered' when it is no longer anomalous by the SAME DetectionPolicy
rule that detected it (Phase 2). Pure: no Prometheus, no k8s — the current value
arrives via an injected `query_value` callable, so this is fully unit-testable.
Fail-safe: any query error/None, or a score-only ('default'-kind) metric with no
usable baseline, makes that metric not-recovered → the predicate returns False →
the caller rolls back (ADR-007). Never raises out of the returned predicate."""

from __future__ import annotations

from collections.abc import Callable

from common.contracts import Situation, TelemetryEvent, TelemetryKind
from services.correlation.detection_policy import DetectionPolicy, classify


def _baseline_score(situation: Situation, name: str, value: float) -> tuple[float, bool]:
    """Return (z_score, has_usable_baseline). z is 0.0 when no usable baseline."""
    b = (situation.baseline or {}).get(name)
    if not isinstance(b, dict):
        return 0.0, False
    mean = b.get("mean")
    std = b.get("std")
    if mean is None or std is None or std <= 0:
        return 0.0, False
    return (value - mean) / std, True


def build_metric_healthy(
    situation: Situation,
    query_value: Callable[[str], float | None],
    policy: DetectionPolicy,
    z_threshold: float = 3.0,
) -> Callable[[], bool]:
    # Firing metrics = metric-kind events that carry a value. Log/trace (value None) skipped.
    names = [
        e.name
        for e in situation.member_events
        if e.kind == TelemetryKind.METRIC and e.value is not None
    ]

    def _recovered(name: str) -> bool:
        try:
            current = query_value(name)
        except Exception:  # noqa: BLE001 — a failed query is 'not recovered'
            return False
        if current is None:
            return False
        score, has_baseline = _baseline_score(situation, name, current)
        # A metric decided by pure z-score has no absolute rule to fall back on, so a
        # fabricated score=0.0 (no usable baseline) would be mistaken for "at the mean"
        # and wrongly read as recovered. That happens for 'default'-kind metrics always,
        # and for EVERY metric when the policy is off (is_anomaly then ignores classify()
        # entirely and does score > z_threshold). Ratio/saturation/latency each keep an
        # absolute component when the policy is on, so a missing baseline there is
        # harmless -> only these two cases need the fail-safe.
        # policy.enabled: pure z-score mode (policy off) makes EVERY metric score-only.
        policy_enabled = policy.enabled
        score_only = not policy_enabled or classify(name) == "default"
        if score_only and not has_baseline:
            return False
        try:
            probe = TelemetryEvent(
                source="verify",
                kind=TelemetryKind.METRIC,
                name=name,
                value=current,
                ts=situation.last_seen,
                fingerprint=f"verify-{name}",
            )
            anomalous = policy.is_anomaly(probe, score, z_threshold)
        except Exception:  # noqa: BLE001 — any policy error is 'not recovered'
            return False
        return not anomalous

    def metric_healthy() -> bool:
        # Vacuously healthy when there is nothing to verify (pod-readiness then decides).
        return all(_recovered(n) for n in names)

    return metric_healthy
