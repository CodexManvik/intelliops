"""/ready reflects the governance service's DB engine health.

Builds the governance app the same way its module does — a readiness closure
that reads app.state.db_engine lazily and pings it via db_ready — then swaps in
a fake OK bus and fake engines to exercise both the DB-down (503) and DB-up
(200) paths without a real Postgres.
"""

from fastapi.testclient import TestClient

from services.base import create_app, db_ready


class _OkBus:
    def ping(self):
        pass


class _OkConn:
    """A minimal connect() context manager whose execute succeeds."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args, **kwargs):
        pass


class _DeadEngine:
    def connect(self):
        raise ConnectionError("cannot reach db")


class _OkEngine:
    def connect(self):
        return _OkConn()


def _client():
    app = create_app(
        "governance-service",
        readiness=lambda: db_ready(getattr(app.state, "db_engine", None)),
    )
    app.state.bus = _OkBus()  # bus is healthy; only the DB varies below
    return app, TestClient(app)


def test_ready_503_when_db_engine_unreachable():
    app, c = _client()
    app.state.db_engine = _DeadEngine()
    r = c.get("/ready")
    assert r.status_code == 503
    assert r.json()["failed"] == ["postgres"]


def test_ready_200_when_db_engine_reachable():
    app, c = _client()
    app.state.db_engine = _OkEngine()
    r = c.get("/ready")
    assert r.status_code == 200
    assert r.json() == {"ready": True}
