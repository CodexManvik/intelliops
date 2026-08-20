import threading

from services.ingestion.app import run_poll_loop


class OneShotSource:
    def __init__(self, events):
        self._events = events
        self.calls = 0

    def poll(self):
        self.calls += 1
        return self._events if self.calls == 1 else []


class RecordingBus:
    def __init__(self):
        self.published = []

    def publish(self, topic, message):
        self.published.append((topic, message))


def _event(name="http_request_errors_total", value=7.0):
    from datetime import UTC, datetime

    from common.contracts import TelemetryEvent, TelemetryKind

    return TelemetryEvent(
        source="prometheus",
        kind=TelemetryKind.METRIC,
        name=name,
        value=value,
        labels={},
        ts=datetime.now(UTC),
        fingerprint="fp1",
    )


def test_poll_loop_publishes_events_then_stops():
    bus = RecordingBus()
    src = OneShotSource([_event()])
    stop = threading.Event()

    # stop after the first non-empty batch by flipping the event from a wrapper source
    class StopAfterFirst:
        def poll(self):
            evs = src.poll()
            if evs:
                return evs
            stop.set()
            return []

    run_poll_loop(bus, StopAfterFirst(), interval=0.0, stop_event=stop)
    assert len(bus.published) == 1
    assert bus.published[0][0] == "telemetry.raw"
    assert "data" in bus.published[0][1]
