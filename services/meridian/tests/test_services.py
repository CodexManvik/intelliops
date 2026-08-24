"""Domain-endpoint tests for all 4 Meridian services via registry-isolated instances.

Each service is built fresh with make_meridian_service(..., registry=CollectorRegistry())
using route-building callables that mirror the real services/meridian/*/app.py
modules line for line, so this file can exercise all 4 services in one pytest
process without ever importing a services/meridian/*/app.py module (which
would execute that module's top-level `app = make_meridian_service(...)` and
register gauges on prometheus_client's process-wide default registry — see
test_metrics.py's docstring for the full explanation of why even a "just
import one name" import doesn't avoid this). No test file in this package
imports any Meridian service module directly, by design: the shared default
registry is also used by services/demo_app/app.py, which the full
`pytest -m "not postgres and not kafka" -q` run collects first.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry

from services.meridian.common import make_meridian_service


def _gateway_routes(app, state) -> None:
    @app.post("/api/submissions")
    def submit(payload: dict) -> dict:
        client = payload.get("client", "")
        period = payload.get("period", "")
        amount = payload.get("amount", 0.0)
        return {"accepted": True, "client": client, "period": period, "amount": amount}

    @app.get("/api/reports")
    def reports() -> dict:
        return {"reports": []}


def _validation_routes(app, state) -> None:
    @app.post("/validate")
    def validate(payload: dict) -> dict:
        client = payload.get("client", "")
        period = payload.get("period", "")
        valid = bool(client) and bool(period)
        return {"valid": valid, "client": client, "period": period}


def _aggregation_routes(app, state) -> None:
    @app.post("/aggregate")
    def aggregate(payload: dict) -> dict:
        rows = payload.get("rows", [])
        return {"aggregated": True, "rows": len(rows)}


def _reporting_routes(app, state) -> None:
    @app.post("/report")
    def report(payload: dict) -> dict:
        submission_id = payload.get("submission_id")
        return {"generated": True, "submission_id": submission_id, "summary": "ok"}


SERVICES = [
    ("meridian-gateway", _gateway_routes),
    ("meridian-validation", _validation_routes),
    ("meridian-aggregation", _aggregation_routes),
    ("meridian-reporting", _reporting_routes),
]


def _client(name: str, routes) -> TestClient:
    app = make_meridian_service(name, routes, registry=CollectorRegistry())
    return TestClient(app)


def test_all_services_health_ok():
    for name, routes in SERVICES:
        c = _client(name, routes)
        assert c.get("/health").status_code == 200, name


def test_gateway_submissions_endpoint():
    c = _client("meridian-gateway", _gateway_routes)
    r = c.post(
        "/api/submissions",
        json={"client": "Acme Corp", "period": "2026-Q2", "amount": 1234.5},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] is True
    assert body["client"] == "Acme Corp"


def test_gateway_reports_endpoint():
    c = _client("meridian-gateway", _gateway_routes)
    r = c.get("/api/reports")
    assert r.status_code == 200
    assert "reports" in r.json()


def test_validation_endpoint():
    c = _client("meridian-validation", _validation_routes)
    r = c.post("/validate", json={"client": "Acme Corp", "period": "2026-Q2"})
    assert r.status_code == 200
    assert r.json()["valid"] is True


def test_validation_rejects_missing_fields():
    c = _client("meridian-validation", _validation_routes)
    r = c.post("/validate", json={"client": "", "period": "2026-Q2"})
    assert r.status_code == 200
    assert r.json()["valid"] is False


def test_aggregation_endpoint():
    c = _client("meridian-aggregation", _aggregation_routes)
    r = c.post("/aggregate", json={"rows": [1, 2, 3]})
    assert r.status_code == 200
    assert r.json()["rows"] == 3


def test_reporting_endpoint():
    c = _client("meridian-reporting", _reporting_routes)
    r = c.post("/report", json={"submission_id": 42})
    assert r.status_code == 200
    assert r.json()["generated"] is True


def test_crash_fault_marks_service_unhealthy_in_state():
    c = _client("meridian-reporting", _reporting_routes)
    r = c.post("/admin/fault", json={"type": "crash"})
    assert r.status_code == 200
    assert c.app.state.meridian.unhealthy is True


def test_saturation_fault_reflected_in_metrics():
    c = _client("meridian-validation", _validation_routes)
    c.post("/admin/fault", json={"type": "saturation"})
    body = c.get("/metrics").text
    line = next(line for line in body.splitlines() if line.startswith("cpu_usage "))
    assert float(line.split()[1]) == 92.0


def test_error_fault_keeps_cpu_at_baseline_for_every_service():
    for name, routes in SERVICES:
        c = _client(name, routes)
        c.post("/admin/fault", json={"type": "error", "magnitude": 1.0})
        body = c.get("/metrics").text
        cpu_line = next(line for line in body.splitlines() if line.startswith("cpu_usage "))
        err_line = next(
            line for line in body.splitlines() if line.startswith("meridian_error_rate ")
        )
        assert float(cpu_line.split()[1]) == 18.0, name
        assert float(err_line.split()[1]) == 1.0, name
