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


# --- Requirements 3.1, 3.2: bus backend configuration ---

def test_bus_backend_default(monkeypatch):
    monkeypatch.delenv("INTELLIOPS_BUS_BACKEND", raising=False)
    s = Settings()
    assert s.bus_backend == "redis"


def test_bus_backend_reads_from_env(monkeypatch):
    monkeypatch.setenv("INTELLIOPS_BUS_BACKEND", "kafka")
    s = Settings()
    assert s.bus_backend == "kafka"


def test_kafka_bootstrap_servers_default(monkeypatch):
    monkeypatch.delenv("INTELLIOPS_KAFKA_BOOTSTRAP_SERVERS", raising=False)
    s = Settings()
    assert s.kafka_bootstrap_servers == "localhost:9092"


def test_kafka_bootstrap_servers_reads_from_env(monkeypatch):
    monkeypatch.setenv("INTELLIOPS_KAFKA_BOOTSTRAP_SERVERS", "broker1:9092,broker2:9092")
    s = Settings()
    assert s.kafka_bootstrap_servers == "broker1:9092,broker2:9092"
