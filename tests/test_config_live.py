from common.config import Settings


def test_live_defaults_are_test_safe():
    s = Settings()
    assert s.telemetry_mode == "file"
    assert s.governance_mode == "in_process"
    assert s.prometheus_url == "http://localhost:9090"
    assert s.read_outcomes_max == 200


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("INTELLIOPS_TELEMETRY_MODE", "prometheus")
    monkeypatch.setenv("INTELLIOPS_GOVERNANCE_MODE", "http")
    s = Settings()
    assert s.telemetry_mode == "prometheus"
    assert s.governance_mode == "http"
