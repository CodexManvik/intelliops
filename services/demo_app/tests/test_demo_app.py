# services/demo_app/tests/test_demo_app.py
from fastapi.testclient import TestClient

from services.demo_app.app import app, _state


def _client():
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
