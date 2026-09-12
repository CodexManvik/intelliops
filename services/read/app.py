"""Read service: serves the dashboard's live read model (CQRS read side)."""

from __future__ import annotations

import asyncio
import hmac
import json
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from common.config import get_settings
from services.base import create_app
from services.read.consumer import run_consumer
from services.read.projection import ReadModel


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


@app.get("/system")
def system() -> dict:
    settings = get_settings()
    endpoint = settings.llm_explanation_endpoint
    return {
        "correlator_kind": settings.correlator_kind,
        "bus_backend": settings.bus_backend,
        "store_backend": settings.store_backend,
        "remediator_mode": settings.remediator_mode,
        "auth_mode": settings.auth_mode,
        "llm": {
            "provider": "openai-compatible" if endpoint else "template",
            "endpoint_configured": bool(endpoint),
            "endpoint": _redact_endpoint(endpoint),
            "model": settings.llm_explanation_model,
        },
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
