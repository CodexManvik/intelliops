"""Shared FastAPI app factory for all six services.

At skeleton stage every service is identical: a /health endpoint and a bus
client on app.state. Service-specific handlers arrive in later slices.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from common.auth import is_authorized
from common.bus import make_bus
from common.config import get_settings
from common.logging import configure_logging


def create_app(
    service_name: str,
    auth_exempt: Callable[[str, str], bool] | None = None,
    readiness: Callable[[], None] | None = None,
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
    app.state.bus = make_bus(settings)

    _is_exempt = auth_exempt or (lambda method, path: path in ("/health", "/ready"))

    # Auth at the edge (AUTH_MODE=off|token). /health and /ready are always
    # exempt so compose/k8s probes never need a token, in any mode — the
    # short-circuit below runs BEFORE the exempt predicate, so probes stay
    # ungated even when a service passes a custom auth_exempt (governance does).
    @app.middleware("http")
    async def _auth_gate(request: Request, call_next):
        if request.url.path in ("/health", "/ready"):
            return await call_next(request)
        current_settings = get_settings()
        if not _is_exempt(request.method, request.url.path) and not is_authorized(
            request, current_settings
        ):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"service": service_name, "status": "ok"}

    @app.get("/ready")
    def ready():
        failed = []
        try:
            app.state.bus.ping()
        except Exception:  # noqa: BLE001 — probe reports the failure, never raises
            failed.append("redis")
        if readiness is not None:
            try:
                readiness()
            except Exception:  # noqa: BLE001 — probe reports the failure, never raises
                failed.append("postgres")
        if failed:
            return JSONResponse({"ready": False, "failed": failed}, status_code=503)
        return {"ready": True}

    return app
