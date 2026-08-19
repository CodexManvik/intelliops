"""A tiny breakable target that emits Prometheus metrics.

The operator flips /break to simulate an incident (error rate + CPU spike) and
/fix to recover. IntelliOps scrapes these metrics via Prometheus. This is the
'real running app' the live demo observes — nothing here depends on IntelliOps.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest

from common.auth import require_token

app = FastAPI(title="IntelliOps · demo-app")

_state: dict[str, bool] = {"broken": False}

_requests = Counter("http_requests_total", "Total work requests")
_errors = Counter("http_request_errors_total", "Total failed work requests")
_cpu = Gauge("cpu_usage", "Simulated CPU utilization percent")

_CPU_HEALTHY = 18.0
_CPU_BROKEN = 92.0


@app.get("/health")
def health() -> dict[str, str]:
    return {"service": "demo-app", "status": "ok"}


@app.get("/work")
def work() -> dict[str, str]:
    _requests.inc()
    if _state["broken"]:
        _errors.inc()
        raise HTTPException(status_code=500, detail="dependency failure")
    return {"result": "ok"}


@app.post("/break", dependencies=[Depends(require_token)])
def break_it() -> dict[str, bool]:
    _state["broken"] = True
    _cpu.set(_CPU_BROKEN)
    return {"broken": True}


@app.post("/fix", dependencies=[Depends(require_token)])
def fix_it() -> dict[str, bool]:
    _state["broken"] = False
    _cpu.set(_CPU_HEALTHY)
    return {"broken": False}


@app.get("/metrics")
def metrics() -> Response:
    # keep the gauge fresh even if neither toggle was hit this scrape
    _cpu.set(_CPU_BROKEN if _state["broken"] else _CPU_HEALTHY)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
