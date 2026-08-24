"""Meridian gateway service: the entry point for financial submissions/reports.

Sample target for IntelliOps — see services/meridian/common.py for the
shared fault mechanism (/admin/fault, /admin/clear, /metrics).

Task-1 scope is domain routes + the shared service scaffold only. The
ops-proxy, /admin/deploy, and the StaticFiles UI mount arrive in Task 3.
"""

from __future__ import annotations

from services.meridian.common import make_meridian_service


def _routes(app, state) -> None:
    @app.post("/api/submissions")
    def submit(payload: dict) -> dict:
        # Simulate accepting a financial submission; a real endpoint so
        # traffic actually flows through the service (and can be
        # latency-injected by a fault).
        client = payload.get("client", "")
        period = payload.get("period", "")
        amount = payload.get("amount", 0.0)
        return {"accepted": True, "client": client, "period": period, "amount": amount}

    @app.get("/api/reports")
    def reports() -> dict:
        # Simulate listing reports; a real endpoint so traffic actually
        # flows through the service.
        return {"reports": []}


app = make_meridian_service("meridian-gateway", _routes)
