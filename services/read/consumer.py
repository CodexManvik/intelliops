"""Read-model consumer: tail the event stream, keep the projection current.

Subscribes to the three topics the dashboard reads. Reads each topic from the
stream's beginning on start (rebuild-on-restart), then tails live. One thread
per topic keeps the loop simple; all share the same ReadModel instance.
"""

from __future__ import annotations

import threading

from common.contracts import DiagnosedSituation, RemediationOutcome, Situation
from common.envelope import iter_models
from services.read.projection import ReadModel

_GROUP = "read-model"

_TOPICS = [
    ("situations.detected", Situation, "apply_detected"),
    ("situations.diagnosed", DiagnosedSituation, "apply_diagnosed"),
    ("remediation.outcomes", RemediationOutcome, "apply_outcome"),
]


def _run_topic(bus, model: ReadModel, topic: str, model_type, method: str,
               stop_event: threading.Event) -> None:
    apply = getattr(model, method)
    for parsed in iter_models(bus, topic, _GROUP, model_type):
        if stop_event.is_set():
            break
        apply(parsed)


def run_consumer(bus, model: ReadModel, stop_event: threading.Event) -> list[threading.Thread]:
    threads = []
    for topic, model_type, method in _TOPICS:
        t = threading.Thread(target=_run_topic,
                             args=(bus, model, topic, model_type, method, stop_event),
                             daemon=True)
        t.start()
        threads.append(t)
    return threads
