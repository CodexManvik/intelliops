"""RCA service: enrich a Situation and rank root-cause hypotheses."""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common.config import get_settings
from common.stores import make_stores
from services.base import create_app, db_ready
from services.rca.adapters.context_provider import FileContextProvider
from services.rca.adapters.explanation_provider import make_explanation_provider
from services.rca.consumer import run_consumer

logger = logging.getLogger("intelliops.rca.app")


def _build_reliability_provider(training_store):
    """Best-effort: per-signature worked/total from training records, same
    math as RiverCorrelator/BaseCorrelator.retrain. Returns None if the read
    fails, so RCA ranking degrades gracefully to rule-only behavior."""
    try:
        records = training_store.read_all()
    except Exception:
        logger.exception("failed to read training store; ranking without reliability boost")
        return None

    worked: dict[str, int] = {}
    total: dict[str, int] = {}
    for record in records:
        sig = record.signature
        total[sig] = total.get(sig, 0) + 1
        if record.worked:
            worked[sig] = worked.get(sig, 0) + 1
    reliability = {sig: worked.get(sig, 0) / n for sig, n in total.items()}

    def _reliability_provider(signature: str) -> float:
        return reliability.get(signature, 0.0)

    return _reliability_provider


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    stop_event = threading.Event()
    provider = FileContextProvider(settings.rca_context_path)
    stores = make_stores(settings)
    app.state.db_engine = stores.engine
    store = stores.playbook_store
    audit_sink = stores.audit_sink
    explainer = make_explanation_provider(settings)
    reliability_provider = _build_reliability_provider(stores.training_store)
    thread = threading.Thread(
        target=run_consumer,
        args=(app.state.bus, provider, store, audit_sink, explainer, stop_event),
        kwargs={"reliability_provider": reliability_provider},
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


app = create_app(
    "rca-service",
    readiness=lambda: db_ready(getattr(app.state, "db_engine", None)),
)
app.router.lifespan_context = lifespan
