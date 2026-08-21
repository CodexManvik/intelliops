from datetime import UTC, datetime

from common.contracts import AuditRecord
from common.interfaces import (
    AuditSink,
    BusClient,
    Correlator,
    Remediator,
    TelemetrySource,
)


class FakeBus:
    """A structural BusClient used to prove the Protocol is satisfiable."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    def publish(self, topic: str, message: dict) -> None:
        self.published.append((topic, message))

    def consume(self, topic: str, group: str):
        yield from ()

    def ping(self) -> None:
        pass


class FakeAudit:
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def write(self, record: AuditRecord) -> None:
        self.records.append(record)


def test_fakebus_satisfies_protocol():
    bus: BusClient = FakeBus()
    bus.publish("telemetry.raw", {"k": "v"})
    assert isinstance(bus, BusClient)  # runtime_checkable


def test_fake_audit_satisfies_protocol():
    sink: AuditSink = FakeAudit()
    sink.write(
        AuditRecord(
            actor="a",
            action="b",
            resource="c",
            decision="allow",
            ts=datetime(2026, 8, 13, tzinfo=UTC),
            correlation_id="x",
        )
    )
    assert isinstance(sink, AuditSink)


def test_protocols_are_importable():
    # Presence + runtime-checkable is enough at skeleton stage.
    for proto in (BusClient, TelemetrySource, Correlator, Remediator, AuditSink):
        assert hasattr(proto, "__protocol_attrs__") or hasattr(proto, "_is_protocol")
