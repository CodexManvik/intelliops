"""RCA service: enrich a Situation and rank root-cause hypotheses."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from common.config import get_settings
from common.idempotency import make_guard
from common.stores import make_stores
from common.supervise import start_supervised
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


def _make_runbook_selector(settings):
    """Selects the embedding-backed RunbookSelector IFF runbook_selector_mode
    is "embedding"; NullRunbookSelector (no semantic fallback, today's
    behavior) otherwise. Mirrors make_explanation_provider's opt-in-via-config
    shape. Lazy-imports EmbeddingRunbookSelector so importing this module
    never requires the embedding dependency/model when the mode is off."""
    from services.rca.adapters.runbook_selector import NullRunbookSelector

    if settings.runbook_selector_mode == "embedding":
        from services.rca.adapters.runbook_selector import EmbeddingRunbookSelector

        return EmbeddingRunbookSelector(
            model_name=settings.runbook_selector_model,
            threshold=settings.runbook_selector_threshold,
        )
    return NullRunbookSelector()


class ReliabilityProvider:
    """(signature, runbook_id) -> worked/total, re-read from the training store.

    Two things were wrong with the boot-time closure this replaces:

    - It was built once in the lifespan, so every outcome recorded after RCA
      started was invisible to ranking until the pod restarted.
    - It was keyed on signature alone, and rank_hypotheses added that one number
      to every runbook-bearing hypothesis alike - which cannot change their order.

    Refreshes lazily, at most every `refresh_seconds`, on the consumer thread
    that calls it. Best-effort: a failed read keeps the last good table (or
    none), so ranking degrades to rule-only instead of raising.
    """

    def __init__(self, training_store, refresh_seconds: float = 60.0, clock=time.monotonic):
        self._store = training_store
        self._refresh = refresh_seconds
        self._clock = clock
        self._loaded_at: float | None = None
        self._by_runbook: dict[tuple[str, str], float] = {}
        self._by_signature: dict[str, float] = {}

    def _maybe_reload(self) -> None:
        now = self._clock()
        if self._loaded_at is not None and now - self._loaded_at < self._refresh:
            return
        self._loaded_at = now
        try:
            records = self._store.read_all()
        except Exception:
            logger.exception("failed to read training store; keeping the last reliability table")
            return
        worked: dict = {}
        total: dict = {}
        for r in records:
            for key in ((r.signature, r.playbook_id), r.signature):
                total[key] = total.get(key, 0) + 1
                if r.worked:
                    worked[key] = worked.get(key, 0) + 1
        ratio = {k: worked.get(k, 0) / n for k, n in total.items()}
        self._by_runbook = {k: v for k, v in ratio.items() if isinstance(k, tuple)}
        self._by_signature = {k: v for k, v in ratio.items() if isinstance(k, str)}

    def __call__(self, signature: str, runbook_id: str | None = None) -> float:
        self._maybe_reload()
        if runbook_id is None:
            return self._by_signature.get(signature, 0.0)
        return self._by_runbook.get((signature, runbook_id), 0.0)


def _build_reliability_provider(training_store, refresh_seconds: float = 60.0):
    return ReliabilityProvider(training_store, refresh_seconds=refresh_seconds)


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
    reliability_provider = _build_reliability_provider(
        stores.training_store, settings.reliability_refresh_seconds
    )
    selector = _make_runbook_selector(settings)
    thread = start_supervised(
        "rca-consumer",
        run_consumer,
        stop_event,
        (app.state.bus, provider, store, audit_sink, holder.get, stop_event),
        {
            "reliability_provider": reliability_provider,
            "selector": selector,
            "guard": make_guard(settings, app.state.bus),
        },
    )
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
