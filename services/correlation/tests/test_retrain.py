from services.correlation.adapters.river_correlator import RiverCorrelator


def test_retrain_aggregates_reliability():
    c = RiverCorrelator()
    c.retrain(
        [
            {"signature": "a", "worked": True},
            {"signature": "a", "worked": True},
            {"signature": "a", "worked": False},
            {"signature": "b", "worked": False},
        ]
    )
    assert c.reliability("a") == 2 / 3
    assert c.reliability("b") == 0.0


def test_reliability_unseen_is_zero():
    assert RiverCorrelator().reliability("never") == 0.0


def test_should_suppress_at_threshold():
    c = RiverCorrelator()
    c.retrain([{"signature": "a", "worked": True}, {"signature": "a", "worked": True}])  # 1.0
    assert c.should_suppress("a", 0.8) is True
    assert c.should_suppress("a", 1.0) is True


def test_should_not_suppress_below_threshold():
    c = RiverCorrelator()
    c.retrain([{"signature": "a", "worked": True}, {"signature": "a", "worked": False}])  # 0.5
    assert c.should_suppress("a", 0.8) is False


def test_should_not_suppress_unseen():
    assert RiverCorrelator().should_suppress("never", 0.8) is False


def test_retrain_replaces_prior():
    # a fresh retrain recomputes from the given data (idempotent w.r.t. input set)
    c = RiverCorrelator()
    c.retrain([{"signature": "a", "worked": False}])
    assert c.reliability("a") == 0.0
    c.retrain([{"signature": "a", "worked": True}, {"signature": "a", "worked": True}])
    assert c.reliability("a") == 1.0


def test_one_success_is_not_a_track_record():
    # reliability 1/1 = 1.0 used to clear any threshold, so a single fixed
    # incident was enough to silence that signature.
    c = RiverCorrelator()
    c.retrain([{"signature": "s", "worked": True}])
    assert c.should_suppress("s", 0.8) is True  # historical default: min_samples=1
    assert c.should_suppress("s", 0.8, min_samples=3) is False
    c.retrain([{"signature": "s", "worked": True}] * 3)
    assert c.should_suppress("s", 0.8, min_samples=3) is True


def test_runtime_refresh_uses_only_real_outcomes():
    from datetime import UTC, datetime

    from common.contracts import RemediationResult, TrainingRecord
    from services.correlation.app import _refresh_reliability
    from services.correlation.engine import CorrelationEngine

    def rec(mode):
        return TrainingRecord(
            situation_id="sit-s",
            signature="s",
            playbook_id="restart-pod",
            result=RemediationResult.SUCCESS,
            worked=True,
            ts=datetime(2026, 9, 23, tzinfo=UTC),
            mode=mode,
        )

    class Store:
        records = [rec("dry_run")] * 5

        def read_all(self):
            return list(self.records)

    engine = CorrelationEngine(RiverCorrelator())
    store = Store()
    _refresh_reliability(engine, store)
    assert engine._correlator.reliability("s") == 0.0  # simulations prove nothing
    store.records = [rec("k8s")] * 3
    _refresh_reliability(engine, store)  # picked up without a restart
    assert engine._correlator.reliability("s") == 1.0

    class Broken:
        def read_all(self):
            raise RuntimeError("db down")

    _refresh_reliability(engine, Broken())  # never raises, keeps the map
    assert engine._correlator.reliability("s") == 1.0
