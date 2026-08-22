from datetime import UTC, datetime, timedelta

from common.contracts import TelemetryEvent, TelemetryKind
from services.correlation.adapters.river_correlator import RiverCorrelator
from services.correlation.engine import CorrelationEngine


def _ev(v, i, t0):
    return TelemetryEvent(
        source="p",
        kind=TelemetryKind.METRIC,
        name="cpu",
        value=v,
        labels={},
        ts=t0 + timedelta(seconds=5 * i),
        fingerprint=f"f{i}",
    )


def test_reset_clears_buffer_and_baseline():
    eng = CorrelationEngine(
        RiverCorrelator(z_threshold=2.0, warmup_samples=2), window_seconds=100.0
    )
    t0 = datetime(2026, 8, 16, tzinfo=UTC)
    for i in range(6):
        eng.add(_ev(10.0 if i < 3 else 99.0, i, t0))  # baseline then spike → buffered anomaly
    eng.reset()
    # after reset, buffer empty → flush yields nothing
    assert eng.flush() is None


def test_reset_baseline_endpoint():
    from fastapi.testclient import TestClient

    from services.correlation.adapters.river_correlator import RiverCorrelator
    from services.correlation.app import app
    from services.correlation.engine import CorrelationEngine

    app.state.engine = CorrelationEngine(RiverCorrelator())
    c = TestClient(app)
    assert c.post("/reset-baseline").json() == {"reset": True}
