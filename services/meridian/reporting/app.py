"""Meridian reporting service: generates summary reports from aggregated data.

Sample target for IntelliOps — see services/meridian/common.py for the
shared fault mechanism (/admin/fault, /admin/clear, /metrics).
"""

from __future__ import annotations

from services.meridian.common import make_meridian_service


def _routes(app, state) -> None:
    @app.post("/report")
    def report(payload: dict) -> dict:
        # Simulate report generation; a real endpoint so traffic actually
        # flows through the service (and can be latency-injected by a fault).
        submission_id = payload.get("submission_id")
        return {"generated": True, "submission_id": submission_id, "summary": "ok"}


app = make_meridian_service("meridian-reporting", _routes)
