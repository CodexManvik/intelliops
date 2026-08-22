"""Correlation service: anomaly detection + event clustering -> Situation."""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common.config import get_settings
from common.envelope import publish_model
from services.base import create_app
from services.correlation.adapters.river_correlator import RiverCorrelator
from services.correlation.consumer import _drain_suppressed, run_consumer
from services.correlation.engine import CorrelationEngine


def run_flusher(
    bus, engine: CorrelationEngine, period_seconds: float, stop_event: threading.Event
) -> None:
    """Periodically collapse the buffered window into a Situation.

    On a continuous stream the consumer's own span-check only fires when a NEW
    anomalous event arrives; once the baseline learns the elevated value, later
    samples score below threshold and no trigger arrives, so a real incident
    could sit buffered indefinitely. This timer closes the window on elapsed
    wall-clock time. flush() is a no-op when the buffer is empty and is
    lock-guarded against the consumer's add().

    A timer-triggered flush can suppress a situation just as the consumer's own
    flush can (closed-loop signatures don't care which code path collapsed the
    window), so this must drain and publish suppressed situations too — otherwise
    a suppression that only ever happens on a timer-flush is silently lost.
    """
    while not stop_event.wait(period_seconds):
        emitted = engine.flush()
        if emitted is not None:
            publish_model(bus, "situations.detected", emitted)
        _drain_suppressed(bus, engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    stop_event = threading.Event()
    engine = CorrelationEngine(
        RiverCorrelator(
            z_threshold=settings.correlation_z_threshold,
            warmup_samples=settings.correlation_warmup_samples,
        ),
        window_seconds=settings.correlation_window_seconds,
    )
    app.state.engine = engine
    thread = threading.Thread(
        target=run_consumer, args=(app.state.bus, engine, stop_event), daemon=True
    )
    thread.start()
    flusher = threading.Thread(
        target=run_flusher,
        args=(app.state.bus, engine, settings.correlation_window_seconds, stop_event),
        daemon=True,
    )
    flusher.start()
    app.state.consumer_stop = stop_event
    app.state.consumer_thread = thread
    app.state.flusher_thread = flusher
    try:
        yield
    finally:
        stop_event.set()


app = create_app("correlation-service")
app.router.lifespan_context = lifespan


@app.post("/reset-baseline")
def reset_baseline() -> dict:
    engine = getattr(app.state, "engine", None)
    if engine is not None:
        engine.reset()
    return {"reset": True}
