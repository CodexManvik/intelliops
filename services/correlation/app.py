"""Correlation service: anomaly detection + event clustering -> Situation."""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from services.base import create_app
from services.correlation.adapters.river_correlator import RiverCorrelator
from services.correlation.consumer import run_consumer
from services.correlation.engine import CorrelationEngine


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop_event = threading.Event()
    engine = CorrelationEngine(RiverCorrelator())
    thread = threading.Thread(
        target=run_consumer, args=(app.state.bus, engine, stop_event), daemon=True
    )
    thread.start()
    app.state.consumer_stop = stop_event
    app.state.consumer_thread = thread
    try:
        yield
    finally:
        stop_event.set()


app = create_app("correlation-service")
app.router.lifespan_context = lifespan
