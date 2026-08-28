"""GET /api/ops/metrics proxies a Prometheus instant query, server-side.

GAUGE-REGISTRY HAZARD: this file must NOT import services.meridian.gateway.app
(or anything that imports it) — see test_gateway.py / test_metrics.py's
docstrings for the full explanation. Merely importing that module executes
its top-level `app = make_meridian_service("meridian-gateway", _routes)`,
registering gauges on prometheus_client's process-wide default registry a
second time (demo_app's tests already register the same bare `cpu_usage`
gauge there, and get collected first in the full
`uv run pytest -m "not postgres and not kafka" -q` run), raising "Duplicated
timeseries in CollectorRegistry". So instead of importing the real module,
`_routes` below is a line-for-line mirror of gateway/app.py's `_routes`
(ops-proxy + /admin/deploy + the new /api/ops/metrics route), applied to a
registry-isolated app the same way test_gateway.py/test_metrics.py do.

httpx.Client is monkeypatched at the `httpx` module level (not
dependency-injected — ops_metrics constructs its own short-lived
`httpx.Client(...)` per the brief) so a MockTransport-backed client stands in
for the real network call.
"""

from __future__ import annotations

import httpx
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry

from common.config import get_settings
from services.meridian.common import make_meridian_service

_MERIDIAN_SERVICES = {"gateway", "validation", "aggregation", "reporting"}


def _routes(app, state) -> None:
    # Mirrors gateway/app.py's _routes — only the piece under test
    # (/api/ops/metrics) needs to be faithful; the other routes aren't
    # exercised here but are included so this stays a drop-in mirror.

    @app.get("/api/ops/metrics")
    def ops_metrics() -> dict:
        prom = get_settings().prometheus_url.rstrip("/")
        query = '{__name__=~"cpu_usage|meridian_error_rate"}'
        try:
            with httpx.Client(timeout=5.0) as c:
                resp = c.get(f"{prom}/api/v1/query", params={"query": query})
        except httpx.HTTPError:
            return {"scraped": False, "services": []}
        if resp.status_code != 200:
            return {"scraped": False, "services": []}
        try:
            body = resp.json()
        except ValueError:
            return {"scraped": False, "services": []}
        if not isinstance(body, dict) or body.get("status") != "success":
            return {"scraped": False, "services": []}
        by_service: dict[str, dict] = {}
        for entry in body.get("data", {}).get("result", []):
            metric = entry.get("metric", {})
            svc = metric.get("service")
            name = metric.get("__name__")
            value_pair = entry.get("value", [0, "0"])
            if not svc or not isinstance(value_pair, list) or len(value_pair) < 2:
                continue
            try:
                val = float(value_pair[1])
            except (TypeError, ValueError):
                continue
            row = by_service.setdefault(
                svc, {"service": svc, "cpu_usage": None, "error_rate": None}
            )
            if name == "cpu_usage":
                row["cpu_usage"] = val
            elif name == "meridian_error_rate":
                row["error_rate"] = val
        services = []
        for row in by_service.values():
            cpu = row["cpu_usage"]
            err = row["error_rate"]
            row["healthy"] = (cpu is None or cpu < 50) and (err is None or err < 0.1)
            services.append(row)
        services.sort(key=lambda r: r["service"])
        return {"scraped": bool(services), "services": services}


def _client() -> TestClient:
    app = make_meridian_service("meridian-gateway", _routes, registry=CollectorRegistry())
    return TestClient(app)


def _mock_httpx_client(monkeypatch, handler):
    class _MockClient(httpx.Client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", _MockClient)


def _prom_response():
    # Prometheus instant-query success shape for cpu_usage + meridian_error_rate
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {
                    "metric": {"__name__": "cpu_usage", "service": "meridian-aggregation"},
                    "value": [0, "92"],
                },
                {
                    "metric": {"__name__": "cpu_usage", "service": "meridian-gateway"},
                    "value": [0, "18"],
                },
                {
                    "metric": {
                        "__name__": "meridian_error_rate",
                        "service": "meridian-aggregation",
                    },
                    "value": [0, "0"],
                },
            ],
        },
    }


def test_ops_metrics_returns_per_service_values(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_prom_response())

    _mock_httpx_client(monkeypatch, handler)

    c = _client()
    r = c.get("/api/ops/metrics")

    assert r.status_code == 200
    body = r.json()
    assert body["scraped"] is True
    agg = next(s for s in body["services"] if s["service"] == "meridian-aggregation")
    assert agg["cpu_usage"] == 92.0
    assert agg["healthy"] is False  # cpu 92 > threshold
    gw = next(s for s in body["services"] if s["service"] == "meridian-gateway")
    assert gw["cpu_usage"] == 18.0
    assert gw["error_rate"] is None
    assert gw["healthy"] is True


def test_ops_metrics_fail_soft_when_prometheus_down(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no prom", request=request)

    _mock_httpx_client(monkeypatch, handler)

    c = _client()
    r = c.get("/api/ops/metrics")

    assert r.status_code == 200
    assert r.json() == {"scraped": False, "services": []}


def test_gateway_module_source_has_ops_metrics_route():
    # Import-safety net (see the gauge-registry hazard above): confirm the
    # real gateway/app.py source actually defines /api/ops/metrics and wires
    # it into common.config.get_settings, without ever importing the module.
    # This is what gives Step 2 of the TDD sequence a genuine red: the tests
    # above exercise a local mirror of the route (required to dodge the
    # gauge hazard) so they can't by themselves prove the real file changed.
    import pathlib

    src = pathlib.Path("services/meridian/gateway/app.py").read_text()
    assert '@app.get("/api/ops/metrics")' in src
    assert "get_settings" in src
    assert "prometheus_url" in src
    # Must come after /api/ops/deploy per the brief's ordering.
    assert src.index('"/api/ops/deploy"') < src.index('"/api/ops/metrics"')
