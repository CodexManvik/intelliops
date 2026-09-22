"""Bus consumer loop for correlation-service.

Consumes normalized telemetry, feeds it to the windowed correlation engine,
and publishes each emitted Situation to situations.detected. Runs in a daemon
thread started by the FastAPI lifespan; a stop_event allows clean shutdown.
"""

from __future__ import annotations

import logging
import threading

from common.contracts import TelemetryEvent
from common.envelope import iter_models, publish_model
from common.idempotency import NullGuard
from services.correlation.engine import CorrelationEngine

logger = logging.getLogger(__name__)


def _snapshot_baseline_once(engine, baseline_store) -> None:
    """Best-effort: snapshot the baseline; log and swallow any error (never raise)."""
    if baseline_store is None:
        return
    try:
        baseline_store.save(engine.snapshot())
    except Exception as exc:  # noqa: BLE001 — best-effort; a missed snapshot is recoverable
        logger.warning("baseline snapshot failed (will retry next period): %s", exc)


def _drain_suppressed(bus, engine: CorrelationEngine) -> None:
    while (s := engine.pop_suppressed()) is not None:
        # The record of every suppression, whatever it did to the Situation. In
        # quiet mode the Situation was ALSO emitted for remediation; in drop mode
        # this line and the situations.suppressed event are all that remain.
        logger.info(
            "suppressed situation %s (signature %s, service %s): %s",
            s.id,
            s.signature,
            next(
                (e.labels.get("service") for e in s.member_events if e.labels.get("service")), "?"
            ),
            "handling quietly" if s.handling == "quiet" else "dropped",
            extra={"situation_id": s.id, "signature": s.signature, "handling": s.handling},
        )
        publish_model(bus, "situations.suppressed", s)


def run_consumer(bus, engine: CorrelationEngine, stop_event: threading.Event, guard=None) -> None:
    guard = guard if guard is not None else NullGuard()
    for event in iter_models(
        bus, "telemetry.raw", "correlation", TelemetryEvent, guard=guard, dlq=bus
    ):
        if stop_event.is_set():
            break
        emitted = engine.add(event)
        if emitted is not None:
            publish_model(bus, "situations.detected", emitted)
        _drain_suppressed(bus, engine)
    # Finite/interrupted stream: publish every final buffered Situation. With
    # grouping on there can be more than one, and flush() would strand the rest.
    for tail in engine.flush_all():
        publish_model(bus, "situations.detected", tail)
    _drain_suppressed(bus, engine)
