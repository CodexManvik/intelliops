import threading
from datetime import UTC, datetime

from common.contracts import RemediationOutcome, RemediationResult
from services.feedback.adapters.training_store import InMemoryTrainingStore
from services.feedback.consumer import run_consumer

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _raw_outcome(result, playbook="restart-pod"):
    o = RemediationOutcome(situation_id="sit-abc", playbook_id=playbook, result=result,
                           health_after="healthy", ts=NOW)
    return {"data": o.model_dump_json()}


class ScriptedBus:
    def __init__(self, script):
        self._script = script
        self.published = []

    def publish(self, topic, message):
        self.published.append((topic, message))

    def consume(self, topic, group):
        yield from self._script


def _run(bus, store, graduator, min_successes=3):
    run_consumer(bus, store, graduator, min_successes, threading.Event())


def test_consumer_labels_and_stores():
    bus = ScriptedBus([_raw_outcome(RemediationResult.SUCCESS)])
    store = InMemoryTrainingStore()
    _run(bus, store, graduator=lambda pid: None)
    recs = store.read_all()
    assert len(recs) == 1
    assert recs[0].signature == "abc"
    assert recs[0].worked is True


def test_consumer_proposes_graduation_after_threshold():
    # three clean successes for restart-pod -> graduation proposed once
    bus = ScriptedBus([_raw_outcome(RemediationResult.SUCCESS) for _ in range(3)])
    store = InMemoryTrainingStore()
    graduated = []
    _run(bus, store, graduator=graduated.append, min_successes=3)
    assert graduated == ["restart-pod"]  # proposed exactly once


def test_consumer_no_graduation_below_threshold():
    bus = ScriptedBus([_raw_outcome(RemediationResult.SUCCESS) for _ in range(2)])
    store = InMemoryTrainingStore()
    graduated = []
    _run(bus, store, graduator=graduated.append, min_successes=3)
    assert graduated == []


def test_consumer_no_graduation_with_rollback():
    bus = ScriptedBus([_raw_outcome(RemediationResult.SUCCESS),
                       _raw_outcome(RemediationResult.SUCCESS),
                       _raw_outcome(RemediationResult.SUCCESS),
                       _raw_outcome(RemediationResult.ROLLED_BACK)])
    store = InMemoryTrainingStore()
    graduated = []
    _run(bus, store, graduator=graduated.append, min_successes=3)
    assert graduated == []  # a rollback disqualifies


def test_consumer_stops_on_stop_event():
    def infinite():
        while True:
            yield _raw_outcome(RemediationResult.SUCCESS)

    class InfBus(ScriptedBus):
        def consume(self, topic, group):
            return infinite()

    store = InMemoryTrainingStore()
    stop = threading.Event()
    stop.set()
    run_consumer(InfBus([]), store, lambda pid: None, 3, stop)
    assert store.read_all() == []
