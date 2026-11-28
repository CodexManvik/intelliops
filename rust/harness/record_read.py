"""Record the Python read model and feedback rules over random event sequences.

uv run python rust/harness/record_read.py
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from common.contracts import (
    DiagnosedSituation,
    HitlMode,
    PreflightResult,
    RemediationOutcome,
    RemediationResult,
    RootCauseHypothesis,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
)
from services.feedback.graduate import playbook_stats, should_graduate
from services.feedback.label import label_outcome
from services.feedback.metrics import compute_metrics
from services.read.projection import ReadModel

APP_CASES = Path(__file__).resolve().parents[1] / "app-cases"
START = datetime(2026, 1, 1, tzinfo=UTC)


def dump(model) -> dict:
    return json.loads(model.model_dump_json())


def situation(rng: random.Random, now: datetime) -> Situation:
    sig = f"sig{rng.randrange(12)}"
    labels = rng.choice(
        [{"service": "gateway"}, {"job": "demo"}, {"instance": "i:80"}, {}, {"service": ""}]
    )
    # Sometimes an older occurrence arrives late; sometimes the same one again.
    first = now - timedelta(seconds=rng.choice([0, 0, 30, 300]), microseconds=rng.randrange(10**6))
    if rng.random() < 0.4:
        first = START + timedelta(seconds=int(sig[3:]) * 7, microseconds=123456)
    members = [
        TelemetryEvent(
            source="s",
            kind=TelemetryKind.METRIC,
            name="cpu_usage",
            value=rng.choice([1.5, 92.0, None]),
            labels=labels,
            ts=first + timedelta(seconds=i, microseconds=rng.randrange(10**6)),
            fingerprint=f"f{i}",
        )
        for i in range(rng.choice([0, 1, 3, 25]))
    ]
    return Situation(
        id=f"sit-{sig}",
        status=SituationStatus.DETECTED,
        member_events=members,
        severity=rng.choice(["low", "medium", "high", "critical", "weird"]),
        first_seen=first,
        last_seen=first + timedelta(seconds=rng.randrange(40)),
        signature=sig,
        peak_score=rng.choice([None, 3.5]),
        baseline=rng.choice([None, {"cpu_usage": {"mean": 1.0, "std": 0.5}}]),
        handling=rng.choice(["normal", "quiet"]),
    )


def diagnosed(rng: random.Random, now: datetime) -> DiagnosedSituation:
    s = situation(rng, now)
    hypotheses = [
        RootCauseHypothesis(
            situation_id=s.id,
            description=f"cause {i}",
            confidence=rng.choice([0.2, 0.8]),
            evidence=["e"],
            suggested_runbook_id=rng.choice([None, "restart-pod"]),
            confidence_source=rng.choice([None, "rule"]),
        )
        for i in range(rng.choice([0, 1, 2]))
    ]
    return DiagnosedSituation(
        situation=s, hypotheses=hypotheses, suggested_runbook_id="restart-pod"
    )


def outcome(rng: random.Random, now: datetime) -> RemediationOutcome:
    return RemediationOutcome(
        situation_id=f"sit-sig{rng.randrange(13)}",
        playbook_id=rng.choice(["restart-pod", "scale-service"]),
        result=rng.choice(list(RemediationResult)),
        health_after=rng.choice(["healthy", "aborted:timeout", "unhealthy:rolled-back"]),
        ts=now + timedelta(seconds=rng.choice([-100000, 5, 90]), microseconds=rng.randrange(10**6)),
        hitl_mode=rng.choice(list(HitlMode)),
        steps=rng.choice([[], ["restart"]]),
        mode=rng.choice(["dry_run", "k8s", "none"]),
        preflight=rng.choice([None, PreflightResult(passed=True, detail="d", mode="off")]),
        handling=rng.choice(["normal", "quiet"]),
    )


def read_scenario(seed: int) -> dict:
    rng = random.Random(seed)
    config = {
        "max_outcomes": rng.choice([3, 200]),
        "ttl_seconds": rng.choice([60.0, 600.0]),
        "max_situations": rng.choice([3, 50]),
    }
    model = ReadModel(**config)
    now = START
    ops = []
    for _ in range(60):
        now += timedelta(seconds=rng.choice([0, 1, 20, 200]))
        kind = rng.choices(
            ["detected", "diagnosed", "suppressed", "outcome", "read", "reset"],
            [30, 20, 5, 30, 14, 1],
        )[0]
        op: dict = {"op": kind}
        if kind == "detected":
            event = situation(rng, now)
            model.apply_detected(event)
            op["event"] = dump(event)
        elif kind == "diagnosed":
            event = diagnosed(rng, now)
            model.apply_diagnosed(event)
            op["event"] = dump(event)
        elif kind == "suppressed":
            event = situation(rng, now)
            model.apply_suppressed(event)
            op["event"] = dump(event)
        elif kind == "outcome":
            event = outcome(rng, now)
            model.apply_outcome(event)
            op["event"] = dump(event)
        elif kind == "reset":
            model.reset()
        else:
            now_ms = int(now.timestamp() * 1000)
            # Copy now: the projection mutates its dicts in place later.
            op["now_ms"] = now_ms
            op["situations"] = json.loads(json.dumps(model.situations(now_ms=now_ms)))
            op["outcomes"] = json.loads(json.dumps(model.outcomes()))
            op["metrics"] = model.metrics()
        ops.append(op)
    return {"config": config, "ops": ops}


def feedback_scenario(seed: int) -> dict:
    rng = random.Random(seed)
    outcomes = [outcome(rng, START) for _ in range(rng.choice([0, 1, 4, 12, 40]))]
    records = [label_outcome(o) for o in outcomes]
    graduation = {}
    for playbook in ["restart-pod", "scale-service"]:
        for simulated in (False, True):
            stats = playbook_stats(records, playbook, count_simulated=simulated)
            graduation[f"{playbook}:{int(simulated)}"] = {
                **stats,
                "graduate": should_graduate(stats, 2),
            }
    return {
        "outcomes": [dump(o) for o in outcomes],
        "records": [dump(r) for r in records],
        "metrics": compute_metrics(records),
        "graduation": graduation,
    }


def main() -> None:
    reads = [read_scenario(seed) for seed in range(30)]
    feedback = [feedback_scenario(seed) for seed in range(60)]
    for name, data in [("read", reads), ("feedback", feedback)]:
        path = APP_CASES / f"{name}.json"
        path.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
        print(path.name, len(data), "scenarios", path.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
