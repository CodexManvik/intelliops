"""Tests for the Meridian gateway's ops-proxy, /admin/deploy, and the
StaticFiles UI mount (services/meridian/gateway/app.py, Task 3).

GAUGE-REGISTRY HAZARD: this file must NOT import services.meridian.gateway.app
(or anything that imports it) — see test_metrics.py's docstring for the full
explanation. Merely importing that module executes its top-level
`app = make_meridian_service("meridian-gateway", _routes)`, registering gauges
on prometheus_client's process-wide default registry a second time (demo_app's
tests already register the same bare `cpu_usage` gauge there, and get
collected first in the full `pytest -m "not postgres and not kafka" -q` run),
raising "Duplicated timeseries in CollectorRegistry". So instead of importing
the real module, `_ops_routes` below is a line-for-line mirror of the
ops-proxy/deploy routes added to gateway/app.py in Task 3, applied to a
registry-isolated app the same way test_services.py/test_metrics.py do.

httpx.Client is monkeypatched at the `httpx` module level (not
dependency-injected — the gateway's ops_fault/ops_clear routes construct
their own short-lived `httpx.Client(...)` per the Task-3 brief) so a
MockTransport-backed client stands in for the real network call and asserts
on the outgoing request.
"""

from __future__ import annotations

import json
import os

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry

from services.meridian.common import make_meridian_service

_RCA_CONTEXT_ENV = "INTELLIOPS_RCA_CONTEXT_PATH"
_MERIDIAN_SERVICES = {"gateway", "validation", "aggregation", "reporting"}


def _known_service(name: str) -> str:
    # Mirrors gateway/app.py._known_service — reject an unknown service before
    # it is interpolated into an in-cluster URL (request-forgery guard).
    if name not in _MERIDIAN_SERVICES:
        raise HTTPException(status_code=400, detail=f"Unknown Meridian service: {name!r}")
    return name


def _ops_routes(app, state) -> None:
    """Mirrors the ops-proxy + /admin/deploy routes in gateway/app.py."""

    @app.post("/api/submissions")
    def submit(payload: dict) -> dict:
        client = payload.get("client", "")
        period = payload.get("period", "")
        amount = payload.get("amount", 0.0)
        return {"accepted": True, "client": client, "period": period, "amount": amount}

    @app.get("/api/reports")
    def reports() -> dict:
        return {"reports": []}

    @app.post("/api/ops/fault")
    def ops_fault(body: dict) -> dict:
        svc = _known_service(body["service"])
        spec = body["spec"]
        url = f"http://meridian-{svc}:8000/admin/fault"
        with httpx.Client(timeout=5.0) as c:
            r = c.post(url, json=spec)
        return {"status": r.status_code}

    @app.post("/api/ops/clear")
    def ops_clear(body: dict) -> dict:
        svc = _known_service(body["service"])
        url = f"http://meridian-{svc}:8000/admin/clear"
        with httpx.Client(timeout=5.0) as c:
            r = c.post(url)
        return {"status": r.status_code}

    @app.post("/api/ops/deploy")
    def ops_deploy(body: dict) -> dict:
        # Read the env var at call time (like the real route reads the
        # module-level _RCA_CONTEXT computed at import time) so tests can
        # monkeypatch it per-test via os.environ.
        rca_context = os.environ.get(_RCA_CONTEXT_ENV, "data/rca_context")
        svc_name = f"meridian-{_known_service(body['service'])}"
        os.makedirs(rca_context, exist_ok=True)
        path = os.path.join(rca_context, "deploys.json")
        from datetime import UTC, datetime

        entry = {
            "service": svc_name,
            "version": body.get("version", "v2.3.1"),
            "ts": datetime.now(UTC).isoformat(),
        }
        with open(path, "w") as f:
            json.dump([entry], f)
        return {"deployed": svc_name}


def _client() -> TestClient:
    app = make_meridian_service("meridian-gateway", _ops_routes, registry=CollectorRegistry())
    return TestClient(app)


def _mock_httpx_client(monkeypatch, handler):
    class _MockClient(httpx.Client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", _MockClient)


def test_ops_fault_proxies_to_the_right_meridian_service(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"applied": "saturation"})

    _mock_httpx_client(monkeypatch, handler)

    c = _client()
    r = c.post(
        "/api/ops/fault",
        json={"service": "validation", "spec": {"type": "saturation", "magnitude": 1.0}},
    )

    assert r.status_code == 200
    assert r.json() == {"status": 200}
    assert captured["url"] == "http://meridian-validation:8000/admin/fault"
    assert captured["method"] == "POST"
    assert captured["body"] == {"type": "saturation", "magnitude": 1.0}


def test_ops_fault_targets_service_from_request_body(monkeypatch):
    seen_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, json={})

    _mock_httpx_client(monkeypatch, handler)

    c = _client()
    for svc in ("gateway", "aggregation", "reporting"):
        c.post("/api/ops/fault", json={"service": svc, "spec": {"type": "crash"}})
    assert seen_urls == [
        "http://meridian-gateway:8000/admin/fault",
        "http://meridian-aggregation:8000/admin/fault",
        "http://meridian-reporting:8000/admin/fault",
    ]


def test_ops_clear_proxies_to_admin_clear(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        return httpx.Response(200, json={"cleared": True})

    _mock_httpx_client(monkeypatch, handler)

    c = _client()
    r = c.post("/api/ops/clear", json={"service": "reporting"})

    assert r.status_code == 200
    assert r.json() == {"status": 200}
    assert captured["url"] == "http://meridian-reporting:8000/admin/clear"
    assert captured["method"] == "POST"


def test_ops_deploy_writes_deploys_json_with_expected_shape(tmp_path, monkeypatch):
    rca_context = tmp_path / "rca_context"
    monkeypatch.setenv(_RCA_CONTEXT_ENV, str(rca_context))

    c = _client()
    r = c.post("/api/ops/deploy", json={"service": "reporting", "version": "v9.9.9"})

    assert r.status_code == 200
    assert r.json() == {"deployed": "meridian-reporting"}

    deploys_path = rca_context / "deploys.json"
    assert deploys_path.exists()
    entries = json.loads(deploys_path.read_text())
    assert isinstance(entries, list)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["service"] == "meridian-reporting"
    assert entry["version"] == "v9.9.9"
    assert entry.get("ts")


def test_ops_deploy_defaults_version_when_omitted(tmp_path, monkeypatch):
    rca_context = tmp_path / "rca_context"
    monkeypatch.setenv(_RCA_CONTEXT_ENV, str(rca_context))

    c = _client()
    r = c.post("/api/ops/deploy", json={"service": "validation"})

    assert r.status_code == 200
    entries = json.loads((rca_context / "deploys.json").read_text())
    assert entries[0]["version"] == "v2.3.1"
    assert entries[0]["service"] == "meridian-validation"


def test_ops_deploy_creates_rca_context_dir_if_missing(tmp_path, monkeypatch):
    rca_context = tmp_path / "does" / "not" / "exist" / "yet"
    monkeypatch.setenv(_RCA_CONTEXT_ENV, str(rca_context))
    assert not rca_context.exists()

    c = _client()
    r = c.post("/api/ops/deploy", json={"service": "gateway"})

    assert r.status_code == 200
    assert (rca_context / "deploys.json").exists()


def test_gateway_module_source_guards_static_mount_on_exists():
    # Import-safety net: confirm the actual gateway/app.py source contains
    # the `.exists()` guard around the StaticFiles mount, and that the mount
    # is the LAST thing in the file (after every /api, /admin, /metrics
    # route) — without ever importing the module (see the gauge-registry
    # hazard explained in the module docstring).
    import pathlib

    src = pathlib.Path("services/meridian/gateway/app.py").read_text()
    assert "if _ui.exists():" in src
    assert 'app.mount("/", StaticFiles(directory=str(_ui), html=True), name="ui")' in src
    mount_idx = src.index("app.mount(")
    # Every route decorator must appear before the mount line.
    for marker in ("/api/submissions", "/api/reports", "/api/ops/fault", "/api/ops/deploy"):
        assert src.index(marker) < mount_idx


def test_static_mount_not_registered_when_dist_dir_absent(tmp_path):
    # Exercises the exact guarded-mount snippet from gateway/app.py against a
    # registry-isolated app (avoids importing the real module). When ui/dist
    # doesn't exist, "/" must NOT become a route.
    from fastapi.staticfiles import StaticFiles

    app = make_meridian_service("meridian-gateway", _ops_routes, registry=CollectorRegistry())
    missing_ui = tmp_path / "ui" / "dist"
    assert not missing_ui.exists()
    if missing_ui.exists():  # pragma: no cover - mirrors the real guard
        app.mount("/", StaticFiles(directory=str(missing_ui), html=True), name="ui")

    c = TestClient(app)
    assert c.get("/health").status_code == 200
    assert c.get("/").status_code == 404


def test_static_mount_registered_when_dist_dir_present(tmp_path):
    # Same snippet, but with a real (empty) dist dir containing an index.html
    # -> the guard now mounts StaticFiles at "/" and it serves the file.
    from fastapi.staticfiles import StaticFiles

    ui_dist = tmp_path / "ui" / "dist"
    ui_dist.mkdir(parents=True)
    (ui_dist / "index.html").write_text("<html>meridian ui</html>")

    app = make_meridian_service("meridian-gateway", _ops_routes, registry=CollectorRegistry())
    if ui_dist.exists():
        app.mount("/", StaticFiles(directory=str(ui_dist), html=True), name="ui")

    c = TestClient(app)
    r = c.get("/")
    assert r.status_code == 200
    assert "meridian ui" in r.text


@pytest.mark.parametrize("bad_svc", ["", "not-a-real-service", "evil-host", "gateway:9999"])
def test_ops_fault_rejects_unknown_service_and_never_proxies(monkeypatch, bad_svc):
    # The proxy interpolates `service` into an in-cluster URL, so an
    # unvalidated value is a request-forgery seam. Only the four Meridian
    # services are legitimate targets; anything else is a 400 and NO
    # outbound request is ever made.
    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200, json={})

    _mock_httpx_client(monkeypatch, handler)

    c = _client()
    r = c.post("/api/ops/fault", json={"service": bad_svc, "spec": {"type": "crash"}})
    assert r.status_code == 400
    assert called["n"] == 0  # never proxied to a forged host
