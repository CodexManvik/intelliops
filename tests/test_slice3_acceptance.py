"""Slice-3 acceptance: HITL-gated remediation end-to-end, in-process.

Uses the real InProcessGovernanceGate over a shared approval store + RBAC policy
+ audit sink. A background thread posts the human approval (as a ChatOps/UI
would), then the gate's poll returns approved and remediation proceeds."""

import threading
from datetime import UTC, datetime

from common.contracts import (
    DiagnosedSituation,
    HitlMode,
    Playbook,
    RemediationOutcome,
    RemediationResult,
    RemediationStep,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
)
from common.envelope import decode_model, publish_model
from services.action.adapters.governance_gate import InProcessGovernanceGate
from services.action.adapters.health import FixedHealthChecker
from services.action.adapters.remediator import RecordingRemediator
from services.action.adapters.sandbox import NullSandbox
from services.action.consumer import run_consumer
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


def _diagnosed():
    sit = Situation(
        id="sit-web-1",
        status=SituationStatus.DIAGNOSED,
        member_events=[
            TelemetryEvent(
                source="prom",
                kind=TelemetryKind.METRIC,
                name="cpu_usage",
                value=99.0,
                labels={"service": "web"},
                ts=NOW,
                fingerprint="fp",
            )
        ],
        severity="high",
        first_seen=NOW,
        last_seen=NOW,
        signature="sig-web",
    )
    return DiagnosedSituation(situation=sit, hypotheses=[], suggested_runbook_id="restart-pod")


def _store():
    s = InMemoryPlaybookStore()
    s.register(
        Playbook(
            id="restart-pod",
            name="Restart Pod",
            match_rule="x",
            steps=[RemediationStep(action="restart")],
            hitl_mode=HitlMode.HITL,
            reversible=True,
            rollback_steps=[RemediationStep(action="restart")],
        )
    )
    return s


def _rbac():
    return RbacPolicy(
        roles={
            "operator": [{"action": "execute", "resource": "playbook:*"}],
            "approver": [{"action": "approve", "resource": "playbook:*"}],
        },
        actors={"action-service": ["operator"], "oncall-alice": ["approver"]},
    )


def _approve_when_pending(approvals, appr_id, audit, done):
    """Simulate a human/ChatOps approving as soon as the request appears."""
    for _ in range(500):
        req = approvals.get(appr_id)
        if req is not None and req.status == "pending":
            approvals[appr_id] = req.model_copy(
                update={"status": "approved", "decided_by": "oncall-alice"}
            )
            done.set()
            return
        threading.Event().wait(0.005)


def test_hitl_approved_healthy_success_end_to_end():
    bus = InMemoryBus()
    approvals: dict = {}
    audit = InMemoryAuditSink()
    remediator = RecordingRemediator()
    gate = InProcessGovernanceGate(_rbac(), approvals, audit, poll_interval_seconds=0.01)
    publish_model(bus, "situations.diagnosed", _diagnosed())

    appr_id = "appr-sit-web-1"
    done = threading.Event()
    approver = threading.Thread(
        target=_approve_when_pending, args=(approvals, appr_id, audit, done), daemon=True
    )
    approver.start()

    run_consumer(
        bus,
        _store(),
        gate,
        remediator,
        FixedHealthChecker(True),
        NullSandbox(),
        timeout_seconds=3.0,
        poll_interval_seconds=0.01,
        stop_event=threading.Event(),
    )
    approver.join(timeout=1.0)

    outcomes = bus.topics.get("remediation.outcomes", [])
    assert len(outcomes) == 1
    o = decode_model(outcomes[0], RemediationOutcome)
    assert o.result == RemediationResult.SUCCESS
    assert o.health_after == "healthy"
    assert remediator.executed_plan is not None
    assert remediator.rolled_back_plan is None  # healthy → no rollback
    assert any(a.action == "execute" and a.correlation_id == "sit-web-1" for a in audit.records())


def test_hitl_approved_unhealthy_rolls_back_end_to_end():
    bus = InMemoryBus()
    approvals: dict = {}
    audit = InMemoryAuditSink()
    remediator = RecordingRemediator()
    gate = InProcessGovernanceGate(_rbac(), approvals, audit, poll_interval_seconds=0.01)
    publish_model(bus, "situations.diagnosed", _diagnosed())

    done = threading.Event()
    approver = threading.Thread(
        target=_approve_when_pending, args=(approvals, "appr-sit-web-1", audit, done), daemon=True
    )
    approver.start()

    run_consumer(
        bus,
        _store(),
        gate,
        remediator,
        FixedHealthChecker(False),
        NullSandbox(),
        timeout_seconds=3.0,
        poll_interval_seconds=0.01,
        stop_event=threading.Event(),
    )
    approver.join(timeout=1.0)

    o = decode_model(bus.topics["remediation.outcomes"][0], RemediationOutcome)
    assert o.result == RemediationResult.ROLLED_BACK
    assert remediator.executed_plan is not None
    assert remediator.rolled_back_plan is not None  # rolled back
