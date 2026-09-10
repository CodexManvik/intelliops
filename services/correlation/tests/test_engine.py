import random
from datetime import UTC, datetime

from common.contracts import Situation, TelemetryEvent, TelemetryKind
from services.correlation.adapters.river_correlator import RiverCorrelator
from services.correlation.detection_policy import DetectionPolicy
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


def test_emitted_situation_carries_peak_score():
    from datetime import UTC, datetime, timedelta

    from common.contracts import TelemetryEvent, TelemetryKind
    from services.correlation.adapters.river_correlator import RiverCorrelator
    from services.correlation.engine import CorrelationEngine

    eng = CorrelationEngine(RiverCorrelator(z_threshold=3.0, warmup_samples=5), window_seconds=30.0)
    base = datetime(2026, 1, 1, tzinfo=UTC)

    def ev(v, i):
        return TelemetryEvent(
            source="test",
            kind=TelemetryKind.METRIC,
            name="cpu_usage",
            value=v,
            labels={"service": "web"},
            ts=base + timedelta(seconds=i),
            fingerprint="fp",
        )

    # river.stats.Var needs non-identical samples to produce a nonzero
    # variance (see RiverCorrelator.detect's sd == 0 guard); a perfectly
    # flat baseline never yields a std dev, so no value could ever score
    # as anomalous. Tiny deterministic jitter around 20 keeps this a
    # "~20 baseline" while giving the z-score something to divide by.
    jitter = [0.0, 0.1, -0.1, 0.2, -0.2, 0.1, -0.1, 0.2, -0.2, 0.1]
    for i in range(10):
        eng.add(ev(20.0 + jitter[i], i))  # learn a ~20 baseline
    eng.add(ev(200.0, 11))  # spike → scores high, buffers
    sit = eng.flush()
    assert sit is not None
    assert sit.peak_score is not None and sit.peak_score > 3.0
    assert sit.baseline is not None and "cpu_usage" in sit.baseline


def test_add_scores_under_lock():
    """detect() must run while the engine lock is held, so a concurrent
    snapshot()/load() on the flusher thread can never read a half-updated
    baseline. We wrap detect() to record whether the lock was locked when it
    ran (add() holds the same non-reentrant lock)."""
    correlator = RiverCorrelator(z_threshold=3.0, warmup_samples=1)
    engine = CorrelationEngine(correlator)
    seen_locked: list[bool] = []
    real_detect = correlator.detect

    def _recording_detect(event):
        seen_locked.append(engine._lock.locked())
        return real_detect(event)

    correlator.detect = _recording_detect
    engine.add(_event(10.0))
    assert seen_locked == [True], "detect() must be called while the lock is held"


def test_detect_called_once_per_add():
    """add() must score via exactly one detect() call: detect() mutates the
    per-metric baseline (mean/var/count), so calling it twice would corrupt
    that state (double-counting the sample) even though this event's own
    score would look identical on a second call."""
    calls = {"n": 0}

    class _Spy(RiverCorrelator):
        def detect(self, event):
            calls["n"] += 1
            return super().detect(event)

    eng = CorrelationEngine(_Spy(), window_seconds=30)
    eng.add(_event(value=95.0))
    assert calls["n"] == 1


def _error_rate_event(value, fp="err", ts_sec=0):
    return TelemetryEvent(
        source="prom",
        kind=TelemetryKind.METRIC,
        name="meridian_error_rate",
        value=value,
        labels={},
        ts=datetime(2026, 8, 13, 0, 0, ts_sec, tzinfo=UTC),
        fingerprint=fp,
    )


def test_enabled_policy_buffers_subz_ratio():
    """meridian_error_rate is cold (never seen before) so detect() returns a
    warm-up-suppressed z-score of 0 -- the OLD `score <= z_threshold` check
    would skip it. The enabled policy classifies it as a "ratio" metric and
    flags purely on the absolute value (0.05 > the 0.02 default threshold),
    independent of the z-score, so it gets buffered and flush() emits it."""
    engine = CorrelationEngine(
        RiverCorrelator(detection_policy=DetectionPolicy(enabled=True)),
        window_seconds=30,
    )
    assert engine.add(_error_rate_event(0.05)) is None  # buffered, no emit yet (lone event)
    sit = engine.flush()
    assert isinstance(sit, Situation)
    assert {e.fingerprint for e in sit.member_events} == {"err"}


def test_disabled_policy_still_skips_subz_event():
    """Confirms the off path is unchanged: a default (disabled-policy) engine
    keeps today's pure z-threshold behavior, so the same cold sub-z event that
    the enabled policy above buffers is skipped entirely here."""
    engine = CorrelationEngine(RiverCorrelator(), window_seconds=30)
    assert engine.add(_error_rate_event(0.05)) is None
    assert engine.flush() is None  # nothing was buffered


def test_reset_preserves_policy():
    eng = CorrelationEngine(RiverCorrelator(detection_policy=DetectionPolicy(enabled=True)))
    eng.reset()
    assert eng._correlator._policy._enabled is True
