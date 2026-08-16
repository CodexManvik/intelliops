# services/correlation/tests/test_suppressed_signal.py
from datetime import UTC, datetime, timedelta

from common.contracts import TelemetryEvent, TelemetryKind
from services.correlation.adapters.river_correlator import RiverCorrelator
from services.correlation.engine import CorrelationEngine


def _ev(v, i, t0):
    return TelemetryEvent(source="p", kind=TelemetryKind.METRIC, name="cpu", value=v,
        labels={}, ts=t0+timedelta(seconds=5*i), fingerprint=f"f{i}")


def test_pop_suppressed_returns_suppressed_situation():
    # Force suppression: a correlator whose should_suppress is always True.
    class AlwaysSuppress(RiverCorrelator):
        def should_suppress(self, signature, threshold): return True
    c = AlwaysSuppress(z_threshold=0.0, warmup_samples=0)
    eng = CorrelationEngine(c, window_seconds=0.0)
    t0 = datetime(2026,8,16,tzinfo=UTC)
    # NOTE: values must vary (not a flat 90.0 for every event) — RiverCorrelator
    # .detect()'s sd==0 guard forces score=0.0 for a zero-variance signal, and
    # with z_threshold=0.0 that makes add() short-circuit before ever buffering
    # anything, so pop_suppressed() would have nothing to return.
    for i, v in enumerate([90.0, 91.0, 89.0]):
        eng.add(_ev(v, i, t0))
    eng.flush()  # buffer collapses; suppressed
    s = eng.pop_suppressed()
    assert s is not None
    assert eng.pop_suppressed() is None  # cleared after pop
