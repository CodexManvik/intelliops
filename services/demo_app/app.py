"""A tiny breakable target that emits Prometheus metrics.

The operator flips /break to simulate an incident (error rate + CPU spike) and
/fix to recover. IntelliOps scrapes these metrics via Prometheus. This is the
'real running app' the live demo observes — nothing here depends on IntelliOps.

demo_app is a single process on prometheus_client's default registry (unlike
Meridian's four services, there's only ever one of these, so no per-service
label or private registry is needed). It exposes the same USE+RED gauge names
as Meridian (Metrics Phase 1) so the quickstart target shows the same rich
metric surface: cpu_usage + request_rate/latency_p50_ms/latency_p99_ms/
memory_usage_mb/saturation/queue_depth/db_pool_in_use/db_pool_max/
disk_usage_percent. /break moves one representative "something's wrong"
cluster (cpu, latency, memory, saturation, queue_depth up); db pool and disk
stay at baseline since demo_app has no db-exhaustion or disk scenario — it's
the minimal one-endpoint target, not a full fault-profile system like
Meridian's services/meridian/common.py.
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

# USE+RED metric set (Metrics Phase 1) — same gauge names/units as Meridian's
# services/meridian/common.py, healthy baselines matched to Meridian's for
# consistency across the demo. request_rate, db_pool_*, and disk_usage_percent
# are NOT part of demo_app's /break cluster (see module docstring) so they
# stay pinned at baseline; /metrics still refreshes them every scrape for a
# stable, complete USE+RED surface.
_request_rate = Gauge("request_rate", "Simulated requests per second")
_latency_p50 = Gauge("latency_p50_ms", "Simulated p50 request latency in milliseconds")
_latency_p99 = Gauge("latency_p99_ms", "Simulated p99 request latency in milliseconds")
_memory_usage = Gauge("memory_usage_mb", "Simulated resident memory usage in megabytes")
_saturation = Gauge("saturation", "Simulated USE saturation fraction 0..1")
_queue_depth = Gauge("queue_depth", "Simulated pending work-queue depth")
_db_pool_in_use = Gauge("db_pool_in_use", "Simulated database connections currently checked out")
_db_pool_max = Gauge("db_pool_max", "Simulated database connection pool size")
_disk_usage = Gauge("disk_usage_percent", "Simulated disk utilization percent")

_REQUEST_RATE_HEALTHY = 50.0
_LATENCY_P50_MS_HEALTHY = 20.0
_LATENCY_P99_MS_HEALTHY = 80.0
_MEMORY_USAGE_MB_HEALTHY = 256.0
_SATURATION_HEALTHY = 0.1
_QUEUE_DEPTH_HEALTHY = 2.0
_DB_POOL_IN_USE_HEALTHY = 3.0
_DB_POOL_MAX_HEALTHY = 20.0
_DISK_USAGE_PERCENT_HEALTHY = 35.0

_LATENCY_P50_MS_BROKEN = 220.0
_LATENCY_P99_MS_BROKEN = 950.0
_MEMORY_USAGE_MB_BROKEN = 700.0
_SATURATION_BROKEN = 0.9
_QUEUE_DEPTH_BROKEN = 180.0


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
    _set_gauges(broken=True)
    return {"broken": True}


@app.post("/fix", dependencies=[Depends(require_token)])
def fix_it() -> dict[str, bool]:
    _state["broken"] = False
    _set_gauges(broken=False)
    return {"broken": False}


def _set_gauges(*, broken: bool) -> None:
    """Set every gauge from `_state["broken"]`: the representative cluster
    (cpu, latency, memory, saturation, queue_depth) moves together; request
    rate, db pool, and disk stay pinned at baseline (see module docstring).
    """
    _cpu.set(_CPU_BROKEN if broken else _CPU_HEALTHY)
    _latency_p50.set(_LATENCY_P50_MS_BROKEN if broken else _LATENCY_P50_MS_HEALTHY)
    _latency_p99.set(_LATENCY_P99_MS_BROKEN if broken else _LATENCY_P99_MS_HEALTHY)
    _memory_usage.set(_MEMORY_USAGE_MB_BROKEN if broken else _MEMORY_USAGE_MB_HEALTHY)
    _saturation.set(_SATURATION_BROKEN if broken else _SATURATION_HEALTHY)
    _queue_depth.set(_QUEUE_DEPTH_BROKEN if broken else _QUEUE_DEPTH_HEALTHY)
    _request_rate.set(_REQUEST_RATE_HEALTHY)
    _db_pool_in_use.set(_DB_POOL_IN_USE_HEALTHY)
    _db_pool_max.set(_DB_POOL_MAX_HEALTHY)
    _disk_usage.set(_DISK_USAGE_PERCENT_HEALTHY)


@app.get("/metrics")
def metrics() -> Response:
    # keep every gauge fresh even if neither toggle was hit this scrape
    _set_gauges(broken=_state["broken"])
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
