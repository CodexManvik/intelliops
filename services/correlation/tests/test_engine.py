import random
from datetime import UTC, datetime

from common.contracts import Situation, TelemetryEvent, TelemetryKind
from services.correlation.adapters.river_correlator import RiverCorrelator
from services.correlation.engine import CorrelationEngine


def _event(value=10.0, fp="fp", ts_sec=0):
    return TelemetryEvent(
        source="prom",
        kind=TelemetryKind.METRIC,
        name="cpu",
        value=value,
        labels={},
        ts=datetime(2026, 8, 13, 0, 0, ts_sec, tzinfo=UTC),
        fingerprint=fp,
    )


def _prime(engine):
    # Feed a jittered baseline so later spikes are anomalous but normal values
    # are not. A dead-flat baseline drives std dev to ~0 and makes the z-score
    # explode on any deviation. Seeded for determinism.
    #
    # NOTE: river.stats.Var is unstable during warm-up (the first ~50 samples),
    # so a few early jittered values legitimately cross the z-threshold and get
    # buffered. Since they all share ts_sec=0 the window never advances to flush
    # them. Flush once here to discard that warm-up noise so each test starts
    # from a clean buffer — this mirrors a real deployment warming up before it
    # trusts anomalies.
    rng = random.Random(42)
    for i in range(200):
        engine.add(_event(value=round(rng.gauss(10.0, 1.0), 3), fp=f"base{i}", ts_sec=0))
    engine.flush()  # discard warm-up noise; return value intentionally ignored


def test_non_anomalous_events_return_none():
    engine = CorrelationEngine(RiverCorrelator(), window_seconds=30)
    _prime(engine)
    assert engine.add(_event(value=10.1, fp="x", ts_sec=1)) is None


def test_flush_emits_situation_from_buffered_anomalies():
    engine = CorrelationEngine(RiverCorrelator(), window_seconds=30)
    _prime(engine)
    # three spikes within the window -> buffered, no emit yet
    assert engine.add(_event(value=100.0, fp="a", ts_sec=1)) is None
    assert engine.add(_event(value=120.0, fp="b", ts_sec=2)) is None
    sit = engine.flush()
    assert isinstance(sit, Situation)
    assert {e.fingerprint for e in sit.member_events} == {"a", "b"}
    assert sit.severity in {"high", "medium", "low"}


def test_window_span_triggers_emit():
    engine = CorrelationEngine(RiverCorrelator(), window_seconds=10)
    _prime(engine)
    engine.add(_event(value=100.0, fp="a", ts_sec=1))  # buffer starts at t=1
    # an anomaly at t=15 is >10s past buffer start -> flush old buffer, return it
    emitted = engine.add(_event(value=100.0, fp="b", ts_sec=15))
    assert isinstance(emitted, Situation)
    assert {e.fingerprint for e in emitted.member_events} == {"a"}
    # new buffer now holds "b"
    tail = engine.flush()
    assert {e.fingerprint for e in tail.member_events} == {"b"}


def test_flush_empty_returns_none():
    engine = CorrelationEngine(RiverCorrelator(), window_seconds=30)
    assert engine.flush() is None


def test_engine_snapshot_load_roundtrip():
    def ev(v):
        return TelemetryEvent(
            source="prom",
            kind=TelemetryKind.METRIC,
            name="cpu_usage",
            value=v,
            labels={},
            ts=datetime(2026, 8, 20, tzinfo=UTC),
            fingerprint="cpu_usage",
        )

    e1 = CorrelationEngine(RiverCorrelator(z_threshold=3.0, warmup_samples=50))
    for v in [50.0 + (i % 5) for i in range(60)]:
        e1.add(ev(v))
    rows = e1.snapshot()

    e2 = CorrelationEngine(RiverCorrelator(z_threshold=3.0, warmup_samples=50))
    e2.load(rows)
    # e2's correlator is warmed - a spike is detected (add returns/ buffers it)
    assert e2._correlator.is_anomaly(ev(500.0))
