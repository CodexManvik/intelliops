"""RCA service: enrich a Situation and rank root-cause hypotheses."""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common.config import get_settings
from services.base import create_app
from services.governance.adapters.audit_sink import FileAuditSink
from services.governance.adapters.playbook_store import FilePlaybookStore
from services.rca.adapters.context_provider import FileContextProvider
from services.rca.consumer import run_consumer


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    stop_event = threading.Event()
    provider = FileContextProvider(settings.rca_context_path)
    store = FilePlaybookStore(settings.playbook_store_path)
    audit_sink = FileAuditSink(settings.audit_store_path)
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


app = create_app("rca-service")
app.router.lifespan_context = lifespan
