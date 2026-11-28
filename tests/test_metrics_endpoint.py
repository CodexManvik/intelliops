from fastapi.testclient import TestClient

from services.base import create_app


def test_metrics_counts_requests_by_route_template_and_skips_auth(monkeypatch):
    monkeypatch.setenv("INTELLIOPS_AUTH_MODE", "token")
    monkeypatch.setenv("INTELLIOPS_AUTH_TOKEN", "t")
    from common.config import get_settings

    get_settings.cache_clear()
    try:
        app = create_app("metrics-test")

        @app.get("/things/{thing_id}")
        def thing(thing_id: str) -> dict:
            return {"id": thing_id}

        client = TestClient(app)
        assert client.get("/things/42", headers={"Authorization": "Bearer t"}).status_code == 200
        body = client.get("/_metrics")  # no token: must stay scrapeable
        assert body.status_code == 200
        assert (
            'intelliops_http_requests_total{method="GET",path="/things/{thing_id}",'
            'service="metrics-test",status="200"} 1.0' in body.text
        )
    finally:
        get_settings.cache_clear()


def test_metrics_can_be_disabled():
    assert TestClient(create_app("no-metrics", metrics=False)).get("/_metrics").status_code == 404
