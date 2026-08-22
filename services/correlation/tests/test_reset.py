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


def test_reset_baseline_deletes_db_rows_in_postgres_mode():
    from fastapi.testclient import TestClient

    from services.correlation.app import app

    executed = []

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, stmt):
            executed.append(str(stmt))

    class _Engine:
        def begin(self):
            return _Conn()

    app.state.db_engine = _Engine()
    # app.state.engine may be unset in this unit context; the endpoint guards it.
    c = TestClient(app)
    r = c.post("/reset-baseline")
    assert r.status_code == 200 and r.json() == {"reset": True}
    assert any("correlation_baseline" in s.lower() for s in executed)
    del app.state.db_engine
