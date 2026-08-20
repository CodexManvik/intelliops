"""Bus consumer for feedback-service — closes the loop.

Consumes remediation.outcomes, labels each into the training store, and — when a
playbook earns a clean track record — proposes it for graduation exactly once.
The graduator callable performs the promotion (the governance graduate call in
the running service; a fake in tests). Runs in a daemon thread via lifespan."""

from __future__ import annotations

import threading
from collections.abc import Callable

from common.contracts import RemediationOutcome
from common.envelope import iter_models
from services.feedback.graduate import playbook_stats, should_graduate
from services.feedback.label import label_outcome


def run_consumer(
    bus, store, graduator: Callable[[str], None], min_successes: int, stop_event: threading.Event
) -> None:
    graduated: set[str] = set()
    for outcome in iter_models(bus, "remediation.outcomes", "feedback", RemediationOutcome):
        if stop_event.is_set():
            break
        store.append(label_outcome(outcome))
        pid = outcome.playbook_id
        if pid and pid not in graduated:
            stats = playbook_stats(store.read_all(), pid)
            if should_graduate(stats, min_successes):
                graduator(pid)
                graduated.add(pid)
