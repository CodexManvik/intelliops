"""Meridian aggregation service: rolls up validated submissions.

Sample target for IntelliOps — see services/meridian/common.py for the
shared fault mechanism (/admin/fault, /admin/clear, /metrics).
"""

from __future__ import annotations

from services.meridian.common import make_meridian_service


def _routes(app, state) -> None:
    @app.post("/aggregate")
    def aggregate(payload: dict) -> dict:
        # Simulate roll-up work; a real endpoint so traffic actually flows
        # through the service (and can be latency-injected by a fault).
        rows = payload.get("rows", [])
        return {"aggregated": True, "rows": len(rows)}


app = make_meridian_service("meridian-aggregation", _routes)
