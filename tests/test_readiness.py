from fastapi.testclient import TestClient

from services.base import create_app


class _OkBus:
    def ping(self):
        pass


class _DeadBus:
    def ping(self):
        raise ConnectionError("redis down")


def _client(bus, readiness=None):
    app = create_app("test-service", readiness=readiness)
    app.state.bus = bus  # override the real bus with a fake
    return TestClient(app)


def test_ready_200_when_bus_ok_no_db():
    c = _client(_OkBus())
    r = c.get("/ready")
    assert r.status_code == 200 and r.json() == {"ready": True}


def test_ready_503_when_bus_down():
    c = _client(_DeadBus())
    r = c.get("/ready")
    assert r.status_code == 503 and r.json()["failed"] == ["redis"]


def test_ready_503_when_db_check_fails():
    def _bad_db():
        raise RuntimeError("db down")

    c = _client(_OkBus(), readiness=_bad_db)
    r = c.get("/ready")
    assert r.status_code == 503 and r.json()["failed"] == ["postgres"]


def test_ready_503_both_down():
    def _bad_db():
        raise RuntimeError("db down")

    c = _client(_DeadBus(), readiness=_bad_db)
    r = c.get("/ready")
    assert r.status_code == 503 and set(r.json()["failed"]) == {"redis", "postgres"}


def test_ready_200_with_passing_db():
    c = _client(_OkBus(), readiness=lambda: None)
    assert c.get("/ready").status_code == 200


def test_health_still_liveness_only():
    c = _client(_DeadBus())  # bus down
    assert c.get("/health").status_code == 200  # liveness unaffected


def test_ready_exempt_under_auth_token(monkeypatch):
    from common.config import get_settings

    monkeypatch.setenv("INTELLIOPS_AUTH_MODE", "token")
    monkeypatch.setenv("INTELLIOPS_AUTH_TOKEN", "secret")
    get_settings.cache_clear()
    c = _client(_OkBus())
    assert c.get("/ready").status_code == 200  # reachable without a token
    get_settings.cache_clear()
