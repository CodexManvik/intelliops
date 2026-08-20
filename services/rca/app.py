"""RCA service: enrich a Situation and rank root-cause hypotheses."""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common.config import get_settings
from common.stores import make_stores
from services.base import create_app
from services.rca.adapters.context_provider import FileContextProvider
from services.rca.consumer import run_consumer


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    stop_event = threading.Event()
    provider = FileContextProvider(settings.rca_context_path)
    stores = make_stores(settings)
    app.state.db_engine = stores.engine
    store = stores.playbook_store
    audit_sink = stores.audit_sink
    thread = threading.Thread(
        target=run_consumer,
        args=(app.state.bus, provider, store, audit_sink, stop_event),
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


app = create_app("rca-service")
app.router.lifespan_context = lifespan
