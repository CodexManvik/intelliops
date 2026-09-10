"""Bus consumer for governance-service — closes the AI author's learning loop.

Consumes remediation.outcomes and writes each outcome back onto the matching
AuthorDecision (by playbook_id), so the next time the author drafts for the
same situation signature, `get_past_decisions` shows whether its past draft
actually worked. Uses consumer group "governance" — distinct from feedback's
"feedback" group, so both services durably receive every outcome (the bus
fans the same stream out per-group, not once-and-consumed). Runs in a daemon
thread via lifespan (mirrors services/feedback/consumer.py)."""

from __future__ import annotations

import logging
import threading

from common.contracts import RemediationOutcome, RemediationResult
from common.envelope import iter_models

logger = logging.getLogger("intelliops.governance.consumer")


def run_consumer(bus, decision_store, stop_event: threading.Event) -> None:
    for outcome in iter_models(bus, "remediation.outcomes", "governance", RemediationOutcome):
        if stop_event.is_set():
            break
        try:
            outcome_label = "worked" if outcome.result == RemediationResult.SUCCESS else "failed"
            decision_store.update_outcome(outcome.playbook_id, outcome_label, outcome.health_after)
        except Exception:  # bus-resilience: one bad outcome must never kill the thread
            logger.warning(
                "failed to update author decision outcome for playbook %s",
                outcome.playbook_id,
                exc_info=True,
            )
