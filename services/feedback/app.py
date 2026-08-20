"""Feedback service: label outcomes, close the loop, compute metrics."""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from common.config import get_settings
from common.stores import make_stores
from services.base import create_app
from services.feedback.consumer import run_consumer
from services.feedback.metrics import compute_metrics


def _make_graduator(rbac_actor: str = "feedback-service"):
    # In the running service, graduation calls governance's REST endpoint.
    # Inside the docker-compose network, governance listens on its internal
    # PORT=8000 (8005 is only the host-side port mapping). Best-effort, fire-and-
    # forget: a failed graduation is logged by governance's own audit, and the
    # next matching outcome will retry on a fresh process. Kept simple here.
    def graduate(playbook_id: str) -> None:
        try:
            httpx.post(f"http://governance:8000/playbooks/{playbook_id}/graduate",
                       json={"decided_by": rbac_actor}, timeout=5.0)
        except Exception:  # noqa: BLE001, S110 — best-effort; governance's own audit is the record
            pass
    return graduate


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    stop_event = threading.Event()
    stores = make_stores(settings)
    app.state.db_engine = stores.engine
    store = stores.training_store
    app.state.training_store = store
    thread = threading.Thread(
        target=run_consumer,
        args=(app.state.bus, store, _make_graduator(), settings.graduation_min_successes,
              stop_event),
        daemon=True,
    )
    thread.start()
    app.state.consumer_stop = stop_event
    app.state.consumer_thread = thread
    try:
        yield
    finally:
        stop_event.set()
        if stores.engine is not None:
            stores.engine.dispose()


app = create_app("feedback-service")
app.router.lifespan_context = lifespan


@app.get("/metrics")
def metrics() -> dict:
    store = getattr(app.state, "training_store", None)
    records = store.read_all() if store is not None else []
    return compute_metrics(records)
