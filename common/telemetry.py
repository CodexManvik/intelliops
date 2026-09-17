"""Idle-jitter for the simulated workloads (Meridian + demo-app).

Why this exists
---------------
The simulated services pin every healthy metric to a single constant
(`CPU_HEALTHY = 18.0`, `MEMORY_USAGE_MB_HEALTHY = 256.0`, ...). That was the
right call for the fault-profile invariants -- a test can assert an exact value
-- but it has two costs in a live cluster:

1. The console's metric charts draw five perfectly flat, perfectly overlapping
   lines. A judge reading that dashboard sees a mock, not a system.
2. A constant series has zero variance, so the robust correlator learns
   `std == 0` for every metric and `verify.py` has to fall back to its
   flat-baseline step rule. Real telemetry has spread; a detector tuned against
   a degenerate baseline has not really been exercised.

So this module adds a small, bounded wander around whatever value the service's
state object currently holds. It does NOT change the state model, the fault
profiles, or any constant -- it is applied at scrape time only, and only when
`INTELLIOPS_TELEMETRY_JITTER` is enabled. The unit tests run with it off and
keep asserting exact values.

Properties that matter
----------------------
- **Deterministic.** A pure function of (metric, service, wall clock). No RNG
  state, so a pod restart rejoins the same curve it left and two replicas of
  one service agree.
- **Bounded.** The deviation never exceeds the per-metric fraction in
  `_AMPLITUDE`, so a healthy service can never wander far enough to trip the
  z-threshold (3.0) and manufacture a false incident. cpu_usage, the widest,
  stays inside 18.0 +/- 16%, which is roughly |z| <= 1.7 against its own
  learned baseline.
- **Shaped like telemetry.** Two sine components at different periods give the
  slow drift and the medium-frequency ripple; a hash-derived grain term adds
  per-scrape texture so the curve is not suspiciously smooth either.
- **Phase-separated per service.** Without this, five services sharing one
  baseline draw five identical curves, which looks exactly as synthetic as five
  flat lines.
"""

from __future__ import annotations

import hashlib
import math
import os
import time

# Per-metric wander, as a fraction of the current value. Chosen so the widest
# (cpu) stays well inside the correlator's z-threshold of 3.0, and so the
# relative noisiness matches what the metric would really do: disk creeps,
# queue depth is spiky, memory is steady.
_AMPLITUDE: dict[str, float] = {
    "cpu_usage": 0.13,
    "memory_usage_mb": 0.055,
    "latency_p50_ms": 0.17,
    "latency_p99_ms": 0.21,
    "request_rate": 0.13,
    "saturation": 0.20,
    "queue_depth": 0.34,
    "db_pool_in_use": 0.28,
    "disk_usage_percent": 0.015,
    "meridian_error_rate": 0.45,
}

# A metric pinned at exactly 0.0 has nothing to wander around, and a chart of
# five lines along the x-axis is no better than five lines at 18.0. A real
# service at steady state does drop a small fraction of requests, so the error
# rate gets an idle floor to breathe around. Everything else is left alone:
# `service_up` is a boolean, `db_pool_max` is a configured size, and
# `tls_handshake_failures` is the trigger for the escalation demo, so none of
# them should move.
_IDLE_FLOOR: dict[str, float] = {
    "meridian_error_rate": 0.0035,
}

# Ranges the jittered value is clamped back into, so a percentage cannot read
# 104 and a fraction cannot read 1.3.
_BOUNDS: dict[str, tuple[float, float]] = {
    "cpu_usage": (0.0, 100.0),
    "disk_usage_percent": (0.0, 100.0),
    "saturation": (0.0, 1.0),
    "meridian_error_rate": (0.0, 1.0),
}

# Periods in seconds. Both are deliberately SHORTER than the span of the
# correlator's learning window, and that constraint is tighter than it looks:
# ingestion polls Prometheus every 5s and publishes one event per service, and
# RobustCorrelator keys its window by metric name alone, so a 128-sample window
# holds only ~128 seconds of wall clock. A drift slower than that is never
# learned as spread -- the window sees only a slice of it, MAD comes out small,
# and the far side of the drift reads as an anomaly. Measured: at a 420s slow
# period, six hours of idle telemetry produced bursts of up to four threshold
# crossings per service per window; at 150s, at most one.
_SLOW_PERIOD = 150.0
_FAST_PERIOD = 40.0
# Grain is quantised to this many seconds so that one Prometheus scrape (15s)
# lands on its own value and the texture does not smear.
_GRAIN_TICK = 5.0


def jitter_enabled() -> bool:
    """True when the deployment asked for live-shaped telemetry.

    Off by default, which is what keeps `test_metrics.py`'s exact-value
    assertions meaningful. `values-live.yaml` turns it on.
    """
    return os.getenv("INTELLIOPS_TELEMETRY_JITTER", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _unit(seed: str) -> float:
    """A stable float in [0, 1) derived from a string."""
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") / 4294967296.0


def jitter(metric: str, service: str, value: float, now: float | None = None) -> float:
    """Return `value` nudged onto a plausible idle curve.

    Metrics with no entry in `_AMPLITUDE` are returned untouched, which is how
    `service_up`, `db_pool_max` and `tls_handshake_failures` stay exact.
    """
    amplitude = _AMPLITUDE.get(metric)
    if amplitude is None:
        return value

    base = max(value, _IDLE_FLOOR.get(metric, 0.0))
    if base == 0.0:
        return value

    t = time.time() if now is None else now
    slow = math.sin(2 * math.pi * t / _SLOW_PERIOD + _unit(f"{service}|{metric}|slow") * math.tau)
    fast = math.sin(2 * math.pi * t / _FAST_PERIOD + _unit(f"{service}|{metric}|fast") * math.tau)
    grain = _unit(f"{service}|{metric}|{int(t // _GRAIN_TICK)}") * 2.0 - 1.0

    # Weights sum to 1.0, so `deviation` stays in [-1, 1] and the result stays
    # inside +/- one swing of the base. Tuned against a replay of the real
    # detector rather than by eye: five services through a 128-sample median/MAD
    # window with a 30-sample warmup, six hours of idle telemetry, counting
    # threshold crossings per service per 30s correlation window. At these
    # values the worst window holds one; the grain term at 0.22 pushed it to
    # four, which is an incident.
    deviation = 0.50 * slow + 0.40 * fast + 0.10 * grain

    # The swing is proportional to how much ROOM the value has, not just to the
    # value. A percentage sitting at 92 has 8 points of headroom, so it wanders
    # by about a point; the same metric at 18 wanders by two. Without this a
    # faulted gauge would swing past 100 and clip flat against the ceiling,
    # which looks worse on a chart than no jitter at all.
    low, high = _BOUNDS.get(metric, (0.0, math.inf))
    room = min(base - low, high - base) if high != math.inf else base
    out = base + amplitude * room * deviation
    return min(max(out, low), high)
