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
