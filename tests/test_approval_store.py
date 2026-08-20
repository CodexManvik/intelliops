from common.contracts import ApprovalRequest
from services.governance.adapters.approval_store import InMemoryApprovalStore


def _appr(aid="a1", status="pending"):
    return ApprovalRequest(
        id=aid,
        situation_id="s1",
        playbook_id="restart-pod",
        requested_by="action-service",
        status=status,
    )


def test_inmem_create_get():
    s = InMemoryApprovalStore()
    s.create(_appr("a1"))
    got = s.get("a1")
    assert got is not None and got.id == "a1" and got.status == "pending"
    assert s.get("missing") is None


def test_inmem_decide_updates_status_and_decider():
    s = InMemoryApprovalStore()
    s.create(_appr("a1"))
    updated = s.decide("a1", status="approved", decided_by="alice")
    assert updated.status == "approved" and updated.decided_by == "alice"
    assert s.get("a1").status == "approved"
    assert s.decide("missing", "approved", "alice") is None


def test_inmem_list_pending_excludes_decided():
    s = InMemoryApprovalStore()
    s.create(_appr("a1"))
    s.create(_appr("a2"))
    s.decide("a1", status="approved", decided_by="alice")
    pending_ids = {a.id for a in s.list_pending()}
    assert pending_ids == {"a2"}
