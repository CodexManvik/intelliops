"""Correlation service: anomaly detection + event clustering -> Situation."""

from __future__ import annotations

import logging
import logging
import threading
import time
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common.config import get_settings
from common.envelope import publish_model
from common.stores import make_stores
from services.base import create_app, db_ready
from common.stores import make_stores
from services.base import create_app, db_ready
from services.correlation.adapters.river_correlator import RiverCorrelator
from services.correlation.consumer import (
    _drain_suppressed,
    _snapshot_baseline_once,
    run_consumer,
)
from services.correlation.consumer import (
    _drain_suppressed,
    _snapshot_baseline_once,
    run_consumer,
)
from services.correlation.engine import CorrelationEngine

logger = logging.getLogger(__name__)


def run_flusher(
    bus,
    engine: CorrelationEngine,
    period_seconds: float,
    stop_event: threading.Event,
    baseline_store=None,
    snapshot_period: float = 30.0,
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

    This thread also piggybacks the periodic baseline snapshot. Because the loop
    wakes on the (possibly shorter) situation-flush cadence, the snapshot runs on
    its own elapsed-time schedule tracked with time.monotonic() rather than once
    per wake. The snapshot is best-effort (_snapshot_baseline_once never raises),
    so a persistence hiccup can never crash this flusher.
    """
    last_snapshot = time.monotonic()

    This thread also piggybacks the periodic baseline snapshot. Because the loop
    wakes on the (possibly shorter) situation-flush cadence, the snapshot runs on
    its own elapsed-time schedule tracked with time.monotonic() rather than once
    per wake. The snapshot is best-effort (_snapshot_baseline_once never raises),
    so a persistence hiccup can never crash this flusher.
    """
    last_snapshot = time.monotonic()
    while not stop_event.wait(period_seconds):
        emitted = engine.flush()
        if emitted is not None:
            publish_model(bus, "situations.detected", emitted)
        _drain_suppressed(bus, engine)
        now = time.monotonic()
        if now - last_snapshot >= snapshot_period:
            _snapshot_baseline_once(engine, baseline_store)
            last_snapshot = now


def _reload_baseline(engine, baseline_store, training_records: list[dict]) -> None:
    """On boot: restore the z-score baseline + recover reliability. Best-effort."""
    if baseline_store is not None:
        try:
            engine.load(baseline_store.load_all())
        except Exception as exc:  # noqa: BLE001 — a failed reload just means a cold start
            logger.warning("baseline reload failed, starting cold: %s", exc)
    if training_records:
        engine._correlator.retrain(training_records)
        now = time.monotonic()
        if now - last_snapshot >= snapshot_period:
            _snapshot_baseline_once(engine, baseline_store)
            last_snapshot = now


def _reload_baseline(engine, baseline_store, training_records: list[dict]) -> None:
    """On boot: restore the z-score baseline + recover reliability. Best-effort."""
    if baseline_store is not None:
        try:
            engine.load(baseline_store.load_all())
        except Exception as exc:  # noqa: BLE001 — a failed reload just means a cold start
            logger.warning("baseline reload failed, starting cold: %s", exc)
    if training_records:
        engine._correlator.retrain(training_records)


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
    # Reload-on-boot: restore the durable baseline + reliability BEFORE the
    # consumer thread starts, so the first events are scored against the
    # recovered state (no cold-start blackout). In file mode baseline_store is
    # None and the reload is a no-op; the training-record retrain still runs.
    #
    # Reload-on-boot is best-effort: a DB-unavailable boot cold-starts (empty
    # baseline + reliability) rather than crashing (ADR-015). make_stores() can
    # connect in postgres mode (PostgresPlaybookStore seeds on construction), so
    # it must be inside the guard too.
    baseline_store = None
    training_records: list[dict] = []
    try:
        stores = make_stores(settings)
        app.state.db_engine = stores.engine
        baseline_store = stores.baseline_store
        training_records = [r.model_dump() for r in stores.training_store.read_all()]
    except Exception as exc:  # noqa: BLE001 — a failed boot-load just means a cold start
        logger.warning("store reload failed, starting cold: %s", exc)
    _reload_baseline(engine, baseline_store, training_records)
    app.state.baseline_store = baseline_store
    # Reload-on-boot: restore the durable baseline + reliability BEFORE the
    # consumer thread starts, so the first events are scored against the
    # recovered state (no cold-start blackout). In file mode baseline_store is
    # None and the reload is a no-op; the training-record retrain still runs.
    #
    # Reload-on-boot is best-effort: a DB-unavailable boot cold-starts (empty
    # baseline + reliability) rather than crashing (ADR-015). make_stores() can
    # connect in postgres mode (PostgresPlaybookStore seeds on construction), so
    # it must be inside the guard too.
    baseline_store = None
    training_records: list[dict] = []
    try:
        stores = make_stores(settings)
        app.state.db_engine = stores.engine
        baseline_store = stores.baseline_store
        training_records = [r.model_dump() for r in stores.training_store.read_all()]
    except Exception as exc:  # noqa: BLE001 — a failed boot-load just means a cold start
        logger.warning("store reload failed, starting cold: %s", exc)
    _reload_baseline(engine, baseline_store, training_records)
    app.state.baseline_store = baseline_store
    thread = threading.Thread(
        target=run_consumer, args=(app.state.bus, engine, stop_event), daemon=True
    )
    thread.start()
    flusher = threading.Thread(
        target=run_flusher,
        args=(
            app.state.bus,
            engine,
            settings.correlation_window_seconds,
            stop_event,
            baseline_store,
            settings.baseline_snapshot_seconds,
        ),
        args=(
            app.state.bus,
            engine,
            settings.correlation_window_seconds,
            stop_event,
            baseline_store,
            settings.baseline_snapshot_seconds,
        ),
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


app = create_app(
    "correlation-service",
    readiness=lambda: db_ready(getattr(app.state, "db_engine", None)),
)
app = create_app(
    "correlation-service",
    readiness=lambda: db_ready(getattr(app.state, "db_engine", None)),
)
app.router.lifespan_context = lifespan


@app.post("/reset-baseline")
def reset_baseline() -> dict:
    engine = getattr(app.state, "engine", None)
    if engine is not None:
        engine.reset()
    return {"reset": True}
