"""Read service: serves the dashboard's live read model (CQRS read side)."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
import threading
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from common.config import get_settings
from services.base import create_app
from services.read.consumer import run_consumer
from services.read.projection import ReadModel
from services.read.rebuild import rebuild

logger = logging.getLogger("intelliops.read.app")

# Both land inside a PromQL selector, so they are allowlisted rather than escaped.
_SAFE_METRIC = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]{0,80}")
_SAFE_LABEL = re.compile(r"[a-zA-Z0-9_.:-]{1,80}")


def _redact_endpoint(endpoint: str) -> str:
    """Show the host but never any embedded credential."""
    if not endpoint:
        return ""
    try:
        from urllib.parse import urlparse

        p = urlparse(endpoint)
        return f"{p.scheme}://{p.hostname}" + (f":{p.port}" if p.port else "")
    except Exception:  # noqa: BLE001
        return "configured"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    stop_event = threading.Event()
    model = ReadModel(
        max_outcomes=settings.read_outcomes_max,
        ttl_seconds=settings.read_situation_ttl_seconds,
        max_situations=settings.read_situations_max,
    )
    # Cold start: replay recent history so the console is not blank after a
    # restart. Returns None when disabled or on any failure, in which case we
    # keep the fresh (empty) model and tail as before.
    rebuilt = rebuild(app.state.bus, settings)
    if rebuilt is not None:
        model = rebuilt
    app.state.model = model
    model.bind_loop(asyncio.get_running_loop())
    app.state.consumer_stop = stop_event
    app.state.consumer_threads = run_consumer(app.state.bus, model, stop_event)
    try:
        yield
    finally:
        stop_event.set()


def _auth_exempt(method: str, path: str) -> bool:
    # /stream is reached by the browser EventSource API, which cannot set the
    # Authorization header; it authenticates via ?token= inside the route.
    return method == "GET" and path == "/stream"


app = create_app("read-service", auth_exempt=_auth_exempt)
app.router.lifespan_context = lifespan


@app.get("/situations")
def situations() -> list[dict]:
    model = getattr(app.state, "model", None)
    return model.situations(now_ms=int(time.time() * 1000)) if model else []


@app.get("/outcomes")
def outcomes() -> list[dict]:
    model = getattr(app.state, "model", None)
    return model.outcomes() if model else []


@app.get("/situations/{sid}")
def situation_detail(sid: str) -> dict:
    model = getattr(app.state, "model", None)
    detail = model.situation(sid) if model else None
    if detail is None:
        raise HTTPException(status_code=404, detail="situation not found")
    return detail


# /system aggregates settings that OTHER services own. Reading them out of
# read-service's own environment is simply wrong - the LLM variables are set on
# rca, so read reported "template, not configured" while rca had a live model.
# Ask the owner instead, cache briefly (this endpoint is polled), and fail soft.
_SYSTEM_CACHE_SECONDS = 5.0
_llm_cache: dict = {"at": 0.0, "value": None}


def _owned_llm_config(settings) -> dict:
    """The authoritative LLM state, from rca. Falls back to local env on failure."""
    now = time.monotonic()
    if _llm_cache["value"] is not None and now - _llm_cache["at"] < _SYSTEM_CACHE_SECONDS:
        return _llm_cache["value"]
    value = None
    try:
        with httpx.Client(timeout=2.0) as client:
            headers = (
                {"Authorization": f"Bearer {settings.auth_token}"}
                if settings.auth_mode == "token" and settings.auth_token
                else {}
            )
            resp = client.get(f"{settings.rca_url}/config/llm", headers=headers)
            resp.raise_for_status()
            body = resp.json()
        value = {
            "provider": body.get("provider", "template"),
            "endpoint_configured": bool(body.get("endpoint_configured")),
            "endpoint": body.get("endpoint", ""),
            "model": body.get("model", ""),
            "last_probe": body.get("last_probe"),
            "source": "rca",
        }
    except Exception as exc:  # noqa: BLE001 - /system must never 500 on a peer being down
        logger.debug("could not reach rca for llm config: %s", exc)
        endpoint = settings.llm_explanation_endpoint
        value = {
            "provider": "openai-compatible" if endpoint else "template",
            "endpoint_configured": bool(endpoint),
            "endpoint": _redact_endpoint(endpoint),
            "model": settings.llm_explanation_model,
            "last_probe": None,
            # Say so, rather than presenting a guess as fact.
            "source": "unavailable",
        }
    _llm_cache.update(at=now, value=value)
    return value


@app.get("/system")
def system() -> dict:
    settings = get_settings()
    return {
        "correlator_kind": settings.correlator_kind,
        "bus_backend": settings.bus_backend,
        "store_backend": settings.store_backend,
        "remediator_mode": settings.remediator_mode,
        "auth_mode": settings.auth_mode,
        "llm": _owned_llm_config(settings),
    }


@app.get("/metrics/history")
def metrics_history(
    metric: str = "cpu_usage",
    minutes: float = 15.0,
    step_seconds: float = 15.0,
    service: str | None = None,
) -> dict:
    """Real per-service time-series for `metric`, proxied from Prometheus.

    The console cannot query Prometheus directly (it sends no CORS header), and
    /metrics is point-in-time, so without this there is no honest way to draw a
    live graph. Fails soft: an unreachable Prometheus returns an empty series
    with `available: false` so the UI can say "no data" instead of inventing a
    shape.

    `metric` is validated against a strict allowlist pattern rather than being
    interpolated blind - it lands in a PromQL selector.
    """
    settings = get_settings()
    if not _SAFE_METRIC.fullmatch(metric):
        raise HTTPException(status_code=400, detail="invalid metric name")
    if service is not None and not _SAFE_LABEL.fullmatch(service):
        raise HTTPException(status_code=400, detail="invalid service name")

    minutes = max(1.0, min(minutes, 360.0))
    step_seconds = max(5.0, min(step_seconds, 300.0))
    end = time.time()
    start = end - minutes * 60.0
    query = metric if service is None else f'{metric}{{service="{service}"}}'

    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(
                f"{settings.prometheus_url}/api/v1/query_range",
                params={"query": query, "start": start, "end": end, "step": step_seconds},
            )
            resp.raise_for_status()
            body = resp.json()
        if body.get("status") != "success":
            raise ValueError(body.get("error", "prometheus returned a non-success status"))
        series = [
            {
                "service": r.get("metric", {}).get("service", "unknown"),
                # [[unix_seconds, value], ...] - numbers, not Prometheus' strings,
                # so the client does not have to know the wire quirk.
                "points": [[float(t), float(v)] for t, v in r.get("values", [])],
            }
            for r in body.get("data", {}).get("result", [])
        ]
    except Exception as exc:  # noqa: BLE001 - a graph must never 500 the console
        logger.info("metrics history unavailable for %s: %s", metric, exc)
        return {
            "metric": metric,
            "available": False,
            "reason": "prometheus unreachable",
            "start": start,
            "end": end,
            "step_seconds": step_seconds,
            "series": [],
        }

    return {
        "metric": metric,
        "available": True,
        "start": start,
        "end": end,
        "step_seconds": step_seconds,
        "series": series,
    }


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


def _stream_authorized(request: Request) -> bool:
    settings = get_settings()
    if settings.auth_mode != "token":
        return True
    token = request.query_params.get("token", "")
    return bool(settings.auth_token) and hmac.compare_digest(token, settings.auth_token)


@app.get("/stream")
async def stream(request: Request):
    if not _stream_authorized(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    model = getattr(app.state, "model", None)
    if model is None:
        return JSONResponse({"detail": "not ready"}, status_code=503)

    async def gen():
        q = model.subscribe()
        try:
            yield ": connected\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            model.unsubscribe(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
