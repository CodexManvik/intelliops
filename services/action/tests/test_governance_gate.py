import threading
from datetime import UTC, datetime

from common.contracts import ApprovalRequest, AuditRecord
from common.interfaces import GovernanceGate
from services.action.adapters.governance_gate import InProcessGovernanceGate
from services.governance.adapters.audit_sink import InMemoryAuditSink
from services.governance.rbac import RbacPolicy

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _gate(approvals=None, poll=0.01):
    rbac = RbacPolicy(
        roles={"operator": [{"action": "execute", "resource": "playbook:*"}]},
        actors={"action-service": ["operator"]},
    )
    return InProcessGovernanceGate(
        rbac,
        approvals if approvals is not None else {},
        InMemoryAuditSink(),
        poll_interval_seconds=poll,
    )


def test_satisfies_protocol():
    assert isinstance(_gate(), GovernanceGate)


def test_check_rbac_delegates():
    g = _gate()
    assert g.check_rbac("action-service", "execute", "playbook:restart-pod") is True
    assert g.check_rbac("action-service", "approve", "playbook:x") is False


def test_request_approval_stores_pending():
    approvals = {}
    g = _gate(approvals)
    req = ApprovalRequest(
        id="a1", situation_id="s1", playbook_id="restart-pod", requested_by="action-service"
    )
    out = g.request_approval(req)
    assert out.status == "pending"
    assert approvals["a1"].status == "pending"


def test_await_decision_returns_when_approved():
    approvals = {
        "a1": ApprovalRequest(
            id="a1", situation_id="s1", playbook_id="restart-pod", requested_by="action-service"
        )
    }
    g = _gate(approvals, poll=0.01)

    # a background thread approves after a moment
    def approve():
        approvals["a1"] = approvals["a1"].model_copy(
            update={"status": "approved", "decided_by": "oncall-alice"}
        )

    timer = threading.Timer(0.03, approve)
    timer.start()
    decided = g.await_decision("a1", timeout_seconds=2.0)
    timer.cancel()
    assert decided.status == "approved"


def test_await_decision_times_out_still_pending():
    approvals = {
        "a1": ApprovalRequest(
            id="a1", situation_id="s1", playbook_id="restart-pod", requested_by="action-service"
        )
    }
    g = _gate(approvals, poll=0.01)
    decided = g.await_decision("a1", timeout_seconds=0.05)
    assert decided.status == "pending"  # caller treats still-pending as timeout (fail closed)


def test_write_audit_persists():
    sink = InMemoryAuditSink()
    rbac = RbacPolicy(roles={}, actors={})
    g = InProcessGovernanceGate(rbac, {}, sink, poll_interval_seconds=0.01)
    g.write_audit(
        AuditRecord(
            actor="action-service",
            action="execute",
            resource="playbook:x",
            decision="allow",
            ts=NOW,
            correlation_id="s1",
        )
    )
    assert len(sink.records()) == 1
