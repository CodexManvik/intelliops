import asyncio
from datetime import UTC, datetime

from common.contracts import TelemetryEvent, TelemetryKind
from services.correlation.adapters.baseline_store import InMemoryBaselineStore
from services.correlation.adapters.river_correlator import RiverCorrelator
from services.correlation.app import _reload_baseline
from services.correlation.engine import CorrelationEngine


def test_reload_baseline_loads_and_retrains():
    store = InMemoryBaselineStore()
    store.save(
        [{"metric_name": "cpu_usage", "n": 60.0, "mean": 52.0, "variance": 4.0, "count": 60}]
    )
    engine = CorrelationEngine(RiverCorrelator(z_threshold=3.0, warmup_samples=50))
    training = [{"signature": "sig-x", "worked": True}, {"signature": "sig-x", "worked": True}]

    _reload_baseline(engine, store, training)

    # baseline loaded → warmed for cpu_usage, so a wild value is an outlier immediately
    ev = TelemetryEvent(
        source="prom",
        kind=TelemetryKind.METRIC,
        name="cpu_usage",
        value=500.0,
        labels={},
        ts=datetime(2026, 8, 20, tzinfo=UTC),
        fingerprint="cpu_usage",
    )
    assert engine._correlator.is_anomaly(ev)
    # reliability recovered from training records
    assert engine._correlator.reliability("sig-x") == 1.0


class _RaisingTrainingStore:
    def read_all(self):
        raise RuntimeError("db down")


class _FakeStores:
    def __init__(self):
        self.training_store = _RaisingTrainingStore()
        self.baseline_store = None  # file-mode: baseline reload is a no-op


def test_lifespan_reload_tolerates_raising_training_store(monkeypatch):
    """A down training store at boot degrades to a cold start, never crashes."""
    from services.correlation import app as app_mod

    class _NoopThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

    monkeypatch.setattr(app_mod, "make_stores", lambda settings: _FakeStores())
    monkeypatch.setattr(app_mod.threading, "Thread", _NoopThread)

    class _App:
        class state:
            bus = object()

    async def _drive():
        # Entering the lifespan must not raise even though read_all() raises.
        async with app_mod.lifespan(_App()):
            assert _App.state.engine is not None
            # cold start: no training records reloaded → empty reliability map
            assert _App.state.engine._correlator.reliability("sig-x") == 0.0

    asyncio.run(_drive())
