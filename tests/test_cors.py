from fastapi.testclient import TestClient

from services.base import create_app


def test_cors_headers_present_for_allowed_origin():
    app = create_app("test-service")
    client = TestClient(app)
    r = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
