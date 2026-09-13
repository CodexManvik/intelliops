"""Bus consumer for governance-service — closes the AI author's learning loop.

Consumes remediation.outcomes and writes each outcome back onto the matching
AuthorDecision (by playbook_id), so the next time the author drafts for the
same situation signature, `get_past_decisions` shows whether its past draft
actually worked. Escalations are skipped — the draft was never executed, so the
decision honestly stays at outcome=unknown rather than being replayed to the
author as a failure of its own work. Uses consumer group "governance" — distinct
from feedback's "feedback" group, so both services durably receive every outcome
(the bus fans the same stream out per-group, not once-and-consumed). Runs in a daemon
thread via lifespan (mirrors services/feedback/consumer.py)."""

from __future__ import annotations

import logging
import threading

from common.contracts import RemediationOutcome, RemediationResult
from common.envelope import iter_models
from common.idempotency import NullGuard

logger = logging.getLogger("intelliops.governance.consumer")


def run_consumer(bus, decision_store, stop_event: threading.Event, guard=None) -> None:
    guard = guard if guard is not None else NullGuard()
    for outcome in iter_models(
        bus, "remediation.outcomes", "governance", RemediationOutcome, guard=guard, dlq=bus
    ):
        if stop_event.is_set():
            break
        if outcome.result == RemediationResult.ESCALATED:
            continue  # never ran - leave the author's decision at outcome=unknown
        try:
            outcome_label = "worked" if outcome.result == RemediationResult.SUCCESS else "failed"
            decision_store.update_outcome(outcome.playbook_id, outcome_label, outcome.health_after)
        except Exception:  # bus-resilience: one bad outcome must never kill the thread
            logger.warning(
                "failed to update author decision outcome for playbook %s",
                outcome.playbook_id,
                exc_info=True,
            )
