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
from services.action.remediate import _ACTOR, execute_remediation
from services.action.select import select_playbook

logger = logging.getLogger("intelliops.action.consumer")


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
) -> None:
    for diagnosed in iter_models(bus, "situations.diagnosed", "action", DiagnosedSituation):
        if stop_event.is_set():
            break
        situation = diagnosed.situation
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
            # The only outcome-producing path here that had no audit trail. The
            # sink propagates errors by design, and losing the outcome (this runs
            # on a daemon thread) is worse than losing one audit row.
            try:
                gate.write_audit(
                    AuditRecord(
                        actor=_ACTOR,
                        action="escalate",
                        resource=f"situation:{situation.id}",
                        decision="escalated",
                        ts=datetime.now(UTC),
                        correlation_id=situation.id,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - audit failure must never kill the thread
                logger.warning("escalation audit write failed for %s: %s", situation.id, exc)
        else:
            outcome = execute_remediation(
                situation,
                playbook,
                gate,
                remediator,
                health,
                sandbox,
                timeout_seconds,
                poll_interval_seconds,
            )
        publish_model(bus, "remediation.outcomes", outcome)
