"""Record the simulated workloads: fault profiles, ramps and idle jitter.

uv run python rust/harness/record_sim.py
"""

from __future__ import annotations

import json
from pathlib import Path

from common.telemetry import jitter
from services.meridian.common import FaultSpec, MeridianState

APP_CASES = Path(__file__).resolve().parents[1] / "app-cases"
FIELDS = [
    "cpu",
    "error_rate",
    "latency_ms",
    "request_rate",
    "latency_p50_ms",
    "latency_p99_ms",
    "memory_usage_mb",
    "saturation",
    "queue_depth",
    "db_pool_in_use",
    "db_pool_max",
    "disk_usage_percent",
    "service_up",
    "tls_handshake_failures",
]
FAULTS = [
    "saturation",
    "error",
    "latency",
    "crash",
    "memory_leak",
    "traffic_surge",
    "dependency_outage",
    "db_exhaustion",
    "unknown_signal",
    "nonsense",
]
METRICS = [
    "cpu_usage",
    "memory_usage_mb",
    "latency_p50_ms",
    "latency_p99_ms",
    "request_rate",
    "saturation",
    "queue_depth",
    "db_pool_in_use",
    "disk_usage_percent",
    "meridian_error_rate",
    "service_up",
]


def state(s: MeridianState) -> dict:
    return {name: getattr(s, name) for name in FIELDS}


def main() -> None:
    faults = []
    for kind in FAULTS:
        for magnitude in [0.0, 0.3, 1.0, 1.7]:
            s = MeridianState()
            s.apply(FaultSpec(type=kind, magnitude=magnitude, duration_seconds=100.0))
            ramp = []
            if s._ramp is not None:
                start = s._ramp["start_time"]
                for elapsed in [0.0, 25.0, 100.0, 500.0]:
                    s.sample(start + elapsed)
                    ramp.append([elapsed, s.memory_usage_mb])
                s.memory_usage_mb = ramp[0][1]
            faults.append({"type": kind, "magnitude": magnitude, "state": state(s), "ramp": ramp})
    wander = []
    for metric in METRICS:
        for service in ["demo-app", "meridian-gateway"]:
            for value in [0.0, 0.05, 1.0, 18.0, 92.0, 100.0]:
                for now in [0.0, 1234.5, 1767323045.25]:
                    wander.append(
                        [metric, service, value, now, jitter(metric, service, value, now)]
                    )
    out = {"faults": faults, "jitter": wander}
    (APP_CASES / "sim.json").write_text(
        json.dumps(out, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    print(len(faults), "faults,", len(wander), "jitter samples")


if __name__ == "__main__":
    main()
