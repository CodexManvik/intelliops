"""Shared fault mechanism + service factory for the Meridian sample system.

Meridian is a small Deloitte-style financial-reporting platform (gateway,
validation, aggregation, reporting) whose only purpose is to be a realistic
target for IntelliOps to monitor. Each service exposes /metrics with a
cpu_usage + meridian_error_rate gauge pair, and an /admin/fault endpoint
(gated by common.auth.require_token) that toggles a simulated incident —
mirroring services/demo_app/app.py's /break /fix but generalized to four
fault types and four services.

CRITICAL: an "error" fault must set meridian_error_rate high while KEEPING
cpu_usage at baseline. If cpu also spiked, the z-score correlator would see
two anomalous signals and RCA's scale-service playbook (weight 0.6) would
outrank restart-pod (weight 0.5) — misdiagnosing a pure error-rate incident
as a capacity problem. Only "saturation" and "latency" faults touch cpu.
"""

from __future__ import annotations

import asyncio

from fastapi import Depends, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Gauge,
    generate_latest,
)
from pydantic import BaseModel

from common.auth import require_token
from services.base import create_app

CPU_HEALTHY = 18.0
CPU_BROKEN = 92.0


class FaultSpec(BaseModel):
    type: str  # "saturation" | "error" | "latency" | "crash"
    magnitude: float = 1.0
    duration_seconds: float | None = None


class MeridianState:
    def __init__(self) -> None:
        self.cpu = CPU_HEALTHY
        self.error_rate = 0.0
        self.latency_ms = 0.0
        self.unhealthy = False

    def apply(self, spec: FaultSpec) -> None:
        if spec.type == "saturation":
            self.cpu = min(100.0, CPU_BROKEN * spec.magnitude)
        elif spec.type == "error":
            self.error_rate = min(1.0, spec.magnitude)
            self.cpu = (
                CPU_HEALTHY  # keep cpu at baseline so RCA maps to restart-pod, NOT scale-service
            )
        elif spec.type == "latency":
            self.latency_ms = 200.0 * spec.magnitude
            self.cpu = CPU_BROKEN  # latency also drives cpu -> scale-service
        elif spec.type == "crash":
            self.unhealthy = True

    def clear(self) -> None:
        self.cpu = CPU_HEALTHY
        self.error_rate = 0.0
        self.latency_ms = 0.0
        self.unhealthy = False


def make_meridian_service(name: str, domain_routes=None, registry: CollectorRegistry | None = None):
    """Build a Meridian FastAPI app: /health, /ready, /metrics, /admin/fault, /admin/clear.

    `domain_routes`, if given, is called as `domain_routes(app, state)` to add
    the service's real endpoints (e.g. POST /validate).

    `registry` defaults to prometheus_client's global default registry, which
    is correct in production: each Meridian service is its own process, so
    there is only ever one `cpu_usage` gauge per process. Tests that need to
    exercise more than one Meridian service in a single pytest process should
    pass a fresh `CollectorRegistry()` per service to avoid the "Duplicated
    timeseries in CollectorRegistry" error from registering `cpu_usage` twice
    against the shared default registry.
    """
    app = create_app(name)
    state = MeridianState()
    app.state.meridian = state
    effective_registry = registry if registry is not None else REGISTRY

    # Bare gauges (no `service` label) — each Meridian service is its own
    # process/container; the Task-2 Prometheus scrape job injects the
    # `service` label at scrape time. Do NOT add a label here.
    cpu_gauge = Gauge("cpu_usage", "Simulated CPU utilization percent", registry=effective_registry)
    error_gauge = Gauge(
        "meridian_error_rate", "Simulated request error rate 0..1", registry=effective_registry
    )

    @app.get("/metrics")
    def metrics() -> Response:
        cpu_gauge.set(state.cpu)
        error_gauge.set(state.error_rate)
        return Response(generate_latest(effective_registry), media_type=CONTENT_TYPE_LATEST)

    @app.post("/admin/fault", dependencies=[Depends(require_token)])
    def fault(spec: FaultSpec) -> dict:
        state.apply(spec)
        return {"applied": spec.type, "cpu": state.cpu, "error_rate": state.error_rate}

    @app.post("/admin/clear", dependencies=[Depends(require_token)])
    def clear() -> dict:
        state.clear()
        return {"cleared": True}

    # Latency injection on real domain traffic only (not health/ready/metrics/admin).
    @app.middleware("http")
    async def _inject_latency(request: Request, call_next):
        path = request.url.path
        is_domain_route = path not in ("/metrics", "/health", "/ready") and not path.startswith(
            "/admin"
        )
        if state.latency_ms and is_domain_route:
            await asyncio.sleep(state.latency_ms / 1000.0)
        return await call_next(request)

    if domain_routes:
        domain_routes(app, state)
    return app
