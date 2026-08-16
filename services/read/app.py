"""Read service: serves the dashboard's live read model (CQRS read side)."""

from __future__ import annotations

import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common.config import get_settings
from services.base import create_app
from services.read.consumer import run_consumer
from services.read.projection import ReadModel


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    stop_event = threading.Event()
    model = ReadModel(
        max_outcomes=settings.read_outcomes_max,
        ttl_seconds=settings.read_situation_ttl_seconds,
        max_situations=settings.read_situations_max,
    )
    app.state.model = model
    app.state.consumer_stop = stop_event
    app.state.consumer_threads = run_consumer(app.state.bus, model, stop_event)
    try:
        yield
    finally:
        stop_event.set()


app = create_app("read-service")
app.router.lifespan_context = lifespan


@app.get("/situations")
def situations() -> list[dict]:
    model = getattr(app.state, "model", None)
    return model.situations(now_ms=int(time.time() * 1000)) if model else []


@app.get("/outcomes")
def outcomes() -> list[dict]:
    model = getattr(app.state, "model", None)
    return model.outcomes() if model else []


@app.get("/metrics")
def metrics() -> dict:
    model = getattr(app.state, "model", None)
    return model.metrics() if model else ReadModel().metrics()


@app.post("/reset")
def reset() -> dict:
    model = getattr(app.state, "model", None)
    if model is not None:
        model.reset()
    return {"reset": True}
