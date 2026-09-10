"""Ingestion service: normalize + dedup telemetry onto the bus.

Two ingress modes: push (POST /ingest) always works; when TELEMETRY_MODE is
'prometheus' a background poll loop pulls from a PrometheusSource and publishes
to telemetry.raw. 'file' (default) leaves the push-only behavior untouched so
tests are unaffected.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from pydantic import BaseModel

from common.config import get_settings
from common.envelope import publish_model
from services.base import create_app
from services.ingestion.normalize import normalize

logger = logging.getLogger("intelliops.ingestion")


def run_poll_loop(bus, source, interval: float, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        # Never let a transient error (Prometheus unreachable, or Redis down when
        # publishing) kill the poll thread — that silently stops ingestion. Log
        # and continue to the next interval; the next poll re-tries.
        try:
            for event in source.poll():
                publish_model(bus, "telemetry.raw", event)
        except Exception:  # resilience: keep polling across transient failures
            logger.warning("ingestion poll iteration failed; retrying next interval", exc_info=True)
        if stop_event.is_set():
            break
        time.sleep(interval)


def _make_source(settings):
    if settings.telemetry_mode == "prometheus":
        from services.ingestion.adapters.prometheus_source import PrometheusSource

        return PrometheusSource(settings.prometheus_url, settings.prometheus_query)
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    stop_event = threading.Event()
    source = _make_source(settings)
    thread = None
    if source is not None:
        thread = threading.Thread(
            target=run_poll_loop,
            args=(app.state.bus, source, settings.telemetry_poll_seconds, stop_event),
            daemon=True,
        )
        thread.start()
    app.state.poll_stop = stop_event
    app.state.poll_thread = thread
    try:
        yield
    finally:
        stop_event.set()


app = create_app("ingestion-service")
app.router.lifespan_context = lifespan


class IngestBatch(BaseModel):
    events: list[dict]


@app.post("/ingest")
def ingest(batch: IngestBatch) -> dict[str, int]:
    accepted = 0
    for raw in batch.events:
        if "ts" not in raw:
            raw = {**raw, "ts": datetime.now(UTC).isoformat()}
        event = normalize(raw)
        publish_model(app.state.bus, "telemetry.raw", event)
        accepted += 1
    return {"accepted": accepted}
