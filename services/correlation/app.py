"""Correlation service: anomaly detection + event clustering -> Situation."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from common.config import get_settings
from common.envelope import publish_model
from common.evidence import real_records
from common.idempotency import make_guard
from common.stores import make_stores
from common.supervise import start_supervised
from services.base import create_app, db_ready
from services.correlation.adapters import make_correlator
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
    training_store=None,
    reliability_refresh_seconds: float = 60.0,
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

    It also refreshes the reliability map suppression reads, on its own
    schedule. That map used to be loaded once at boot, so a fix that started
    working was only recognised after a restart.
    """
    last_snapshot = last_refresh = time.monotonic()
    while not stop_event.wait(period_seconds):
        for emitted in engine.flush_all():
            publish_model(bus, "situations.detected", emitted)
        _drain_suppressed(bus, engine)
        now = time.monotonic()
        if now - last_snapshot >= snapshot_period:
            _snapshot_baseline_once(engine, baseline_store)
            last_snapshot = now
        if training_store is not None and now - last_refresh >= reliability_refresh_seconds:
            _refresh_reliability(engine, training_store)
            last_refresh = now


def _training_rows(training_store) -> list[dict]:
    """Real outcomes only (common/evidence.py): a dry run always "works", so it
    is no evidence a signature is reliably fixed."""
    return [r.model_dump() for r in real_records(training_store.read_all())]


def _refresh_reliability(engine, training_store) -> None:
    """Best-effort: a failed read keeps the current map."""
    try:
        engine.retrain(_training_rows(training_store))
    except Exception as exc:  # noqa: BLE001 - refresh again next period
        logger.warning("reliability refresh failed (keeping the current map): %s", exc)


def _reload_baseline(engine, baseline_store, training_records: list[dict]) -> None:
    """On boot: restore the z-score baseline + recover reliability. Best-effort."""
    if baseline_store is not None:
        try:
            engine.load(baseline_store.load_all())
        except Exception as exc:  # noqa: BLE001 — a failed reload just means a cold start
            logger.warning("baseline reload failed, starting cold: %s", exc)
    if training_records:
        engine._correlator.retrain(training_records)


def _reload_model(engine, model_store, name: str = "trained") -> None:
    """On boot: restore a persisted trained model onto the correlator. Best-effort.

    Only correlators exposing load_model (the trained kind) can consume a blob; a
    river/robust correlator has no model to restore, so this is a no-op for them.
    Any failure (no store, no blob, feature drift, load error) leaves the
    correlator cold — it simply re-fits from live data."""
    if model_store is None:
        return
    load_model = getattr(engine._correlator, "load_model", None)
    if load_model is None:
        return
    try:
        blob = model_store.load_latest(name)
        if blob:
            load_model(blob)
    except Exception as exc:  # noqa: BLE001 — a failed model reload just means cold start
        logger.warning("model reload failed, starting cold: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    stop_event = threading.Event()
    engine = CorrelationEngine(
        make_correlator(settings),
        window_seconds=settings.correlation_window_seconds,
        group_by=settings.correlation_group_by,
        min_events=settings.correlation_min_events,
        # Both were settings the service never passed: the threshold was dead
        # config and the engine always used its 0.8 default.
        suppress_threshold=settings.reliability_suppress_threshold,
        suppress_min_samples=settings.reliability_suppress_min_samples,
        suppress_mode=settings.suppression_mode,
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
    model_store = None
    training_store = None
    training_records: list[dict] = []
    try:
        stores = make_stores(settings)
        app.state.db_engine = stores.engine
        baseline_store = stores.baseline_store
        model_store = getattr(stores, "model_store", None)
        training_store = stores.training_store
        training_records = _training_rows(training_store)
    except Exception as exc:  # noqa: BLE001 — a failed boot-load just means a cold start
        logger.warning("store reload failed, starting cold: %s", exc)
    # The durable `correlation_baseline` table only understands the river z-score's
    # scalar codec (mean/variance/count). robust/trained carry a per-(metric,bucket)
    # window whose snapshot rows don't fit that schema, so persisting them would
    # KeyError on every flush (best-effort-swallowed, but noisy). Their baseline
    # stays in-process only (ADR-019); skip the durable store so the flusher and
    # boot reload are clean no-ops rather than logging a warning every period.
    if settings.correlator_kind != "river":
        baseline_store = None
    _reload_baseline(engine, baseline_store, training_records)
    _reload_model(engine, model_store)
    app.state.baseline_store = baseline_store
    app.state.model_store = model_store
    thread = start_supervised(
        "correlation-consumer",
        run_consumer,
        stop_event,
        (app.state.bus, engine, stop_event, make_guard(settings, app.state.bus)),
    )
    flusher = start_supervised(
        "correlation-flusher",
        run_flusher,
        stop_event,
        (
            app.state.bus,
            engine,
            settings.correlation_window_seconds,
            stop_event,
            baseline_store,
            settings.baseline_snapshot_seconds,
            training_store,
            settings.reliability_refresh_seconds,
        ),
    )
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
app.router.lifespan_context = lifespan


@app.post("/reset-baseline")
def reset_baseline() -> dict:
    engine = getattr(app.state, "engine", None)
    if engine is not None:
        engine.reset()
    db = getattr(app.state, "db_engine", None)
    if db is not None:
        with db.begin() as conn:
            conn.execute(text("DELETE FROM correlation_baseline"))
    return {"reset": True}


@app.post("/retrain")
def retrain() -> dict:
    """The REAL fit trigger for the trained correlator.

    retrain()-at-boot has an empty feature deque and never fits; this endpoint is
    what the demo/benchmark fires once the correlator has observed enough live
    events. It fits the model from the buffered features, then persists the fresh
    artifact best-effort (a failed save just means the next boot cold-starts and
    re-fits). A river/robust correlator has no fit(), so this is a graceful no-op
    ({"fitted": False}) for the non-trained kinds."""
    engine = getattr(app.state, "engine", None)
    fit = getattr(getattr(engine, "_correlator", None), "fit", None)
    if fit is None:
        return {"fitted": False, "persisted": False}
    fitted = bool(fit())
    persisted = False
    if fitted:
        model_store = getattr(app.state, "model_store", None)
        serialize = getattr(engine._correlator, "serialize", None)
        if model_store is not None and serialize is not None:
            try:
                blob = serialize()
                if blob:
                    model_store.save("trained", blob)
                    persisted = True
            except Exception as exc:  # noqa: BLE001 — persistence is best-effort
                logger.warning("model persist failed after fit: %s", exc)
    return {"fitted": fitted, "persisted": persisted}


@app.get("/baseline")
def baseline() -> dict:
    """What the detector currently believes "normal" looks like.

    The two correlators snapshot different shapes. RiverCorrelator carries a
    running mean/variance/count per metric; RobustCorrelator carries the raw
    per-(metric, hour-bucket) window and derives median/MAD from it on demand.

    This endpoint only ever understood the first shape, so under `robust` -- the
    live posture's correlator -- every row came back mean=None and
    std=sqrt(0)=0.0, and the console's "live baselines" table rendered a
    column of dashes beside a column of zeros. Read whichever shape the running
    correlator actually produces, and say which statistic it is.
    """
    settings = get_settings()
    engine = getattr(app.state, "engine", None)
    rows = engine.snapshot() if engine is not None else []
    correlator = getattr(engine, "_correlator", None)

    # Decide from the correlator, not from the rows: right after a reset there
    # are no rows, and reporting "mean/stddev" for an empty robust table is the
    # same mislabelling in miniature. `_windows` is what the window-based
    # correlators (robust, and trained layered over it) have and river does not.
    if hasattr(correlator, "_windows"):
        # Robust: pool the per-bucket sample counts, and take the statistics
        # from the correlator's own median/MAD summary.
        counts: dict[str, int] = {}
        for r in rows:
            name = r.get("metric_name")
            counts[name] = counts.get(name, 0) + int(r.get("n") or 0)
        summary = correlator.baseline_snapshot() if hasattr(correlator, "baseline_snapshot") else {}
        baselines = [
            {
                "metric_name": name,
                "mean": stat.get("mean"),
                "std": stat.get("std"),
                "count": counts.get(name),
            }
            for name, stat in summary.items()
        ]
        statistic = "median/MAD"
    else:
        baselines = [
            {
                "metric_name": r.get("metric_name"),
                "mean": r.get("mean"),
                "std": (r.get("variance") or 0.0) ** 0.5,
                "count": r.get("count"),
            }
            for r in rows
        ]
        statistic = "mean/stddev"
    return {
        "correlator_kind": settings.correlator_kind,
        "statistic": statistic,
        "baselines": baselines,
    }
