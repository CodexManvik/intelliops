import threading
from datetime import UTC, datetime

from common.contracts import AuthorDecision, RemediationOutcome, RemediationResult
from services.governance.adapters.author_decision_store import InMemoryAuthorDecisionStore
from services.governance.consumer import run_consumer

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _decision(**kw):
    base = {
        "signature": "sig-x",
        "proposal_id": "prop-1",
        "playbook_id": "ai-sig-x-abc123",
        "actions": ["restart", "scale"],
        "cited_facts": ["restart worked 4/5 for sig-x"],
        "note": None,
        "disposition": "accepted",
        "outcome": "unknown",
        "decided_by": None,
        "ts": NOW,
    }
    base.update(kw)
    return AuthorDecision(**base)


def _raw_outcome(result, playbook_id="ai-sig-x-abc123", health_after="healthy"):
    o = RemediationOutcome(
        situation_id="sit-1",
        playbook_id=playbook_id,
        result=result,
        health_after=health_after,
        ts=NOW,
    )
    return {"data": o.model_dump_json()}


class ScriptedBus:
    """Fake bus mirroring services/feedback/tests/test_consumer.py's ScriptedBus:
    consume() yields raw envelope fields (decoded by iter_models), and the
    script naturally ends the generator — no stop_event needed to drain it."""

    def __init__(self, script):
        self._script = script
        self.published = []

    def publish(self, topic, message):
        self.published.append((topic, message))

    def consume(self, topic, group):
        yield from self._script


class RaisingOnceStore:
    """Wraps a real store; update_outcome raises once (for the first call) then
    delegates normally — proves one bad outcome doesn't kill the consumer
    thread and the next outcome still gets processed."""

    def __init__(self, inner):
        self._inner = inner
        self._raised = False

    def update_outcome(self, playbook_id, outcome, health_after):
        if not self._raised:
            self._raised = True
            raise RuntimeError("store blip")
        self._inner.update_outcome(playbook_id, outcome, health_after)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _run(bus, store):
    run_consumer(bus, store, threading.Event())


def test_consumer_marks_outcome_worked_on_success():
    ds = InMemoryAuthorDecisionStore()
    ds.record(_decision(playbook_id="ai-sig-x-1", signature="sig-x"))
    bus = ScriptedBus([_raw_outcome(RemediationResult.SUCCESS, playbook_id="ai-sig-x-1")])
    _run(bus, ds)
    assert ds.by_signature("sig-x")[0].outcome == "worked"


def test_consumer_marks_outcome_failed_on_non_success():
    ds = InMemoryAuthorDecisionStore()
    ds.record(_decision(playbook_id="ai-sig-y-1", signature="sig-y"))
    bus = ScriptedBus([_raw_outcome(RemediationResult.ROLLED_BACK, playbook_id="ai-sig-y-1")])
    _run(bus, ds)
    assert ds.by_signature("sig-y")[0].outcome == "failed"


def test_consumer_marks_outcome_failed_on_failure_result():
    ds = InMemoryAuthorDecisionStore()
    ds.record(_decision(playbook_id="ai-sig-z-1", signature="sig-z"))
    bus = ScriptedBus([_raw_outcome(RemediationResult.FAILURE, playbook_id="ai-sig-z-1")])
    _run(bus, ds)
    assert ds.by_signature("sig-z")[0].outcome == "failed"


def test_consumer_noop_for_unknown_playbook_id():
    ds = InMemoryAuthorDecisionStore()
    ds.record(_decision(playbook_id="ai-sig-x-1", signature="sig-x"))
    bus = ScriptedBus([_raw_outcome(RemediationResult.SUCCESS, playbook_id="unknown-playbook")])
    _run(bus, ds)  # must not raise
    assert ds.by_signature("sig-x")[0].outcome == "unknown"


def test_consumer_survives_a_raising_store_and_processes_next_outcome():
    ds = InMemoryAuthorDecisionStore()
    ds.record(_decision(playbook_id="ai-sig-a-1", signature="sig-a"))
    ds.record(_decision(playbook_id="ai-sig-b-1", signature="sig-b"))
    raising_store = RaisingOnceStore(ds)
    bus = ScriptedBus(
        [
            _raw_outcome(RemediationResult.SUCCESS, playbook_id="ai-sig-a-1"),  # raises, swallowed
            _raw_outcome(RemediationResult.SUCCESS, playbook_id="ai-sig-b-1"),  # processed fine
        ]
    )
    _run(bus, raising_store)  # must not raise / thread must not die
    assert ds.by_signature("sig-a")[0].outcome == "unknown"  # lost to the swallowed error
    assert ds.by_signature("sig-b")[0].outcome == "worked"  # next outcome still processed


def test_consumer_stops_on_stop_event():
    def infinite():
        while True:
            yield _raw_outcome(RemediationResult.SUCCESS, playbook_id="ai-sig-x-1")

    class InfBus(ScriptedBus):
        def consume(self, topic, group):
            return infinite()

    ds = InMemoryAuthorDecisionStore()
    ds.record(_decision(playbook_id="ai-sig-x-1", signature="sig-x"))
    stop = threading.Event()
    stop.set()
    run_consumer(InfBus([]), ds, stop)
    assert ds.by_signature("sig-x")[0].outcome == "unknown"  # loop broke before doing any work
