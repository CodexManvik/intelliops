"""Record the Python correlation engine over random event streams.

Covers both online detectors, window and service grouping, min_events,
suppression in drop and quiet modes, reset and snapshot/reload.

    uv run python rust/harness/record_engine.py
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from common.contracts import TelemetryEvent, TelemetryKind
from services.correlation.adapters.river_correlator import RiverCorrelator
from services.correlation.adapters.robust_correlator import RobustCorrelator
from services.correlation.detection_policy import DetectionPolicy
from services.correlation.engine import CorrelationEngine
from services.ingestion.normalize import compute_fingerprint

OUT = Path(__file__).resolve().parents[1] / "app-cases" / "engine.json"
START = datetime(2026, 1, 1, 22, 50, tzinfo=UTC)
METRICS = ["cpu_usage", "latency_p99_ms", "meridian_error_rate", "queue_depth", "service_up"]
SERVICES = ["gateway", "validation", None]


def summary(situation) -> dict | None:
    if situation is None:
        return None
    return {
        "signature": situation.signature,
        "severity": situation.severity,
        "handling": situation.handling,
        "members": len(situation.member_events),
        "first_seen": situation.first_seen.isoformat(),
        "last_seen": situation.last_seen.isoformat(),
        "peak_score": situation.peak_score,
        "baseline": situation.baseline,
    }


def scenario(seed: int) -> dict:
    rng = random.Random(seed)
    config = {
        "kind": "river" if seed % 2 == 0 else "robust",
        "key_by": rng.choice(["metric", "series"]),
        "policy": rng.random() < 0.4,
        "warmup": rng.choice([2, 5, 8]),
        "z_threshold": rng.choice([2.0, 3.0]),
        "window_seconds": rng.choice([5.0, 15.0, 30.0]),
        "group_by": rng.choice(["window", "service"]),
        "min_events": rng.choice([1, 1, 3]),
        "suppress_mode": rng.choice(["drop", "quiet"]),
        "suppress_min_samples": rng.choice([1, 3]),
        "bucket_size": rng.choice([16, 128]),
    }
    kwargs = {
        "z_threshold": config["z_threshold"],
        "warmup_samples": config["warmup"],
        "detection_policy": DetectionPolicy(enabled=config["policy"]),
    }
    if config["kind"] == "river":
        correlator = RiverCorrelator(**kwargs)
    else:
        correlator = RobustCorrelator(
            **kwargs, key_by=config["key_by"], window_size=config["bucket_size"]
        )
    engine = CorrelationEngine(
        correlator,
        window_seconds=config["window_seconds"],
        group_by=config["group_by"],
        min_events=config["min_events"],
        suppress_mode=config["suppress_mode"],
        suppress_min_samples=config["suppress_min_samples"],
    )
    base = {m: rng.choice([0.01, 1.0, 18.0, 95.0]) for m in METRICS}
    now = START
    ops: list[dict] = []
    seen: list[str] = []

    def drain() -> list[dict]:
        out = []
        while (s := engine.pop_suppressed()) is not None:
            out.append(summary(s))
        return out

    # A small set of metrics, so the same signature recurs and can be suppressed.
    metrics = rng.sample(METRICS, rng.choice([1, 2, 3]))
    services = rng.sample(SERVICES, rng.choice([1, 2]))
    for step in range(260):
        now += timedelta(seconds=rng.choice([0, 0.5, 1, 2, 7, 40]))
        roll = rng.random()
        if roll < 0.04:
            op = {"op": "flush", "out": [summary(s) for s in engine.flush_all()]}
        elif roll < 0.07 and seen:
            sig = rng.choice(seen)
            records = [
                {"signature": sig, "worked": rng.random() < 0.9}
                for _ in range(rng.choice([1, 3, 5]))
            ]
            engine.retrain(records)
            op = {"op": "retrain", "records": records}
        elif roll < 0.075:
            engine.reset()
            op = {"op": "reset"}
        else:
            name = rng.choice(metrics)
            service = rng.choice(services)
            labels = {} if service is None else {"service": service}
            b = base[name]
            spike = rng.random() < 0.18
            if name == "service_up":
                value = 0.0 if spike else 1.0
            elif spike:
                value = b * rng.choice([3.0, 10.0, 50.0])
            else:
                value = b if step % 50 < 20 else rng.gauss(b, b * 0.04 + 0.001)
            event = {
                "source": "s",
                "kind": TelemetryKind.METRIC,
                "name": name,
                "value": None if rng.random() < 0.02 else value,
                "labels": labels,
                "ts": now,
                "fingerprint": compute_fingerprint("s", name, labels),
            }
            ev = TelemetryEvent(**event)
            emitted = engine.add(ev)
            op = {"op": "add", "event": json.loads(ev.model_dump_json()), "out": summary(emitted)}
            if emitted is not None:
                seen.append(emitted.signature)
        op["suppressed"] = drain()
        for s in [*op.get("suppressed", [])]:
            seen.append(s["signature"])
        ops.append(op)
    return {"config": config, "ops": ops}


def main() -> None:
    data = [scenario(seed) for seed in range(80)]
    OUT.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
    ops = [o for s in data for o in s["ops"]]
    emitted = sum(1 for o in ops if o["op"] == "add" and o["out"]) + sum(
        len(o["out"]) for o in ops if o["op"] == "flush"
    )
    suppressed = [x for o in ops for x in o["suppressed"]]
    quiet = sum(x["handling"] == "quiet" for x in suppressed)
    print(f"{len(data)} scenarios, {len(ops)} ops, {emitted} situations,", end=" ")
    print(f"{len(suppressed)} suppressed ({quiet} quiet) -> {OUT}")


if __name__ == "__main__":
    main()
