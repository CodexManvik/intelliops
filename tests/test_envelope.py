from datetime import UTC, datetime

from common.contracts import Situation, SituationStatus, TelemetryEvent, TelemetryKind
from common.envelope import decode_model, iter_models, publish_model

NOW = datetime(2026, 8, 13, tzinfo=UTC)


class FakeBus:
    def __init__(self):
        self.published: list[tuple[str, dict]] = []
        self._to_consume: dict[str, list[dict]] = {}

    def publish(self, topic, message):
        self.published.append((topic, message))

    def consume(self, topic, group):
        yield from self._to_consume.get(topic, [])


def _event(name="cpu", value=0.9):
    return TelemetryEvent(
        source="prometheus",
        kind=TelemetryKind.METRIC,
        name=name,
        value=value,
        labels={"pod": "web-1"},
        ts=NOW,
        fingerprint="fp1",
    )


def test_publish_model_wraps_json_in_data_field():
    bus = FakeBus()
    publish_model(bus, "telemetry.raw", _event())
    topic, message = bus.published[0]
    assert topic == "telemetry.raw"
    assert set(message.keys()) == {"data"}
    assert '"name":"cpu"' in message["data"]


def test_decode_model_roundtrips():
    bus = FakeBus()
    publish_model(bus, "telemetry.raw", _event())
    _topic, message = bus.published[0]
    restored = decode_model(message, TelemetryEvent)
    assert restored == _event()


def test_iter_models_yields_typed_models():
    bus = FakeBus()
    ev = _event()
    bus._to_consume["telemetry.raw"] = [{"data": ev.model_dump_json()}]
    out = list(iter_models(bus, "telemetry.raw", "g1", TelemetryEvent))
    assert out == [ev]


def test_iter_models_handles_situation_with_members():
    bus = FakeBus()
    sit = Situation(
        id="s1",
        status=SituationStatus.DETECTED,
        member_events=[_event(), _event("mem", 0.8)],
        severity="high",
        first_seen=NOW,
        last_seen=NOW,
        signature="sig1",
    )
    bus._to_consume["situations.detected"] = [{"data": sit.model_dump_json()}]
    out = list(iter_models(bus, "situations.detected", "g1", Situation))
    assert out == [sit]
    assert len(out[0].member_events) == 2
