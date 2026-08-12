"""Shared FastAPI app factory for all six services.

At skeleton stage every service is identical: a /health endpoint and a bus
client on app.state. Service-specific handlers arrive in later slices.
"""

from __future__ import annotations

from fastapi import FastAPI

from common.bus import make_bus
from common.config import get_settings


def create_app(service_name: str) -> FastAPI:
    app = FastAPI(title=f"IntelliOps · {service_name}")
    app.state.bus = make_bus(get_settings())

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"service": service_name, "status": "ok"}

    return app
