"""Bus consumer for action-service.

Consumes situations.diagnosed, selects a playbook, runs it through the
remediation gates, and publishes a RemediationOutcome on remediation.outcomes.
When no playbook matches, emits an ESCALATED outcome — nothing was attempted
because there was no candidate fix, so a human must look; Slice-4 feedback
deliberately ignores escalations rather than learning from a non-decision.
Runs in a daemon thread started by the FastAPI lifespan."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime

from common.contracts import (
    AuditRecord,
    DiagnosedSituation,
    HitlMode,
    RemediationOutcome,
    RemediationResult,
)
from common.envelope import iter_models, publish_model
from common.evidence import quiet_eligible
from common.idempotency import NullGuard
from services.action.remediate import _ACTOR, execute_remediation
from services.action.select import select_playbook

logger = logging.getLogger("intelliops.action.consumer")


def _audit_best_effort(gate, situation_id: str, action: str, decision: str) -> None:
    """Record a non-remediation decision. Best effort by design: the audit sink
    propagates errors, and losing the outcome (this runs on a daemon thread)
    is worse than losing one audit row."""
    try:
        gate.write_audit(
            AuditRecord(
                actor=_ACTOR,
                action=action,
                resource=f"situation:{situation_id}",
                decision=decision,
                ts=datetime.now(UTC),
                correlation_id=situation_id,
            )
        )
    except Exception as exc:  # noqa: BLE001 - audit failure must never kill the thread
        logger.warning("%s audit write failed for %s: %s", action, situation_id, exc)


def _quiet_decision(
    gate,
    situation,
    playbook,
    training_store,
    threshold: float,
    min_samples: int,
    allowed: bool,
) -> bool:
    """Should this quiet situation's playbook run without a human approval?

    Correlation marks a signature quiet from its own reliability map; that is a
    request, and it is not specific to the playbook RCA then picked. So the
    decision is re-made here against THIS playbook's real track record on THIS
    signature (common/evidence.py). Whatever the answer, it is audited and
    logged: a quiet fix must never be invisible.

    An AUTO playbook never asks a human anyway, so there is nothing to waive.
    """
    if playbook.hitl_mode != HitlMode.HITL:
        logger.info("quiet situation %s: playbook %s is already auto", situation.id, playbook.id)
        return False
    record = None
    eligible = False
    if allowed and training_store is not None:
        try:
            eligible, record = quiet_eligible(
                training_store.read_all(), situation.signature, playbook.id, threshold, min_samples
            )
        except Exception as exc:  # noqa: BLE001 - no evidence means ask a human
            logger.warning("quiet situation %s: track record unreadable (%s)", situation.id, exc)
    evidence = str(record) if record is not None else "no track record available"
    if not allowed:
        evidence = "quiet approval disabled (QUIET_SKIP_APPROVAL=false)"
    decision = "quiet:skip-approval" if eligible else "quiet:needs-approval"
    logger.info(
        "quiet situation %s, playbook %s: %s (%s)",
        situation.id,
        playbook.id,
        "running without approval" if eligible else "asking a human after all",
        evidence,
        extra={"situation_id": situation.id, "playbook_id": playbook.id, "quiet": eligible},
    )
    _audit_best_effort(gate, situation.id, "quiet-handling", f"{decision} ({evidence})")
    return eligible


def run_consumer(
    bus,
    store,
    gate,
    remediator,
    health,
    sandbox,
    timeout_seconds: float,
    poll_interval_seconds: float,
    stop_event: threading.Event,
    guard=None,
    training_store=None,
    quiet_threshold: float = 0.8,
    quiet_min_samples: int = 3,
    quiet_skip_approval: bool = True,
) -> None:
    guard = guard if guard is not None else NullGuard()
    for diagnosed in iter_models(
        bus, "situations.diagnosed", "action", DiagnosedSituation, guard=guard, dlq=bus
    ):
        if stop_event.is_set():
            break
        situation = diagnosed.situation
        # Two-phase execution claim. This service mutates a REAL cluster, so a
        # redelivery must never silently re-run a remediation. Keyed on the
        # situation (not the event id) so a re-emitted diagnosis is caught too.
        # Inert by default: NullGuard.claim always wins.
        # situation.id is a content hash of the member fingerprints, so the same
        # incident SHAPE recurring later reuses it. first_seen makes the claim
        # per-occurrence; without it a recurring incident would be blocked for the
        # whole idempotency TTL with no outcome and no escalation.
        claim_key = f"action:exec:{situation.id}:{situation.first_seen.isoformat()}"
        if not guard.claim(claim_key):
            if guard.state(claim_key) == "done":
                continue  # already fully handled; do not re-execute or re-publish
            # in_progress: a previous attempt died mid-flight and we cannot know
            # whether the cluster was already mutated. Never guess -- surface it
            # and let a human look.
            interrupted = RemediationOutcome(
                situation_id=situation.id,
                playbook_id="",
                result=RemediationResult.FAILURE,
                health_after="interrupted:unknown",
                ts=datetime.now(UTC),
                hitl_mode=HitlMode.DISABLED,
                mode="none",
                steps=[],
            )
            _audit_best_effort(gate, situation.id, "interrupted", "unknown-if-mutated")
            publish_model(bus, "remediation.outcomes", interrupted)
            guard.set_state(claim_key, "done")
            continue
        guard.set_state(claim_key, "in_progress")
        playbook = select_playbook(diagnosed, store)
        if playbook is None:
            # select_playbook returns None for two distinct reasons; an operator
            # triaging the card needs to know which — "RCA had nothing to suggest"
            # and "RCA suggested a runbook nobody registered" are different bugs.
            suggested = diagnosed.suggested_runbook_id
            reason = "escalated:no-diagnosis" if not suggested else "escalated:unknown-runbook"
            outcome = RemediationOutcome(
                situation_id=situation.id,
                # Keep the suggested id as genuine provenance — it is what RCA
                # named, even though nothing ran. Downstream filters must key on
                # result == ESCALATED, never on an empty playbook_id.
                playbook_id=suggested or "",
                result=RemediationResult.ESCALATED,
                health_after=reason,
                ts=datetime.now(UTC),
                # Defaults (HITL / "dry_run") would claim a run was planned and
                # rehearsed; nothing was planned, approved or executed.
                hitl_mode=HitlMode.DISABLED,
                mode="none",
                steps=[],
            )
            # The only outcome-producing path here that had no audit trail.
            _audit_best_effort(gate, situation.id, "escalate", "escalated")
        else:
            skip = False
            if situation.handling == "quiet":
                skip = _quiet_decision(
                    gate,
                    situation,
                    playbook,
                    training_store,
                    quiet_threshold,
                    quiet_min_samples,
                    quiet_skip_approval,
                )
            outcome = execute_remediation(
                situation,
                playbook,
                gate,
                remediator,
                health,
                sandbox,
                timeout_seconds,
                poll_interval_seconds,
                skip_approval=skip,
            )
            if situation.handling == "quiet" and (skip or playbook.hitl_mode == HitlMode.AUTO):
                # Nobody was asked: this is what "handled quietly" means.
                outcome = outcome.model_copy(update={"handling": "quiet"})
        publish_model(bus, "remediation.outcomes", outcome)
        guard.set_state(claim_key, "done")
