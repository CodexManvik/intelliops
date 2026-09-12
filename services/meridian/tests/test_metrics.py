"""/metrics + fault-toggle tests for the Meridian gateway service.

Builds a gateway-equivalent app via make_meridian_service(..., registry=CollectorRegistry())
with a locally-defined `_routes` (identical in behavior to
services/meridian/gateway/app.py's), on a private registry.

GAUGE-REGISTRY HAZARD: `from services.meridian.gateway.app import _routes` (or
`import app`) would NOT avoid the collision — Python executes a module's
entire top level on first import regardless of which names you bind, so
merely importing `_routes` from gateway.py still runs its
`app = make_meridian_service("meridian-gateway", _routes)` line, registering
gauges on prometheus_client's process-wide default registry. That registry
is shared by the WHOLE pytest process, not just this package —
services/demo_app/app.py ALSO registers a bare `Gauge("cpu_usage", ...)` at
import time, and `uv run pytest -m "not postgres and not kafka" -q` collects
demo_app's tests before meridian's, so any import of a Meridian service
module here raises "Duplicated timeseries in CollectorRegistry" in the
full-suite run (even though it looks fine when services/meridian/tests/ is
run alone). Defining `_routes` locally and passing a fresh CollectorRegistry
avoids importing any Meridian service module's top level entirely.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry

from services.meridian.common import make_meridian_service


def _routes(app, state) -> None:
    @app.post("/api/submissions")
    def submit(payload: dict) -> dict:
        client = payload.get("client", "")
        period = payload.get("period", "")
        amount = payload.get("amount", 0.0)
        return {"accepted": True, "client": client, "period": period, "amount": amount}

    @app.get("/api/reports")
    def reports() -> dict:
        return {"reports": []}


def _client() -> TestClient:
    app = make_meridian_service("meridian-gateway", _routes, registry=CollectorRegistry())
    return TestClient(app)


def _metric_value(body: str, name: str) -> float:
    line = next(line for line in body.splitlines() if line.startswith(f"{name} "))
    return float(line.split()[1])


def test_metrics_exposes_cpu_usage_and_error_rate():
    c = _client()
    body = c.get("/metrics").text
    assert "cpu_usage" in body
    assert "meridian_error_rate" in body
    assert _metric_value(body, "cpu_usage") == 18.0
    assert _metric_value(body, "meridian_error_rate") == 0.0


def test_saturation_fault_sets_cpu_to_92():
    c = _client()
    r = c.post("/admin/fault", json={"type": "saturation"})
    assert r.status_code == 200
    body = c.get("/metrics").text
    assert _metric_value(body, "cpu_usage") == 92.0


def test_clear_resets_cpu_to_18():
    c = _client()
    c.post("/admin/fault", json={"type": "saturation"})
    r = c.post("/admin/clear")
    assert r.status_code == 200
    body = c.get("/metrics").text
    assert _metric_value(body, "cpu_usage") == 18.0


def test_error_fault_sets_error_rate_and_keeps_cpu_at_baseline():
    c = _client()
    r = c.post("/admin/fault", json={"type": "error", "magnitude": 0.7})
    assert r.status_code == 200
    body = c.get("/metrics").text
    assert _metric_value(body, "meridian_error_rate") == 0.7
    # CRITICAL: cpu must stay at 18.0 (baseline) so RCA maps to restart-pod,
    # not scale-service.
    assert _metric_value(body, "cpu_usage") == 18.0


def test_latency_fault_sets_cpu_to_92_too():
    c = _client()
    r = c.post("/admin/fault", json={"type": "latency", "magnitude": 1.0})
    assert r.status_code == 200
    body = c.get("/metrics").text
    assert _metric_value(body, "cpu_usage") == 92.0


def test_fresh_state_exposes_full_metric_set_at_baseline():
    from prometheus_client import CollectorRegistry

    from services.meridian.common import make_meridian_service

    app = make_meridian_service("meridian-test", registry=CollectorRegistry())
    client = TestClient(app)
    body = client.get("/metrics").text
    for name in (
        "cpu_usage",
        "meridian_error_rate",
        "request_rate",
        "latency_p50_ms",
        "latency_p99_ms",
        "memory_usage_mb",
        "saturation",
        "queue_depth",
        "db_pool_in_use",
        "db_pool_max",
        "disk_usage_percent",
    ):
        assert name in body, f"missing metric: {name}"


def test_sample_advances_memory_leak_ramp():
    from services.meridian.common import FaultSpec, MeridianState

    st = MeridianState()
    st.apply(FaultSpec(type="memory_leak", magnitude=1.0, duration_seconds=100.0))
    st.sample(now=0.0)
    m0 = st.memory_usage_mb
    st.sample(now=50.0)
    m50 = st.memory_usage_mb
    assert m50 > m0  # the ramp climbed with elapsed time


def test_admin_fault_gated_in_token_mode(monkeypatch):
    # get_settings() is @lru_cache'd (see common/config.py), so the cache
    # must be cleared once after setting the env vars (to pick up token
    # mode) and once more before returning (so the NEXT test's call to
    # get_settings() re-populates the cache from the env as monkeypatch has
    # already restored it by then — pytest's monkeypatch fixture undoes env
    # changes on teardown, right after this function returns). Matches the
    # convention in services/demo_app/tests/test_demo_app.py.
    from common.config import get_settings

    monkeypatch.setenv("INTELLIOPS_AUTH_MODE", "token")
    monkeypatch.setenv("INTELLIOPS_AUTH_TOKEN", "secret")
    get_settings.cache_clear()

    c = _client()
    r = c.post("/admin/fault", json={"type": "saturation"})
    assert r.status_code == 401
    r = c.post(
        "/admin/fault",
        json={"type": "saturation"},
        headers={"Authorization": "Bearer secret"},
    )
    assert r.status_code == 200
    get_settings.cache_clear()
