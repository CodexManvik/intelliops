from fastapi.testclient import TestClient

from common.config import get_settings
from services.base import create_app


def _client(monkeypatch, mode: str, token: str = "") -> TestClient:
    monkeypatch.setenv("INTELLIOPS_AUTH_MODE", mode)
    monkeypatch.setenv("INTELLIOPS_AUTH_TOKEN", token)
    get_settings.cache_clear()
    return TestClient(create_app("test-service"))


def _clear_cache():
    get_settings.cache_clear()


def test_off_mode_leaves_everything_open(monkeypatch):
    client = _client(monkeypatch, "off")
    assert client.get("/health").status_code == 200
    # off mode gates nothing, even paths that don't exist on this app --
    # they should 404 from routing, not 401 from the auth gate.
    assert client.get("/situations").status_code == 404
    _clear_cache()


def test_health_open_even_in_token_mode(monkeypatch):
    client = _client(monkeypatch, "token", "secret")
    assert client.get("/health").status_code == 200
    _clear_cache()


def test_token_mode_blocks_missing_token(monkeypatch):
    client = _client(monkeypatch, "token", "secret")
    assert client.get("/situations").status_code == 401
    _clear_cache()


def test_token_mode_blocks_wrong_token(monkeypatch):
    client = _client(monkeypatch, "token", "secret")
    r = client.get("/situations", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    _clear_cache()


def test_token_mode_allows_correct_token(monkeypatch):
    client = _client(monkeypatch, "token", "secret")
    r = client.get("/situations", headers={"Authorization": "Bearer secret"})
    # Auth passes; this app has no /situations route, so routing 404s --
    # the point is it's a 404, not a 401.
    assert r.status_code == 404
    _clear_cache()
