"""Shared FastAPI app factory for all six services.

At skeleton stage every service is identical: a /health endpoint and a bus
client on app.state. Service-specific handlers arrive in later slices.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sqlalchemy import text

from common.auth import is_authorized
from common.bus import make_bus
from common.config import get_settings
from common.logging import configure_logging

# Self-monitoring (RED) for the platform's own services. Module-level so repeated
# create_app() calls (tests) never re-register; prefixed so they cannot collide
# with a monitored app's own http_* series (demo-app). `path` is the matched ROUTE
# TEMPLATE, not the raw URL, so path params can't blow up label cardinality.
_HTTP_REQUESTS = Counter(
    "intelliops_http_requests_total",
    "HTTP requests handled",
    ["service", "method", "path", "status"],
)
_HTTP_DURATION = Histogram(
    "intelliops_http_request_duration_seconds",
    "HTTP request latency",
    ["service", "method", "path"],
)
# Not /metrics: read + feedback already serve token-gated JSON business metrics there.
METRICS_PATH = "/_metrics"
_PROBE_PATHS = ("/health", "/ready", METRICS_PATH)


def db_ready(engine) -> None:
    """Raise if the DB is unreachable. A None engine (file mode / no DB) is a no-op pass."""
    if engine is None:
        return
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


def create_app(
    service_name: str,
    auth_exempt: Callable[[str, str], bool] | None = None,
    readiness: Callable[[], None] | None = None,
    metrics: bool = True,
) -> FastAPI:
    """Create a FastAPI app with standard IntelliOps middleware.

    Args:
        service_name: Human label shown in /health and OpenAPI title.
        auth_exempt: Optional predicate ``(method, path) -> bool``;
            returns True to skip the auth gate.  Defaults to exempting
            only ``/health``.  Services that host internal-bus endpoints
            (e.g. governance) pass a broader predicate so inter-service
            calls are never blocked by AUTH_MODE=token.
        readiness: Optional zero-arg callable that raises on a failed
            dependency check (e.g. a Postgres ping).  Wired into ``/ready``
            alongside the bus ping.  Services with no database omit it and
            get bus-only readiness.
        metrics: Expose ``/_metrics`` (request count + latency) for Prometheus.
            The monitored sample system (Meridian) passes False so platform
            instrumentation stays out of the workload being observed.
    """
    settings = get_settings()
    configure_logging(service_name, settings)
    app = FastAPI(title=f"IntelliOps · {service_name}")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # The consumer name MUST stay stable across restarts: under at-least-once the
    # pending-entry self-drain re-serves entries recorded against THIS name, so a
    # per-process name would strand an un-acked entry. Scaling a consumer past
    # replicas:1 needs per-pod names plus XAUTOCLAIM (see ADR-033).
    app.state.bus = make_bus(settings, consumer_name=settings.bus_consumer_name or "c1")

    _is_exempt = auth_exempt or (lambda method, path: path in ("/health", "/ready"))

    # Auth at the edge (AUTH_MODE=off|token). /health, /ready and /_metrics are
    # always exempt so compose/k8s probes and the Prometheus scrape never need a
    # token, in any mode — the short-circuit below runs BEFORE the exempt
    # predicate, so they stay ungated even for a service that passes a custom
    # auth_exempt.
    @app.middleware("http")
    async def _auth_gate(request: Request, call_next):
        if request.url.path in _PROBE_PATHS:
            return await call_next(request)
        current_settings = get_settings()
        if not _is_exempt(request.method, request.url.path) and not is_authorized(
            request, current_settings
        ):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)

    if metrics:
        # Registered after the auth gate, so it wraps it and counts 401s too.
        @app.middleware("http")
        async def _record_metrics(request: Request, call_next):
            if request.url.path in _PROBE_PATHS:
                return await call_next(request)
            start = time.perf_counter()
            response = await call_next(request)
            route = request.scope.get("route")
            path = route.path if route is not None else "unmatched"
            _HTTP_REQUESTS.labels(service_name, request.method, path, response.status_code).inc()
            _HTTP_DURATION.labels(service_name, request.method, path).observe(
                time.perf_counter() - start
            )
            return response

        @app.get(METRICS_PATH, include_in_schema=False)
        def metrics_endpoint() -> Response:
            return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"service": service_name, "status": "ok"}

    @app.get("/config/posture")
    def posture() -> dict:
        """What THIS process is actually running.

        /system on read-service aggregates the operational posture, but those
        settings belong to other services - correlator_kind to correlation,
        remediator_mode to action, store_backend to whoever owns a store. Read
        was answering from its OWN environment, which is not merely stale but
        a different process's configuration: it reported store_backend "file"
        while five services ran on Postgres. Each service now answers for
        itself and read asks the owner.

        Deliberately no secrets: endpoints and modes only, never a key.
        """
        s = get_settings()
        return {
            "service": service_name,
            "store_backend": s.store_backend,
            "bus_backend": s.bus_backend,
            "auth_mode": s.auth_mode,
            "correlator_kind": s.correlator_kind,
            "detection_policy": s.detection_policy,
            "remediator_mode": s.remediator_mode,
            "health_check_mode": s.health_check_mode,
            "sandbox_mode": s.sandbox_mode,
            "runbook_selector_mode": s.runbook_selector_mode,
            "bus_delivery": s.bus_delivery,
            "correlation_group_by": s.correlation_group_by,
        }

    @app.get("/ready")
    def ready():
        failed = []
        try:
            app.state.bus.ping()
        except Exception:  # noqa: BLE001
            failed.append("redis")
        if readiness is not None:
            try:
                readiness()
            except Exception:  # noqa: BLE001
                failed.append("postgres")
        if failed:
            return JSONResponse({"ready": False, "failed": failed}, status_code=503)
        return {"ready": True}

    return app
