import random
import threading
from datetime import UTC, datetime

from common.contracts import Situation, TelemetryEvent, TelemetryKind
from common.envelope import decode_model
from services.correlation.adapters.river_correlator import RiverCorrelator
from services.correlation.consumer import run_consumer
from services.correlation.engine import CorrelationEngine


def _raw_event(value, fp, ts_sec):
    ev = TelemetryEvent(
        source="prom", kind=TelemetryKind.METRIC, name="cpu", value=value,
        labels={}, ts=datetime(2026, 8, 13, 0, 0, ts_sec, tzinfo=UTC),
        fingerprint=fp,
    )
    return {"data": ev.model_dump_json()}


def _event(value, fp, ts_sec):
    return TelemetryEvent(
        source="prom", kind=TelemetryKind.METRIC, name="cpu", value=value,
        labels={}, ts=datetime(2026, 8, 13, 0, 0, ts_sec, tzinfo=UTC),
        fingerprint=fp,
    )


def _prime_and_flush(engine, n=200, seed=42):
    """Warm the engine's per-metric baseline with jittered values, then flush.

    A dead-flat baseline drives std dev to ~0 and makes any deviation explode
    into a huge z-score. Even with jitter, river.stats.Var is UNSTABLE during
    warm-up (the first ~50 samples): a few early baseline values legitimately
    cross the z-threshold and get buffered as spurious anomalies. Since those
    baseline events all share ts_sec=0, the window never advances to flush them.
    So we prime the engine directly, then flush() once to DISCARD that warm-up
    noise — mirroring a real deployment that warms up before trusting anomalies.
    Seeded for determinism. Verified empirically against river 0.25.
    """
    rng = random.Random(seed)
    for i in range(n):
        engine.add(_event(round(rng.gauss(10.0, 1.0), 3), f"b{i}", 0))
    engine.flush()  # discard warm-up noise; return value intentionally ignored


class ScriptedBus:
    """A finite bus: consume() yields a fixed script then stops."""

    def __init__(self, script):
        self._script = script
        self.published = []

    def publish(self, topic, message):
        self.published.append((topic, message))

    def consume(self, topic, group):
        yield from self._script


def test_consumer_emits_situation_for_correlated_anomalies():
    # Pre-warm the engine, THEN feed only the two spikes through the consumer.
    engine = CorrelationEngine(RiverCorrelator(z_threshold=3.0), window_seconds=30)
    _prime_and_flush(engine)

    bus = ScriptedBus([_raw_event(100.0, "a", 1), _raw_event(120.0, "b", 2)])
    run_consumer(bus, engine, threading.Event())

    situations = [m for (t, m) in bus.published if t == "situations.detected"]
    assert len(situations) == 1
    sit = decode_model(situations[0], Situation)
    assert {e.fingerprint for e in sit.member_events} == {"a", "b"}


def test_consumer_publishes_nothing_without_anomalies():
    # Pre-warm, then feed only normal (near-mean) events -> no anomalies emitted.
    engine = CorrelationEngine(RiverCorrelator(z_threshold=3.0), window_seconds=30)
    _prime_and_flush(engine)

    bus = ScriptedBus([_raw_event(10.0, "n1", 1), _raw_event(10.1, "n2", 2)])
    run_consumer(bus, engine, threading.Event())
    assert [m for (t, m) in bus.published if t == "situations.detected"] == []


def test_consumer_stops_on_stop_event():
    # An infinite script; stop_event is pre-set so the loop exits immediately.
    def infinite():
        while True:
            yield _raw_event(10.0, "b", 0)

    class InfBus(ScriptedBus):
        def consume(self, topic, group):
            return infinite()

    bus = InfBus([])
    stop = threading.Event()
    stop.set()
    run_consumer(bus, engine=CorrelationEngine(RiverCorrelator()), stop_event=stop)
    assert bus.published == []
