"""Record the Python detector over random streams, as the oracle for Rust.

The detector is stateful, so rust-anything's per-function recorder cannot cover
it; this records whole sequences instead.

    uv run python rust/harness/record_detector.py
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime
from pathlib import Path

from common.contracts import TelemetryEvent, TelemetryKind
from services.correlation.adapters.river_correlator import RiverCorrelator
from services.correlation.detection_policy import DetectionPolicy, classify

OUT = Path(__file__).resolve().parents[1] / "app-cases" / "detector.json"
METRICS = [
    "cpu_usage",
    "Latency_P99_ms",
    "meridian_error_rate",
    "saturation",
    "disk_usage_percent",
    "queue_depth",
    "cache_hit_ratio",
    "request_duration",
    "memory_usage_mb",
]


def event(name: str, value: float | None) -> TelemetryEvent:
    return TelemetryEvent(
        source="s",
        kind=TelemetryKind.METRIC,
        name=name,
        value=value,
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        fingerprint="f",
    )


def value(rng: random.Random, base: float, flat: bool) -> float | None:
    roll = rng.random()
    if roll < 0.03:
        return None
    if flat:
        return base
    if roll < 0.08:
        return base * rng.choice([0.0, 5.0, 50.0, -1.0])
    return rng.gauss(base, abs(base) * 0.05 + 0.001)


def scenario(seed: int) -> dict:
    rng = random.Random(seed)
    warmup = rng.choice([0, 1, 2, 8, 30, 50])
    threshold = rng.choice([2.0, 3.0])
    policy_on = seed % 2 == 0
    correlator = RiverCorrelator(
        z_threshold=threshold,
        warmup_samples=warmup,
        detection_policy=DetectionPolicy(enabled=policy_on),
    )
    names = rng.sample(METRICS, 3)
    bases = {n: rng.choice([0.01, 0.9, 18.0, 95.0, 480.0, 1e9]) for n in names}
    flat = {n: rng.random() < 0.2 for n in names}
    steps = []
    for _ in range(150):
        name = rng.choice(names)
        v = value(rng, bases[name], flat[name])
        ev = event(name, v)
        score = correlator.detect(ev)
        steps.append(
            {
                "name": name,
                "value": v,
                "score": score,
                "anomaly": correlator.is_anomaly_scored(ev, score),
            }
        )
    return {
        "warmup": warmup,
        "threshold": threshold,
        "policy": policy_on,
        "steps": steps,
        "snapshot": sorted(correlator.snapshot(), key=lambda r: r["metric_name"]),
        "classes": {n: classify(n) for n in METRICS},
    }


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    data = [scenario(seed) for seed in range(60)]
    OUT.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
    steps = sum(len(s["steps"]) for s in data)
    flagged = sum(x["anomaly"] for s in data for x in s["steps"])
    print(f"{len(data)} scenarios, {steps} steps, {flagged} anomalies -> {OUT}")


if __name__ == "__main__":
    main()
