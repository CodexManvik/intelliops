"""Bus consumer for action-service.

Consumes situations.diagnosed, selects a playbook, runs it through the
remediation gates, and publishes a RemediationOutcome on remediation.outcomes.
When no playbook matches, emits a skipped outcome so Slice-4 feedback still sees
the decision. Runs in a daemon thread started by the FastAPI lifespan."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from common.contracts import DiagnosedSituation, RemediationOutcome, RemediationResult
from common.envelope import iter_models, publish_model
from services.action.remediate import execute_remediation
from services.action.select import select_playbook


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
            outcome = RemediationOutcome(
                situation_id=situation.id,
                playbook_id=diagnosed.suggested_runbook_id or "",
                result=RemediationResult.FAILURE,
                health_after="skipped:no-playbook",
                ts=datetime.now(UTC),
            )
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
