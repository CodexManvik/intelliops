"""The remediation orchestration: three hard safety gates + outcome mapping.

Enforces (in order): disabled → skip; not reversible → refuse (ADR-007); RBAC
deny → fail closed (ADR-003); HITL → wait for explicit approval, reject/timeout
fail closed (ADR-008). Only then execute; verify health; roll back if unhealthy.
Every branch produces a RemediationOutcome (reason in health_after) and an audit
record threaded by the situation id (see flow.md 5.4)."""

from __future__ import annotations

from datetime import UTC, datetime

from common.config import get_settings
from common.contracts import (
    ApprovalRequest,
    AuditRecord,
    HitlMode,
    Playbook,
    PreflightResult,
    RemediationOutcome,
    RemediationPlan,
    RemediationResult,
    Situation,
)
from services.action.targets import resolve_target

_ACTOR = "action-service"

# Destructive-shape denylist floors. The closed action Literal already excludes
# catastrophic *verbs*; these guard dangerous *shapes* of allowed verbs and are
# the gate that will also protect AI-authored runbooks (PR C). Each floor is a
# deliberate, documented safety minimum.
_SCALE_TAKEDOWN_DELTA = -10  # a scale delta of -10 or beyond can zero any in-range
# deployment (_MAX_REPLICAS is 10) → treated as a take-down
_MIN_CPU_MILLICORES = 10  # reject cpu_limit below 10m (would throttle the pod to death)
_MIN_MEM_MEBIBYTES = 16  # reject mem_limit below 16Mi (would OOM immediately)
_MIN_FAILURE_THRESHOLD = 1  # a probe failureThreshold < 1 is invalid/defeats the probe
_MIN_PROBE_PERIOD_SECONDS = 1  # non-positive probe periods are invalid


def _cpu_millicores(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        s = v.strip()
        return float(s[:-1]) if s.endswith("m") else float(s) * 1000.0
    except (ValueError, AttributeError):
        return -1.0  # unparseable -> treat as below any floor (unsafe)


def _mem_mebibytes(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        s = v.strip()
        if s.endswith("Gi"):
            return float(s[:-2]) * 1024.0
        if s.endswith("Mi"):
            return float(s[:-2])
        if s.endswith("Ki"):
            return float(s[:-2]) / 1024.0
        return float(s) / (1024.0 * 1024.0)  # bare bytes
    except (ValueError, AttributeError):
        return -1.0  # unparseable -> unsafe


def _denylist_reason(playbook: Playbook) -> str | None:
    for step in list(playbook.steps) + list(playbook.rollback_steps):
        # We cannot know current replicas here, but a large negative delta is
        # a disguised take-down; the dispatch clamps to >=1, but a playbook
        # that *intends* to zero out a deployment must be refused, not
        # silently clamped. _MAX_REPLICAS is 10, so a delta of -10 or beyond
        # can zero any in-range deployment — refuse it.
        if (
            step.action == "scale"
            and step.replicas is not None
            and step.replicas <= _SCALE_TAKEDOWN_DELTA
        ):
            return "denied:unsafe-scale"
        if step.action == "patch_resource_limits":
            cpu = _cpu_millicores(step.cpu_limit)
            mem = _mem_mebibytes(step.mem_limit)
            if (cpu is not None and cpu < _MIN_CPU_MILLICORES) or (
                mem is not None and mem < _MIN_MEM_MEBIBYTES
            ):
                return "denied:unsafe-limits"
        if step.action == "patch_probe":
            if (
                step.failure_threshold is not None
                and step.failure_threshold < _MIN_FAILURE_THRESHOLD
            ):
                return "denied:unsafe-probe"
            # period_seconds and timeout_seconds must be >= 1 if set (a zero/negative
            # period is invalid k8s and defeats the probe).
            for p in (step.period_seconds, step.timeout_seconds):
                if p is not None and p < _MIN_PROBE_PERIOD_SECONDS:
                    return "denied:unsafe-probe"
            # initial_delay_seconds may be 0 (fine) but not negative.
            if step.initial_delay_seconds is not None and step.initial_delay_seconds < 0:
                return "denied:unsafe-probe"
    return None


def _outcome(
    situation: Situation,
    playbook: Playbook,
    result: RemediationResult,
    health_after: str,
    steps: list[str] | None = None,
    mode: str = "dry_run",
    preflight: PreflightResult | None = None,
) -> RemediationOutcome:
    return RemediationOutcome(
        situation_id=situation.id,
        playbook_id=playbook.id,
        result=result,
        health_after=health_after,
        ts=datetime.now(UTC),
        hitl_mode=playbook.hitl_mode,
        steps=steps or [],
        mode=mode,
        preflight=preflight,
    )


def _format_steps(plan: RemediationPlan) -> list[str]:
    out: list[str] = []
    for step in plan.steps:
        if step.action == "scale" and step.replicas is not None:
            sign = "+" if step.replicas >= 0 else ""
            out.append(f"scale {plan.target.deployment} {sign}{step.replicas} replicas")
        elif step.note:
            out.append(f"{step.action}: {step.note}")
        else:
            out.append(step.action)
    return out


def _audit(gate, situation: Situation, playbook: Playbook, decision: str) -> None:
    gate.write_audit(
        AuditRecord(
            actor=_ACTOR,
            action="execute",
            resource=f"playbook:{playbook.id}",
            decision=decision,
            ts=datetime.now(UTC),
            correlation_id=situation.id,
        )
    )


def execute_remediation(
    situation: Situation,
    playbook: Playbook,
    gate,
    remediator,
    health,
    sandbox,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> RemediationOutcome:
    # Gate 0: disabled playbooks never run.
    if playbook.hitl_mode == HitlMode.DISABLED:
        _audit(gate, situation, playbook, "skipped")
        return _outcome(situation, playbook, RemediationResult.FAILURE, "skipped:disabled")

    # Gate 1: reversible-only (ADR-007) — a non-reversible playbook is refused.
    if not playbook.reversible:
        _audit(gate, situation, playbook, "refused")
        return _outcome(situation, playbook, RemediationResult.FAILURE, "refused:not-reversible")

    # Gate 2: RBAC, fail closed (ADR-003).
    if not gate.check_rbac(_ACTOR, "execute", f"playbook:{playbook.id}"):
        _audit(gate, situation, playbook, "deny")
        return _outcome(situation, playbook, RemediationResult.FAILURE, "denied:rbac")

    # Gate 2.5: destructive-shape denylist (fail closed). Runs before the plan
    # build and the sandbox — a dangerous-but-valid-looking step could pass the
    # rehearsal, so a hard gate must precede it. Also guards AI-authored runbooks.
    reason = _denylist_reason(playbook)
    if reason is not None:
        _audit(gate, situation, playbook, "denied-unsafe")
        return _outcome(situation, playbook, RemediationResult.FAILURE, reason)

    # Resolve the target once and build a typed plan (needed by the sandbox below).
    target = resolve_target(situation, get_settings().k8s_namespace)
    plan = RemediationPlan(
        target=target, steps=playbook.steps, rollback_steps=playbook.rollback_steps
    )
    steps = _format_steps(plan)
    mode = get_settings().remediator_mode

    # Pre-flight rehearsal: try the fix on an isolated clone before the human
    # approves (and before an auto playbook executes). Fail-safe — the sandbox
    # never raises; a failure is a PreflightResult(passed=False).
    preflight = sandbox.rehearse(situation, plan)
    if not preflight.passed and playbook.hitl_mode == HitlMode.AUTO:
        # Auto has no human to advise — block.
        _audit(gate, situation, playbook, "preflight-failed")
        return _outcome(
            situation,
            playbook,
            RemediationResult.FAILURE,
            "preflight-failed",
            steps=steps,
            mode=mode,
            preflight=preflight,
        )

    # Gate 3: HITL — wait for an explicit human approval (ADR-008). The human
    # sees the pre-flight verdict on the request.
    if playbook.hitl_mode == HitlMode.HITL:
        request = gate.request_approval(
            ApprovalRequest(
                id=f"appr-{situation.id}",
                situation_id=situation.id,
                playbook_id=playbook.id,
                requested_by=_ACTOR,
                preflight=preflight,
            )
        )
        decided = gate.await_decision(request.id, timeout_seconds)
        if decided.status != "approved":
            reason = "aborted:rejected" if decided.status == "rejected" else "aborted:timeout"
            _audit(gate, situation, playbook, "abort")
            return _outcome(
                situation, playbook, RemediationResult.FAILURE, reason, preflight=preflight
            )

    # Execute.
    if not remediator.execute(plan):
        _audit(gate, situation, playbook, "execute-failed")
        return _outcome(
            situation,
            playbook,
            RemediationResult.FAILURE,
            "execute-failed",
            steps=steps,
            mode=mode,
            preflight=preflight,
        )

    # Verify health; roll back if unhealthy.
    if health.check(situation, target):
        _audit(gate, situation, playbook, "allow")
        return _outcome(
            situation,
            playbook,
            RemediationResult.SUCCESS,
            "healthy",
            steps=steps,
            mode=mode,
            preflight=preflight,
        )

    remediator.rollback(plan)
    _audit(gate, situation, playbook, "rolled-back")
    return _outcome(
        situation,
        playbook,
        RemediationResult.ROLLED_BACK,
        "unhealthy:rolled-back",
        steps=steps,
        mode=mode,
        preflight=preflight,
    )
