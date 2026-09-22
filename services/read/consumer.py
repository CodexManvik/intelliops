"""Read-model consumer: tail the event stream, keep the projection current.

Subscribes to the three topics the dashboard reads. On first start against a
fresh stream (no consumer group yet), each topic is read from the beginning
and the full projection is built up. On a process restart where the consumer
group already exists, consumption resumes from the last acknowledged entry —
the stream is NOT re-read from the beginning (see RedisBus.consume in
common/bus.py, which creates the group at id="0" only when it doesn't already
exist). A fresh `docker compose up` that provisions a new Redis instance has
no pre-existing group, so that case does rebuild the projection from scratch.
One thread per topic keeps the loop simple; all share the same ReadModel
instance.
"""

from __future__ import annotations

import threading

from common.contracts import DiagnosedSituation, RemediationOutcome, Situation
from common.envelope import iter_models
from common.supervise import start_supervised
from services.read.projection import ReadModel

_GROUP = "read-model"

_TOPICS = [
    ("situations.detected", Situation, "apply_detected"),
    ("situations.diagnosed", DiagnosedSituation, "apply_diagnosed"),
    ("remediation.outcomes", RemediationOutcome, "apply_outcome"),
    ("situations.suppressed", Situation, "apply_suppressed"),
]


def _run_topic(
    bus,
    model: ReadModel,
    topic: str,
    model_type,
    method: str,
    stop_event: threading.Event,
    dlq=None,
) -> None:
    apply = getattr(model, method)
    # No idempotency guard here on purpose: re-applying a projection event is
    # harmless, and a durable guard would suppress exactly the replay the
    # cold-start rebuild depends on. The DLQ still applies - an undecodable
    # payload must not kill this thread and stall the console.
    for parsed in iter_models(bus, topic, _GROUP, model_type, dlq=dlq):
        if stop_event.is_set():
            break
        apply(parsed)


def run_consumer(bus, model: ReadModel, stop_event: threading.Event) -> list[threading.Thread]:
    threads = []
    for topic, model_type, method in _TOPICS:
        t = start_supervised(
            f"read-{topic}",
            _run_topic,
            stop_event,
            (bus, model, topic, model_type, method, stop_event, bus),
        )
        threads.append(t)
    return threads
