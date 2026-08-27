"""RCA service: enrich a Situation and rank root-cause hypotheses."""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from common.config import get_settings
from common.stores import make_stores
from services.base import create_app, db_ready
from services.rca.adapters.context_provider import FileContextProvider
from services.rca.adapters.explanation_provider import (
    OpenAICompatibleExplanationProvider,
    TemplateExplanationProvider,
    make_explanation_provider,
)
from services.rca.consumer import run_consumer
from services.rca.provider_holder import ProviderHolder

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
    holder = ProviderHolder(make_explanation_provider(settings))
    app.state.provider_holder = holder
    reliability_provider = _build_reliability_provider(stores.training_store)
    thread = threading.Thread(
        target=run_consumer,
        args=(app.state.bus, provider, store, audit_sink, holder.get, stop_event),
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


class LlmConfig(BaseModel):
    endpoint: str = ""
    api_key: str = ""
    model: str = "gpt-4o-mini"
    timeout_seconds: float = 10.0


def _redact(endpoint: str) -> str:
    if not endpoint:
        return ""
    from urllib.parse import urlparse

    p = urlparse(endpoint)
    return f"{p.scheme}://{p.hostname}" + (f":{p.port}" if p.port else "")


def _state(holder) -> dict:
    prov = holder.get()
    is_llm = isinstance(prov, OpenAICompatibleExplanationProvider)
    return {
        "provider": "openai-compatible" if is_llm else "template",
        "endpoint_configured": is_llm,
        "endpoint": _redact(getattr(prov, "_base", "")),
        "model": getattr(prov, "_model", get_settings().llm_explanation_model),
        "last_probe": holder.last_probe,
    }


@app.get("/config/llm")
def get_llm_config() -> dict:
    return _state(app.state.provider_holder)


# POST /config/llm carries the api_key; it is auth-gated automatically by
# create_app's default exempt predicate (only /health and /ready are exempt),
# so AUTH_MODE=token protects this route with no extra work here.
@app.post("/config/llm")
def set_llm_config(cfg: LlmConfig) -> dict:
    holder = app.state.provider_holder
    if cfg.endpoint:
        holder.set(
            OpenAICompatibleExplanationProvider(
                base_url=cfg.endpoint,
                model=cfg.model,
                api_key=cfg.api_key,
                timeout_seconds=cfg.timeout_seconds,
            )
        )
    else:
        holder.set(TemplateExplanationProvider())
    return _state(holder)  # never echoes api_key


@app.post("/config/llm/test")
def test_llm_config(cfg: LlmConfig) -> dict:
    import time
    from datetime import UTC, datetime

    from common.contracts import EnrichmentContext, RootCauseHypothesis, Situation, SituationStatus

    if not cfg.endpoint:
        return {"ok": False, "error": "no endpoint configured"}
    provider = OpenAICompatibleExplanationProvider(
        base_url=cfg.endpoint,
        model=cfg.model,
        api_key=cfg.api_key,
        timeout_seconds=cfg.timeout_seconds,
    )
    hyp = RootCauseHypothesis(
        situation_id="probe", description="probe", confidence=0.5, evidence=["probe"]
    )
    sit = Situation(
        id="probe",
        status=SituationStatus.DETECTED,
        severity="low",
        first_seen=datetime.now(UTC),
        last_seen=datetime.now(UTC),
        signature="probe",
    )
    template = TemplateExplanationProvider().explain(hyp, EnrichmentContext(), sit)
    start = time.monotonic()
    text = provider.explain(hyp, EnrichmentContext(), sit)
    latency_ms = int((time.monotonic() - start) * 1000)
    ok = text != template  # provider falls back to template on ANY failure
    probe = {"ok": ok, "model": cfg.model, "latency_ms": latency_ms}
    if not ok:
        probe["error"] = (
            "endpoint unreachable or returned no usable content (fell back to template)"
        )
    app.state.provider_holder.set_last_probe(probe)
    return probe
