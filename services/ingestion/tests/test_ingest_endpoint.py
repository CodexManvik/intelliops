from fastapi.testclient import TestClient

from common.contracts import TelemetryEvent
from common.envelope import decode_model


class RecordingBus:
    def __init__(self):
        self.published = []

    def publish(self, topic, message):
        self.published.append((topic, message))

    def consume(self, topic, group):
        yield from ()


def _client():
    from services.ingestion.app import app

    app.state.bus = RecordingBus()
    return TestClient(app), app.state.bus


def test_ingest_publishes_normalized_events_to_telemetry_raw():
    client, bus = _client()
    resp = client.post("/ingest", json={"events": [
        {"source": "prom", "kind": "metric", "name": "cpu", "value": 0.9,
         "labels": {"pod": "web-1"}, "ts": "2026-08-13T00:00:00+00:00"},
    ]})
    assert resp.status_code == 200
    assert resp.json() == {"accepted": 1}
    assert len(bus.published) == 1
    topic, message = bus.published[0]
    assert topic == "telemetry.raw"
    ev = decode_model(message, TelemetryEvent)
    assert ev.name == "cpu"
    assert ev.fingerprint  # computed


def test_ingest_defaults_missing_ts():
    client, bus = _client()
    resp = client.post("/ingest", json={"events": [
        {"source": "prom", "kind": "metric", "name": "cpu", "value": 0.1},
    ]})
    assert resp.status_code == 200
    ev = decode_model(bus.published[0][1], TelemetryEvent)
    assert ev.ts is not None


def test_ingest_empty_batch_accepts_zero():
    client, bus = _client()
    resp = client.post("/ingest", json={"events": []})
    assert resp.json() == {"accepted": 0}
    assert bus.published == []


def test_health_still_works():
    client, _ = _client()
    assert client.get("/health").json() == {"service": "ingestion-service", "status": "ok"}
