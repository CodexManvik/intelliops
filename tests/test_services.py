import importlib

import pytest
from fastapi.testclient import TestClient

SERVICES = [
    ("ingestion", "ingestion-service"),
    ("correlation", "correlation-service"),
    ("rca", "rca-service"),
    ("action", "action-service"),
    ("governance", "governance-service"),
    ("feedback", "feedback-service"),
]


@pytest.mark.parametrize("module_name, service_name", SERVICES)
def test_health_endpoint(module_name, service_name):
    mod = importlib.import_module(f"services.{module_name}.app")
    client = TestClient(mod.app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"service": service_name, "status": "ok"}


def test_create_app_attaches_bus():
    from common.interfaces import BusClient
    from services.base import create_app

    app = create_app("test-service")
    assert isinstance(app.state.bus, BusClient)
