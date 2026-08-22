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
