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
import time

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

# USE+RED metric set healthy baselines. Task 2 adds the fault profiles that
# move these away from baseline; Task 1 only establishes the field + gauge
# for each metric at a plausible steady-state value.
REQUEST_RATE_HEALTHY = 50.0
LATENCY_P50_MS_HEALTHY = 20.0
LATENCY_P99_MS_HEALTHY = 80.0
MEMORY_USAGE_MB_HEALTHY = 256.0
SATURATION_HEALTHY = 0.1
QUEUE_DEPTH_HEALTHY = 2.0
DB_POOL_IN_USE_HEALTHY = 3.0
DB_POOL_MAX_HEALTHY = 20.0
DISK_USAGE_PERCENT_HEALTHY = 35.0


class FaultSpec(BaseModel):
    type: str  # "saturation" | "error" | "latency" | "crash" | "memory_leak"
    magnitude: float = 1.0
    duration_seconds: float | None = None


class MeridianState:
    def __init__(self) -> None:
        self.cpu = CPU_HEALTHY
        self.error_rate = 0.0
        self.latency_ms = 0.0
        self.unhealthy = False

        # USE+RED metric set (Metrics Phase 1). Purely additive alongside the
        # original cpu/error_rate/latency_ms/unhealthy fields above.
        self.request_rate = REQUEST_RATE_HEALTHY
        self.latency_p50_ms = LATENCY_P50_MS_HEALTHY
        self.latency_p99_ms = LATENCY_P99_MS_HEALTHY
        self.memory_usage_mb = MEMORY_USAGE_MB_HEALTHY
        self.saturation = SATURATION_HEALTHY
        self.queue_depth = QUEUE_DEPTH_HEALTHY
        self.db_pool_in_use = DB_POOL_IN_USE_HEALTHY
        self.db_pool_max = DB_POOL_MAX_HEALTHY
        self.disk_usage_percent = DISK_USAGE_PERCENT_HEALTHY

        # Gradual-ramp bookkeeping: a fault (e.g. a future `memory_leak`
        # profile) can populate this with a descriptor and `sample(now)` will
        # advance the named metric linearly from start_value to target_value
        # over duration seconds, measured from start_time. None means no
        # ramp is active and `sample` is a no-op read.
        self._ramp: dict | None = None

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
        elif spec.type == "memory_leak":
            # Minimal ramp wiring for Task 1's sample() machinery: start a
            # linear climb from the current memory_usage_mb toward a target
            # scaled by magnitude, over duration_seconds (default 300s if
            # unspecified). Task 2 owns the full fault-profile tuning (target
            # formula, interaction with other metrics, etc.) — this only
            # proves the ramp descriptor + sample() advance it correctly.
            duration = spec.duration_seconds if spec.duration_seconds is not None else 300.0
            self._ramp = {
                "metric": "memory_usage_mb",
                "start_value": self.memory_usage_mb,
                "target_value": self.memory_usage_mb + 512.0 * spec.magnitude,
                "start_time": time.monotonic(),
                "duration": duration,
            }

    def sample(self, now: float) -> None:
        """Advance any active gradual ramp to the given monotonic time.

        For all non-ramp state this is a no-op read: with no ramp active,
        calling sample() does not change any field. When a ramp is active,
        the named metric is set to
        `start + (target - start) * min(1.0, (now - start_time) / duration)`,
        so it climbs linearly and holds at target_value once elapsed time
        reaches duration.
        """
        if self._ramp is None:
            return
        ramp = self._ramp
        elapsed = now - ramp["start_time"]
        duration = ramp["duration"]
        fraction = 1.0 if duration <= 0 else min(1.0, elapsed / duration)
        value = ramp["start_value"] + (ramp["target_value"] - ramp["start_value"]) * fraction
        setattr(self, ramp["metric"], value)

    def clear(self) -> None:
        self.cpu = CPU_HEALTHY
        self.error_rate = 0.0
        self.latency_ms = 0.0
        self.unhealthy = False

        self.request_rate = REQUEST_RATE_HEALTHY
        self.latency_p50_ms = LATENCY_P50_MS_HEALTHY
        self.latency_p99_ms = LATENCY_P99_MS_HEALTHY
        self.memory_usage_mb = MEMORY_USAGE_MB_HEALTHY
        self.saturation = SATURATION_HEALTHY
        self.queue_depth = QUEUE_DEPTH_HEALTHY
        self.db_pool_in_use = DB_POOL_IN_USE_HEALTHY
        self.db_pool_max = DB_POOL_MAX_HEALTHY
        self.disk_usage_percent = DISK_USAGE_PERCENT_HEALTHY
        self._ramp = None


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

    # USE+RED metric set (Metrics Phase 1) — same bare-gauge convention as
    # cpu_gauge/error_gauge above.
    request_rate_gauge = Gauge(
        "request_rate", "Simulated requests per second", registry=effective_registry
    )
    latency_p50_gauge = Gauge(
        "latency_p50_ms",
        "Simulated p50 request latency in milliseconds",
        registry=effective_registry,
    )
    latency_p99_gauge = Gauge(
        "latency_p99_ms",
        "Simulated p99 request latency in milliseconds",
        registry=effective_registry,
    )
    memory_usage_gauge = Gauge(
        "memory_usage_mb",
        "Simulated resident memory usage in megabytes",
        registry=effective_registry,
    )
    saturation_gauge = Gauge(
        "saturation", "Simulated USE saturation fraction 0..1", registry=effective_registry
    )
    queue_depth_gauge = Gauge(
        "queue_depth", "Simulated pending work-queue depth", registry=effective_registry
    )
    db_pool_in_use_gauge = Gauge(
        "db_pool_in_use",
        "Simulated database connections currently checked out",
        registry=effective_registry,
    )
    db_pool_max_gauge = Gauge(
        "db_pool_max", "Simulated database connection pool size", registry=effective_registry
    )
    disk_usage_gauge = Gauge(
        "disk_usage_percent", "Simulated disk utilization percent", registry=effective_registry
    )

    @app.get("/metrics")
    def metrics() -> Response:
        state.sample(time.monotonic())
        cpu_gauge.set(state.cpu)
        error_gauge.set(state.error_rate)
        request_rate_gauge.set(state.request_rate)
        latency_p50_gauge.set(state.latency_p50_ms)
        latency_p99_gauge.set(state.latency_p99_ms)
        memory_usage_gauge.set(state.memory_usage_mb)
        saturation_gauge.set(state.saturation)
        queue_depth_gauge.set(state.queue_depth)
        db_pool_in_use_gauge.set(state.db_pool_in_use)
        db_pool_max_gauge.set(state.db_pool_max)
        disk_usage_gauge.set(state.disk_usage_percent)
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
