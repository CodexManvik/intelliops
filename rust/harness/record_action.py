"""Record the Python remediation gates and the post-fix health predicate.

uv run python rust/harness/record_action.py
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime
from pathlib import Path

from common.contracts import (
    ApprovalRequest,
    HitlMode,
    Playbook,
    PreflightResult,
    RemediationStep,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
)
from services.action.remediate import execute_remediation
from services.action.verify import build_metric_healthy
from services.correlation.detection_policy import DetectionPolicy

APP_CASES = Path(__file__).resolve().parents[1] / "app-cases"
NOW = datetime(2026, 1, 1, tzinfo=UTC)
QUANTITIES = [None, "5m", "10m", "500m", "0.001", "1", "abc", "", " 20m ", "15Mi", "16Mi", "1Gi"]
QUANTITIES += ["0.01Gi", "2048Ki", "20M", "10M", "1G", "1K", "1000000", "999", "Mi"]
ACTIONS = [
    "restart",
    "scale",
    "rollback_deploy",
    "wait",
    "patch_resource_limits",
    "rollback_to_revision",
    "patch_probe",
]
PROBE_FIELDS = [
    "failure_threshold",
    "period_seconds",
    "timeout_seconds",
    "initial_delay_seconds",
]


def step(rng: random.Random) -> RemediationStep:
    action = rng.choice(ACTIONS)
    fields: dict = {"action": action}
    if action == "scale":
        fields["replicas"] = rng.choice([None, 2, -2, -9, -10, -50, 0])
    if action == "patch_resource_limits":
        fields["cpu_limit"] = rng.choice(QUANTITIES)
        fields["mem_limit"] = rng.choice(QUANTITIES)
    if action == "rollback_to_revision":
        fields["revision"] = rng.choice([None, 3])
    if action == "patch_probe":
        fields["probe"] = rng.choice([None, "liveness", "readiness", "liveness"])
        for name in PROBE_FIELDS:
            fields[name] = rng.choice([None, None, 1, 5, 0, -1])
    fields["note"] = rng.choice([None, None, "n", ""])
    return RemediationStep(**fields)


def situation(rng: random.Random) -> Situation:
    labels = rng.choice(
        [
            [{"service": "gateway"}],
            [{"job": "demo"}],
            [{}],
            [{}, {"service": ""}, {"service": "b"}],
            [{"service": "a", "job": "j"}],
        ]
    )
    names = ["cpu_usage", "service_up", "latency_p99_ms", "error_rate", "queue_depth"]
    names.append("saturation")
    members = [
        TelemetryEvent(
            source="s",
            kind=rng.choice([TelemetryKind.METRIC] * 5 + [TelemetryKind.LOG]),
            name=rng.choice(names),
            value=rng.choice([1.0, 90.0, None]),
            labels=label,
            ts=NOW,
            fingerprint="f",
        )
        for label in labels
    ]
    entries = [
        None,
        {"mean": 1.0, "std": 0.0},
        {"mean": 18.0, "std": 2.0},
        {"mean": 0.0, "std": 0.0},
        {"mean": None, "std": 1.0},
        {"mean": 100.0, "std": None},
    ]
    baseline = {}
    for event in members:
        if (entry := rng.choice(entries)) is not None:
            baseline[event.name] = entry
    return Situation(
        id="sit-abc",
        status=SituationStatus.DIAGNOSED,
        member_events=members,
        severity="low",
        first_seen=NOW,
        last_seen=NOW,
        signature="abc",
        baseline=rng.choice([baseline, baseline, None]),
    )


class Gate:
    def __init__(self, allowed: bool, status: str) -> None:
        self.allowed, self.status = allowed, status
        self.calls: list[str] = []
        self.request = None

    def check_rbac(self, actor, action, resource):
        return self.allowed

    def request_approval(self, request: ApprovalRequest):
        self.calls.append("request")
        self.request = request
        return request

    def await_decision(self, approval_id, timeout_seconds):
        return self.request.model_copy(update={"status": self.status})

    def write_audit(self, record):
        self.calls.append(f"audit:{record.decision}")

    def expire(self, approval_id, actor):
        self.calls.append("expire")


class Remediator:
    def __init__(self, execute: bool, rollback: bool, calls: list[str]) -> None:
        self.results, self.calls = (execute, rollback), calls

    def execute(self, plan):
        self.calls.append(f"execute:{plan.target.deployment}")
        return self.results[0]

    def rollback(self, plan):
        self.calls.append("rollback")
        return self.results[1]


class Health:
    def __init__(self, healthy: bool) -> None:
        self.healthy = healthy

    def check(self, situation, target):
        return self.healthy


class Sandbox:
    def __init__(self, passed: bool, calls: list[str]) -> None:
        self.passed, self.calls = passed, calls

    def rehearse(self, situation, plan):
        self.calls.append("rehearse")
        return PreflightResult(passed=self.passed, detail="d", mode="off")


def gates(seed: int) -> dict:
    rng = random.Random(seed)
    simple = rng.random() < 0.6
    if simple:
        steps = [RemediationStep(action="restart")]
        undo = [RemediationStep(action="restart")]
    else:
        steps = [step(rng) for _ in range(rng.choice([0, 1, 2]))]
        undo = [step(rng)]
    modes = [HitlMode.HITL, HitlMode.HITL, HitlMode.AUTO, HitlMode.DISABLED]
    playbook = Playbook(
        id="pb",
        name="n",
        match_rule="m",
        steps=steps,
        rollback_steps=[] if rng.random() < 0.5 else undo,
        hitl_mode=rng.choice(modes),
        reversible=rng.random() < 0.85,
    )
    world = {
        "rbac": rng.random() < 0.85,
        "decision": rng.choice(["approved", "approved", "rejected", "pending", "expired"]),
        "execute": rng.random() < 0.8,
        "healthy": rng.random() < 0.6,
        "rollback": rng.random() < 0.7,
        "preflight": rng.random() < 0.7,
        "skip_approval": rng.random() < 0.25,
    }
    s = situation(rng)
    gate = Gate(world["rbac"], world["decision"])
    out = execute_remediation(
        s,
        playbook,
        gate,
        Remediator(world["execute"], world["rollback"], gate.calls),
        Health(world["healthy"]),
        Sandbox(world["preflight"], gate.calls),
        1.0,
        0.1,
        skip_approval=world["skip_approval"],
    )
    outcome = json.loads(out.model_dump_json())
    outcome.pop("ts")
    return {
        "situation": json.loads(s.model_dump_json()),
        "playbook": json.loads(playbook.model_dump_json()),
        "world": world,
        "calls": gate.calls,
        "outcome": outcome,
    }


def verify(seed: int) -> dict:
    rng = random.Random(seed)
    s = situation(rng)
    enabled = rng.random() < 0.5
    options = [None, 0.0, 1.0, 1.4, 18.0, 25.0, 91.0, 600.0, 0.01, -3.0, 100.0]
    values = {e.name: rng.choice(options) for e in s.member_events}
    healthy = build_metric_healthy(s, values.get, DetectionPolicy(enabled=enabled), 3.0)()
    return {
        "situation": json.loads(s.model_dump_json()),
        "policy": enabled,
        "values": values,
        "healthy": healthy,
    }


def main() -> None:
    data = {"gates": [gates(s) for s in range(2500)], "verify": [verify(s) for s in range(600)]}
    (APP_CASES / "action.json").write_text(
        json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    reasons: dict[str, int] = {}
    for case in data["gates"]:
        reason = case["outcome"]["health_after"]
        reasons[reason] = reasons.get(reason, 0) + 1
    print(reasons)
    print(sum(v["healthy"] for v in data["verify"]), "of 600 recovered")


if __name__ == "__main__":
    main()
