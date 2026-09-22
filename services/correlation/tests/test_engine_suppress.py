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


def test_every_bucket_suppressed_in_one_flush_is_reported():
    # With group_by="service", one flush_all() can suppress several buckets. The
    # engine used to keep suppressed situations in a single slot, so only the
    # last one ever reached situations.suppressed.
    correlator = RiverCorrelator(z_threshold=3.0)
    engine = CorrelationEngine(correlator, window_seconds=30, group_by="service")
    ev_a = _event(1.0, "fa").model_copy(update={"labels": {"service": "a"}})
    ev_b = _event(1.0, "fb").model_copy(update={"labels": {"service": "b"}})
    engine._buffers = {"a": [ev_a], "b": [ev_b]}
    engine._max_scores = {"a": 9.0, "b": 9.0}
    correlator.retrain(
        [
            {"signature": correlator._signature([ev_a]), "worked": True},
            {"signature": correlator._signature([ev_b]), "worked": True},
        ]
    )

    assert engine.flush_all() == []
    suppressed = []
    while (s := engine.pop_suppressed()) is not None:
        suppressed.append(s)
    assert len(suppressed) == 2


def _suppressible_engine(mode):
    correlator = RiverCorrelator(z_threshold=3.0)
    engine = CorrelationEngine(correlator, window_seconds=30, suppress_mode=mode)
    ev = _event(1.0, "fq")
    engine._buffers = {engine._ALL: [ev]}
    engine._max_scores = {engine._ALL: 9.0}
    correlator.retrain([{"signature": correlator._signature([ev]), "worked": True}])
    return engine


def test_quiet_mode_still_emits_the_situation_for_remediation():
    engine = _suppressible_engine("quiet")
    emitted = engine.flush_all()
    assert len(emitted) == 1 and emitted[0].handling == "quiet"
    logged = engine.pop_suppressed()  # still recorded as a suppression
    assert logged is not None and logged.handling == "quiet"


def test_drop_mode_keeps_the_historical_behaviour():
    engine = _suppressible_engine("drop")
    assert engine.flush_all() == []
    assert engine.pop_suppressed() is not None


def test_a_baseline_reset_keeps_what_was_learned_about_fixes():
    engine = _suppressible_engine("quiet")
    engine.reset()
    assert engine._correlator.reliability(next(iter(engine._correlator._reliability))) == 1.0
