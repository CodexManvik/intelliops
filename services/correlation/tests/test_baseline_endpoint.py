from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from common.contracts import TelemetryEvent, TelemetryKind
from services.correlation.adapters.robust_correlator import RobustCorrelator
from services.correlation.app import app
from services.correlation.engine import CorrelationEngine


def test_baseline_endpoint_shape():
    with TestClient(app) as client:
        r = client.get("/baseline")
        assert r.status_code == 200
        body = r.json()
        assert "correlator_kind" in body
        assert isinstance(body["baselines"], list)


def test_baseline_endpoint_reports_robust_statistics():
    """Regression: under the robust correlator every row came back mean=None and
    std=0.0, because this endpoint only understood RiverCorrelator's snapshot.

    The live posture runs `robust`, so the console's "live z-score baselines"
    table -- a page whose whole claim is that nothing on it is staged -- showed a
    column of dashes beside a column of zeros for every metric.
    """
    engine = CorrelationEngine(RobustCorrelator(z_threshold=3.0, warmup_samples=5))
    ts = datetime(2026, 8, 13, 0, 0, 0, tzinfo=UTC)
    for i in range(40):
        engine.add(
            TelemetryEvent(
                source="prom",
                kind=TelemetryKind.METRIC,
                name="cpu_usage",
                value=18.0 + (i % 7) * 0.4,
                labels={},
                ts=ts + timedelta(seconds=i),
                fingerprint="fp",
            )
        )
    with TestClient(app) as client:
        client.app.state.engine = engine
        body = client.get("/baseline").json()
    assert body["statistic"] == "median/MAD"
    row = next(b for b in body["baselines"] if b["metric_name"] == "cpu_usage")
    assert row["mean"] is not None and row["mean"] > 0
    assert row["std"] is not None and row["std"] > 0, "a varying window must not report zero spread"
    assert row["count"] == 40


def test_baseline_endpoint_labels_an_empty_robust_table_correctly():
    """Right after a reset there are no rows to infer the shape from, and
    calling an empty robust table "mean/stddev" is the same mislabelling."""
    engine = CorrelationEngine(RobustCorrelator(z_threshold=3.0, warmup_samples=5))
    with TestClient(app) as client:
        client.app.state.engine = engine
        body = client.get("/baseline").json()
    assert body["baselines"] == []
    assert body["statistic"] == "median/MAD"
