"""Bus consumer loop for correlation-service.

Consumes normalized telemetry, feeds it to the windowed correlation engine,
and publishes each emitted Situation to situations.detected. Runs in a daemon
thread started by the FastAPI lifespan; a stop_event allows clean shutdown.
"""

from __future__ import annotations

import logging
import threading

from common.contracts import Situation, TelemetryEvent
from common.envelope import iter_models, publish_model
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
    s = engine.pop_suppressed()
    if s is not None:
        publish_model(bus, "situations.suppressed", s)


def run_consumer(bus, engine: CorrelationEngine, stop_event: threading.Event) -> None:
    for event in iter_models(bus, "telemetry.raw", "correlation", TelemetryEvent):
        if stop_event.is_set():
            break
        emitted = engine.add(event)
        if emitted is not None:
            publish_model(bus, "situations.detected", emitted)
        _drain_suppressed(bus, engine)
    # Finite/interrupted stream: publish any final buffered Situation.
    tail: Situation | None = engine.flush()
    if tail is not None:
        publish_model(bus, "situations.detected", tail)
    _drain_suppressed(bus, engine)
