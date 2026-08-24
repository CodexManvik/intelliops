"""TrainedCorrelator: IsolationForest model composed over the robust online path.

The trained correlator wraps a RobustCorrelator for the online (cold-start)
score and, once fitted, blends in an IsolationForest anomaly score. All scoring
lives in detect() — the engine only ever calls detect(). These tests pin the
corrections an adversarial review verified:
  (a) cold-start detect() == the composed robust score, byte-identical;
  (b) the reset factory type(c)(z_threshold=, warmup_samples=) works;
  (c) after enough events fit() produces a model and a planted outlier scores
      ABOVE a cluster point (the -score_samples sign convention);
  (d) serialize()->load_model() round-trips to identical scores;
  (e) feature-name drift on load refuses (stays cold);
  (f) snapshot()/load(rows) (the engine baseline path) delegate to the composed
      robust correlator and do NOT collide with load_model(blob).
"""

from datetime import UTC, datetime, timedelta

from common.contracts import TelemetryEvent, TelemetryKind
from services.correlation.adapters.robust_correlator import RobustCorrelator
from services.correlation.adapters.trained_correlator import (
    FEATURE_NAMES,
    TrainedCorrelator,
)


def _event(name="cpu", value=10.0, fp="fp", ts=None, kind=TelemetryKind.METRIC, labels=None):
    return TelemetryEvent(
        source="prom",
        kind=kind,
        name=name,
        value=value,
        labels=labels or {},
        ts=ts or datetime(2026, 8, 13, 0, 0, 0, tzinfo=UTC),
        fingerprint=fp,
    )


def test_cold_start_matches_composed_robust_score_byte_identical():
    """(a) With no fitted model, detect() must return the online robust score
    alone — byte-identical to feeding the same stream through a bare
    RobustCorrelator with the same params."""
    trained = TrainedCorrelator(z_threshold=3.0, warmup_samples=30)
    robust = RobustCorrelator(z_threshold=3.0, warmup_samples=30)

    ts0 = datetime(2026, 8, 13, 0, 0, 0, tzinfo=UTC)
    values = [10.0, 10.1, 9.9, 10.2, 9.8] * 8  # 40 stable samples
    for i, v in enumerate(values):
        ts = ts0 + timedelta(seconds=i)
        assert trained.detect(_event(value=v, ts=ts)) == robust.detect(_event(value=v, ts=ts))

    # a spike, still cold (no fit() called): trained == robust exactly
    spike_ts = ts0 + timedelta(seconds=100)
    assert trained.detect(_event(value=500.0, ts=spike_ts)) == robust.detect(
        _event(value=500.0, ts=spike_ts)
    )


def test_reset_factory_compat_extra_kwargs_default():
    """(b) engine.reset() calls type(c)(z_threshold=, warmup_samples=) with ONLY
    those two kwargs — every extra ctor arg must default."""
    c = TrainedCorrelator(
        z_threshold=3.0,
        warmup_samples=30,
        seasonal_buckets=12,
        window_size=64,
        min_fit_samples=100,
        contamination=0.05,
    )
    c2 = type(c)(z_threshold=3.0, warmup_samples=30)  # must not raise TypeError
    assert c2._z_threshold == 3.0
    assert c2._warmup_samples == 30


def test_fit_produces_model_and_outlier_scores_above_cluster_point():
    """(c) The sign-convention test. After enough events fit() must build a model,
    and a planted outlier must score ABOVE a normal cluster point — proving we
    negate score_samples (which returns HIGHER=more normal)."""
    c = TrainedCorrelator(z_threshold=3.0, warmup_samples=30, min_fit_samples=200)
    ts0 = datetime(2026, 8, 13, 0, 0, 0, tzinfo=UTC)
    # a tight cluster around 10 with mild jitter — 250 samples > min_fit_samples
    cluster = [10.0, 10.1, 9.9, 10.2, 9.8] * 50
    for i, v in enumerate(cluster):
        c.detect(_event(value=v, ts=ts0 + timedelta(seconds=i)))

    assert c.fit() is True  # enough samples -> a model exists

    # Guard the sign convention on the ISOLATED model score, not the max()-blend.
    # detect() returns max(online, model_score); for a gross outlier the online
    # robust score dominates, so asserting on detect() alone would pass even with
    # a flipped negation. Assert on the raw model instead: after negating
    # score_samples (HIGHER=more normal), a planted outlier's negated score MUST
    # exceed a cluster point's. A flipped sign fails this.
    normal_row = c.featurize(_event(value=10.0, ts=ts0 + timedelta(seconds=1000)), 0.0)
    outlier_row = c.featurize(_event(value=5000.0, ts=ts0 + timedelta(seconds=1001)), 0.0)
    normal_model = -float(c._model.score_samples([normal_row])[0])
    outlier_model = -float(c._model.score_samples([outlier_row])[0])
    assert outlier_model > normal_model  # negation is correct: outlier reads MORE anomalous

    # And the blended detect() still ranks the outlier above a normal point.
    normal_score = c.detect(_event(value=10.0, ts=ts0 + timedelta(seconds=1002)))
    outlier_score = c.detect(_event(value=5000.0, ts=ts0 + timedelta(seconds=1003)))
    assert outlier_score > normal_score


def test_serialize_load_model_round_trips_identical_scores():
    """(d) serialize() -> load_model() must reproduce identical model scores."""
    c = TrainedCorrelator(z_threshold=3.0, warmup_samples=30, min_fit_samples=200)
    ts0 = datetime(2026, 8, 13, 0, 0, 0, tzinfo=UTC)
    cluster = [10.0, 10.1, 9.9, 10.2, 9.8] * 50
    for i, v in enumerate(cluster):
        c.detect(_event(value=v, ts=ts0 + timedelta(seconds=i)))
    assert c.fit() is True

    blob = c.serialize()
    assert isinstance(blob, bytes) and blob

    # a fresh correlator, warmed identically so the online path is the same,
    # then load the model. detect() on the same probe must match exactly.
    c2 = TrainedCorrelator(z_threshold=3.0, warmup_samples=30, min_fit_samples=200)
    for i, v in enumerate(cluster):
        c2.detect(_event(value=v, ts=ts0 + timedelta(seconds=i)))
    assert c2.load_model(blob) is True

    probe_ts = ts0 + timedelta(seconds=2000)
    assert c.detect(_event(value=42.0, ts=probe_ts)) == c2.detect(_event(value=42.0, ts=probe_ts))


def test_serialize_cold_returns_none():
    """serialize() with no fitted model returns None (nothing to persist)."""
    c = TrainedCorrelator(z_threshold=3.0, warmup_samples=30)
    assert c.serialize() is None


def test_feature_name_drift_on_load_refuses_and_stays_cold():
    """(e) A blob whose persisted feature_names differ from FEATURE_NAMES must be
    refused — the correlator stays cold rather than scoring a mismatched model."""
    import io

    import joblib

    c = TrainedCorrelator(z_threshold=3.0, warmup_samples=30, min_fit_samples=200)
    ts0 = datetime(2026, 8, 13, 0, 0, 0, tzinfo=UTC)
    cluster = [10.0, 10.1, 9.9, 10.2, 9.8] * 50
    for i, v in enumerate(cluster):
        c.detect(_event(value=v, ts=ts0 + timedelta(seconds=i)))
    assert c.fit() is True
    good_blob = c.serialize()

    # hand-craft a drifted blob: same model, wrong feature_names
    payload = joblib.load(io.BytesIO(good_blob))
    payload["feature_names"] = list(FEATURE_NAMES[:-1])  # dropped a column -> drift
    buf = io.BytesIO()
    joblib.dump(payload, buf)
    drifted = buf.getvalue()

    fresh = TrainedCorrelator(z_threshold=3.0, warmup_samples=30, min_fit_samples=200)
    for i, v in enumerate(cluster):
        fresh.detect(_event(value=v, ts=ts0 + timedelta(seconds=i)))
    assert fresh.load_model(drifted) is False  # refused
    # stays cold: detect() == the composed robust score (no model applied)
    robust = RobustCorrelator(z_threshold=3.0, warmup_samples=30)
    for i, v in enumerate(cluster):
        robust.detect(_event(value=v, ts=ts0 + timedelta(seconds=i)))
    probe_ts = ts0 + timedelta(seconds=3000)
    assert fresh.detect(_event(value=99.0, ts=probe_ts)) == robust.detect(
        _event(value=99.0, ts=probe_ts)
    )


def test_snapshot_load_delegate_to_robust_and_do_not_collide_with_load_model():
    """(f) snapshot()/load(rows) are the engine BASELINE path — they must delegate
    to the composed robust correlator and round-trip its windows. load(rows) takes
    a list[dict], distinct from load_model(blob) which takes bytes."""
    c = TrainedCorrelator(z_threshold=3.0, warmup_samples=30)
    ts0 = datetime(2026, 8, 13, 0, 0, 0, tzinfo=UTC)
    values = [10.0, 10.1, 9.9, 10.2, 9.8] * 8
    for i, v in enumerate(values):
        c.detect(_event(value=v, ts=ts0 + timedelta(seconds=i)))

    snap = c.snapshot()
    assert snap  # non-empty list of dicts (robust window rows)
    for row in snap:
        assert set(row.keys()) == {"metric_name", "bucket", "n", "window"}

    # load(rows) rebuilds the robust windows and reproduces identical robust scores
    c2 = TrainedCorrelator(z_threshold=3.0, warmup_samples=30)
    c2.load(snap)
    probe_ts = ts0 + timedelta(seconds=100)
    assert c.detect(_event(value=500.0, ts=probe_ts)) == c2.detect(_event(value=500.0, ts=probe_ts))

    # load(rows) and load_model(blob) are distinct methods with distinct inputs
    assert c.load.__name__ == "load"
    assert c.load_model.__name__ == "load_model"


def test_retrain_endpoint_fits_and_persists_then_reload_restores():
    """The REAL fit trigger: POST /retrain fits the trained correlator, saves the
    blob via model_store, and a boot reload restores an identical model."""
    from fastapi.testclient import TestClient

    from services.correlation.adapters.model_store import InMemoryModelStore
    from services.correlation.app import _reload_model, app
    from services.correlation.engine import CorrelationEngine

    engine = CorrelationEngine(
        TrainedCorrelator(z_threshold=3.0, warmup_samples=30, min_fit_samples=200)
    )
    ts0 = datetime(2026, 8, 13, 0, 0, 0, tzinfo=UTC)
    cluster = [10.0, 10.1, 9.9, 10.2, 9.8] * 50
    for i, v in enumerate(cluster):
        engine.add(_event(value=v, ts=ts0 + timedelta(seconds=i)))

    store = InMemoryModelStore()
    app.state.engine = engine
    app.state.model_store = store
    c = TestClient(app)

    body = c.post("/retrain").json()
    assert body == {"fitted": True, "persisted": True}
    assert store.load_latest("trained")  # a blob was saved

    # boot reload onto a fresh correlator restores an identical model
    fresh = TrainedCorrelator(z_threshold=3.0, warmup_samples=30, min_fit_samples=200)
    for i, v in enumerate(cluster):
        fresh.detect(_event(value=v, ts=ts0 + timedelta(seconds=i)))
    fresh_engine = CorrelationEngine(fresh)
    _reload_model(fresh_engine, store)

    probe_ts = ts0 + timedelta(seconds=5000)
    trained_now = engine._correlator.detect(_event(value=77.0, ts=probe_ts))
    reloaded = fresh.detect(_event(value=77.0, ts=probe_ts))
    assert trained_now == reloaded

    del app.state.engine
    del app.state.model_store


def test_retrain_endpoint_noop_for_non_trained_correlator():
    """A river/robust correlator has no fit() — /retrain degrades gracefully."""
    from fastapi.testclient import TestClient

    from services.correlation.adapters.river_correlator import RiverCorrelator
    from services.correlation.app import app
    from services.correlation.engine import CorrelationEngine

    app.state.engine = CorrelationEngine(RiverCorrelator())
    app.state.model_store = None
    c = TestClient(app)
    assert c.post("/retrain").json() == {"fitted": False, "persisted": False}
    del app.state.engine
    del app.state.model_store
