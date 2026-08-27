from fastapi.testclient import TestClient

from services.rca.app import app


def test_config_llm_swaps_provider_and_never_echoes_key():
    with TestClient(app) as client:
        # default: template
        r = client.get("/config/llm")
        assert r.json()["provider"] == "template"
        # set an (unreachable) endpoint → provider becomes openai-compatible
        r = client.post(
            "/config/llm",
            json={
                "endpoint": "http://127.0.0.1:1/v1",
                "api_key": "secret-key",
                "model": "gpt-4o-mini",
            },
        )
        body = r.json()
        assert body["provider"] == "openai-compatible"
        assert "secret-key" not in str(body)  # key never echoed
        # test-connection against the dead endpoint → ok False, falls back
        r = client.post(
            "/config/llm/test",
            json={"endpoint": "http://127.0.0.1:1/v1", "api_key": "k", "model": "gpt-4o-mini"},
        )
        assert r.json()["ok"] is False
        # clear → back to template
        r = client.post("/config/llm", json={"endpoint": "", "api_key": "", "model": "gpt-4o-mini"})
        assert r.json()["provider"] == "template"
