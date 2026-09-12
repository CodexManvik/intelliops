"""Meridian validation service: validates financial submissions.

Sample target for IntelliOps — see services/meridian/common.py for the
shared fault mechanism (/admin/fault, /admin/clear, /metrics).
"""

from __future__ import annotations

from services.meridian.common import make_meridian_service


def _routes(app, state) -> None:
    @app.post("/validate")
    def validate(payload: dict) -> dict:
        # Simulate validation work; a real endpoint so traffic actually flows
        # through the service (and can be latency-injected by a fault).
        client = payload.get("client", "")
        period = payload.get("period", "")
        valid = bool(client) and bool(period)
        return {"valid": valid, "client": client, "period": period}


app = make_meridian_service("meridian-validation", _routes)
