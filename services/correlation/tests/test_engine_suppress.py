import random
from datetime import UTC, datetime

from common.contracts import TelemetryEvent, TelemetryKind
from services.correlation.adapters.river_correlator import RiverCorrelator
from services.correlation.engine import CorrelationEngine


def _event(value, fp, ts_sec=0):
    return TelemetryEvent(
        source="prom",
        kind=TelemetryKind.METRIC,
        name="cpu",
        value=value,
        labels={},
        ts=datetime(2026, 8, 13, 0, 0, ts_sec, tzinfo=UTC),
        fingerprint=fp,
    )


def _prime_and_flush(engine, n=200, seed=42):
    rng = random.Random(seed)
    for i in range(n):
        engine.add(_event(round(rng.gauss(10.0, 1.0), 3), f"b{i}", 0))
    engine.flush()


def test_engine_suppresses_reliable_signature():
    correlator = RiverCorrelator(z_threshold=3.0)
    engine = CorrelationEngine(correlator, window_seconds=30, suppress_threshold=0.8)
    _prime_and_flush(engine)

    # First, form a Situation from two spikes to learn its signature.
    engine.add(_event(100.0, "a", 1))
    engine.add(_event(120.0, "b", 2))
    situation = engine.flush()
    assert situation is not None
    sig = situation.signature

    # Teach the correlator that this signature reliably self-heals.
    correlator.retrain(
        [
            {"signature": sig, "worked": True},
            {"signature": sig, "worked": True},
            {"signature": sig, "worked": True},
        ]
    )

    # The SAME spikes now form the same-signature Situation, which is suppressed.
    engine.add(_event(100.0, "a", 3))
    engine.add(_event(120.0, "b", 4))
    suppressed = engine.flush()
    assert suppressed is None  # reliably-self-healing signature is suppressed


def test_engine_still_emits_unreliable_signature():
    correlator = RiverCorrelator(z_threshold=3.0)
    engine = CorrelationEngine(correlator, window_seconds=30, suppress_threshold=0.8)
    _prime_and_flush(engine)

    engine.add(_event(100.0, "a", 1))
    engine.add(_event(120.0, "b", 2))
    situation = engine.flush()
    sig = situation.signature

    # This signature keeps FAILING — stays sensitive.
    correlator.retrain([{"signature": sig, "worked": False}, {"signature": sig, "worked": False}])

    engine.add(_event(100.0, "a", 3))
    engine.add(_event(120.0, "b", 4))
    still_emitted = engine.flush()
    assert still_emitted is not None  # unreliable signature is NOT suppressed
