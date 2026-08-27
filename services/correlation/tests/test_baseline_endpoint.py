from fastapi.testclient import TestClient

from services.correlation.app import app


def test_baseline_endpoint_shape():
    with TestClient(app) as client:
        r = client.get("/baseline")
        assert r.status_code == 200
        body = r.json()
        assert "correlator_kind" in body
        assert isinstance(body["baselines"], list)
