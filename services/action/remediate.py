"""The remediation orchestration: three hard safety gates + outcome mapping.

Enforces (in order): disabled → skip; not reversible → refuse (ADR-007); RBAC
deny → fail closed (ADR-003); HITL → wait for explicit approval, reject/timeout
fail closed (ADR-008). Only then execute; verify health; roll back if unhealthy.
Every branch produces a RemediationOutcome (reason in health_after) and an audit
record threaded by the situation id (see flow.md 5.4)."""

from __future__ import annotations

from datetime import UTC, datetime

from common.contracts import (
    ApprovalRequest,
    AuditRecord,
    HitlMode,
    Playbook,
    RemediationOutcome,
    RemediationResult,
    Situation,
)

_ACTOR = "action-service"


def _outcome(situation: Situation, playbook: Playbook, result: RemediationResult,
             health_after: str) -> RemediationOutcome:
    return RemediationOutcome(
        situation_id=situation.id, playbook_id=playbook.id, result=result,
        health_after=health_after, ts=datetime.now(UTC),
    )


def _audit(gate, situation: Situation, playbook: Playbook, decision: str) -> None:
    gate.write_audit(AuditRecord(
        actor=_ACTOR, action="execute", resource=f"playbook:{playbook.id}",
        decision=decision, ts=datetime.now(UTC), correlation_id=situation.id,
    ))


def execute_remediation(situation: Situation, playbook: Playbook, gate, remediator,
                        health, timeout_seconds: float,
                        poll_interval_seconds: float) -> RemediationOutcome:
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

    # Gate 3: HITL — wait for an explicit human approval (ADR-008).
    if playbook.hitl_mode == HitlMode.HITL:
        request = gate.request_approval(ApprovalRequest(
            id=f"appr-{situation.id}", situation_id=situation.id, playbook_id=playbook.id,
            requested_by=_ACTOR,
        ))
        decided = gate.await_decision(request.id, timeout_seconds)
        if decided.status != "approved":
            reason = "aborted:rejected" if decided.status == "rejected" else "aborted:timeout"
            _audit(gate, situation, playbook, "abort")
            return _outcome(situation, playbook, RemediationResult.FAILURE, reason)

    # Execute.
    if not remediator.execute(playbook.steps):
        _audit(gate, situation, playbook, "execute-failed")
        return _outcome(situation, playbook, RemediationResult.FAILURE, "execute-failed")

    # Verify health; roll back if unhealthy.
    if health.check(situation):
        _audit(gate, situation, playbook, "allow")
        return _outcome(situation, playbook, RemediationResult.SUCCESS, "healthy")

    remediator.rollback(playbook.rollback_steps)
    _audit(gate, situation, playbook, "rolled-back")
    return _outcome(situation, playbook, RemediationResult.ROLLED_BACK, "unhealthy:rolled-back")
