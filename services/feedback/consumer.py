"""Bus consumer for feedback-service — closes the loop.

Consumes remediation.outcomes, labels each into the training store, and — when a
playbook earns a clean track record — proposes it for graduation exactly once.
Escalations are skipped: nothing was attempted, so they are not evidence about
any runbook and must never reach the training store the whole loop reads from.
The graduator callable performs the promotion (the governance graduate call in
the running service; a fake in tests). Runs in a daemon thread via lifespan."""

from __future__ import annotations

import threading
from collections.abc import Callable

from common.contracts import HitlMode, RemediationOutcome, RemediationResult
from common.envelope import iter_models
from common.idempotency import NullGuard
from services.feedback.graduate import playbook_stats, should_graduate
from services.feedback.label import label_outcome


def run_consumer(
    bus,
    store,
    graduator: Callable[[str], None],
    min_successes: int,
    stop_event: threading.Event,
    guard=None,
    demoter: Callable[[str], None] | None = None,
    count_simulated: bool = False,
) -> None:
    guard = guard if guard is not None else NullGuard()
    graduated: set[str] = set()
    for outcome in iter_models(
        bus, "remediation.outcomes", "feedback", RemediationOutcome, guard=guard, dlq=bus
    ):
        if stop_event.is_set():
            break
        if outcome.result == RemediationResult.ESCALATED:
            continue  # nothing was attempted - not evidence about any runbook
        if outcome.health_after.startswith("interrupted:"):
            # An attempt that died mid-flight is a non-decision: we do not know
            # whether the runbook worked, so it is no evidence either way.
            continue
        store.append(label_outcome(outcome))
        pid = outcome.playbook_id
        if (
            pid
            and demoter is not None
            and outcome.hitl_mode == HitlMode.AUTO
            and outcome.result in (RemediationResult.FAILURE, RemediationResult.ROLLED_BACK)
        ):
            # An auto playbook just failed unattended. Graduation only ever
            # expanded automation; this is the matching contraction, so the next
            # run goes back behind a human.
            demoter(pid)
            graduated.discard(pid)
            continue
        if pid and pid not in graduated:
            stats = playbook_stats(store.read_all(), pid, count_simulated=count_simulated)
            if should_graduate(stats, min_successes):
                graduator(pid)
                graduated.add(pid)
