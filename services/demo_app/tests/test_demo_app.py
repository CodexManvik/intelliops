# services/demo_app/tests/test_demo_app.py
from fastapi.testclient import TestClient

from common.config import get_settings
from services.demo_app.app import _state, app


def _client():
    from common.config import get_settings

    get_settings.cache_clear()
    _state["broken"] = False
    return TestClient(app)


def test_work_ok_when_healthy():
    c = _client()
    assert c.get("/work").status_code == 200


def test_break_makes_work_error_and_cpu_spike():
    c = _client()
    c.post("/break")
    assert c.get("/work").status_code == 500
    body = c.get("/metrics").text
    assert "cpu_usage" in body
    # cpu gauge should read high (>= 80) when broken
    line = next(l for l in body.splitlines() if l.startswith("cpu_usage "))
    assert float(line.split()[1]) >= 80.0


def test_fix_recovers():
    c = _client()
    c.post("/break")
    c.post("/fix")
    assert c.get("/work").status_code == 200


def test_break_and_fix_open_by_default(monkeypatch):
    monkeypatch.setenv("INTELLIOPS_AUTH_MODE", "off")
    get_settings.cache_clear()
    c = _client()
    assert c.post("/break").status_code == 200
    assert c.post("/fix").status_code == 200
    get_settings.cache_clear()


def test_break_and_fix_gated_in_token_mode(monkeypatch):
    monkeypatch.setenv("INTELLIOPS_AUTH_MODE", "token")
    monkeypatch.setenv("INTELLIOPS_AUTH_TOKEN", "secret")
    get_settings.cache_clear()
    c = _client()
    assert c.post("/break").status_code == 401
    r = c.post("/break", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    get_settings.cache_clear()


def test_health_and_metrics_stay_open_in_token_mode(monkeypatch):
    monkeypatch.setenv("INTELLIOPS_AUTH_MODE", "token")
    monkeypatch.setenv("INTELLIOPS_AUTH_TOKEN", "secret")
    get_settings.cache_clear()
    c = _client()
    assert c.get("/health").status_code == 200
    assert c.get("/metrics").status_code == 200
    assert c.get("/work").status_code == 200
    get_settings.cache_clear()
