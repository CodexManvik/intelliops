"""Slice-4 acceptance: the loop closes, in-process.

(a) Remediation outcomes → training store → correlation.retrain → a reliably
self-healing signature is suppressed while a failing one still emits.
(b) A playbook graduates hitl→auto through governance on a clean track record."""

import random
import threading
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from common.contracts import (
    HitlMode,
    Playbook,
    RemediationOutcome,
    RemediationResult,
    RemediationStep,
    TelemetryEvent,
    TelemetryKind,
)
from common.envelope import publish_model
from services.correlation.adapters.river_correlator import RiverCorrelator
from services.correlation.engine import CorrelationEngine
from services.feedback.adapters.training_store import InMemoryTrainingStore
from services.feedback.consumer import run_consumer
from services.governance.adapters.audit_sink import InMemoryAuditSink
from services.governance.adapters.playbook_store import InMemoryPlaybookStore
from services.governance.rbac import RbacPolicy

NOW = datetime(2026, 8, 13, tzinfo=UTC)


class InMemoryBus:
    def __init__(self):
        self.topics: dict[str, list[dict]] = {}

    def publish(self, topic, message):
        self.topics.setdefault(topic, []).append(message)

    def consume(self, topic, group):
        yield from list(self.topics.get(topic, []))


def _event(value, fp, ts_sec=0):
    return TelemetryEvent(
        source="prom",
        kind=TelemetryKind.METRIC,
        name="cpu",
        value=value,
        labels={},
        ts=datetime(2026, 8, 13, 0, 0, ts_sec, tzinfo=UTC),
        fingerprint=fp,
    )


def _prime_and_flush(engine, n=200, seed=42):
    rng = random.Random(seed)
    for i in range(n):
        engine.add(_event(round(rng.gauss(10.0, 1.0), 3), f"b{i}", 0))
    engine.flush()


def test_loop_closes_reliable_signature_suppressed():
    # 1. Form a Situation to discover its signature.
    correlator = RiverCorrelator(z_threshold=3.0)
    engine = CorrelationEngine(correlator, window_seconds=30, suppress_threshold=0.8)
    _prime_and_flush(engine)
    engine.add(_event(100.0, "a", 1))
    engine.add(_event(120.0, "b", 2))
    situation = engine.flush()

    # 2. feedback consumes SUCCESS outcomes for that situation → training store.
    bus = InMemoryBus()
    store = InMemoryTrainingStore()
    for _ in range(3):
        publish_model(
            bus,
            "remediation.outcomes",
            RemediationOutcome(
                situation_id=situation.id,
                playbook_id="restart-pod",
                result=RemediationResult.SUCCESS,
                health_after="healthy",
                ts=NOW,
            ),
        )
    run_consumer(
        bus, store, graduator=lambda pid: None, min_successes=3, stop_event=threading.Event()
    )

    # 3. correlation retrains from the store → learns the signature self-heals.
    training = [{"signature": r.signature, "worked": r.worked} for r in store.read_all()]
    correlator.retrain(training)

    # 4. The same spikes now form the same signature — suppressed (loop closed).
    engine.add(_event(100.0, "a", 3))
    engine.add(_event(120.0, "b", 4))
    assert engine.flush() is None  # reliably self-healing → suppressed

    # A different (unseen) signature would still emit — sanity via reliability.
    assert correlator.should_suppress("some-other-sig", 0.8) is False


def test_playbook_graduates_through_governance():
    from services.governance.app import app

    store = InMemoryPlaybookStore()
    store.register(
        Playbook(
            id="restart-pod",
            name="Restart",
            match_rule="x",
            steps=[RemediationStep(action="restart")],
            hitl_mode=HitlMode.HITL,
            reversible=True,
            rollback_steps=[],
        )
    )
    app.state.playbook_store = store
    app.state.audit_sink = InMemoryAuditSink()
    app.state.rbac = RbacPolicy(
        roles={"coe-admin": [{"action": "graduate", "resource": "playbook:*"}]},
        actors={"feedback-service": ["coe-admin"]},
    )
    app.state.approvals = {}
    client = TestClient(app)

    # feedback's graduator, wired to the real governance endpoint via TestClient.
    def graduator(pid: str) -> None:
        client.post(f"/playbooks/{pid}/graduate", json={"decided_by": "feedback-service"})

    bus = InMemoryBus()
    tstore = InMemoryTrainingStore()
    for _ in range(3):
        publish_model(
            bus,
            "remediation.outcomes",
            RemediationOutcome(
                situation_id="sit-x",
                playbook_id="restart-pod",
                result=RemediationResult.SUCCESS,
                health_after="healthy",
                ts=NOW,
            ),
        )
    run_consumer(bus, tstore, graduator, min_successes=3, stop_event=threading.Event())

    # The playbook is now auto, promoted through governance under RBAC + audit.
    assert store.get("restart-pod").hitl_mode == HitlMode.AUTO
    assert any(a.action == "graduate" for a in app.state.audit_sink.records())
