from fastapi.testclient import TestClient

from common.contracts import ApprovalRequest
from services.governance.adapters.approval_store import InMemoryApprovalStore
from services.governance.rbac import RbacPolicy


def _client():
    from services.governance.app import app

    app.state.rbac = RbacPolicy(
        roles={"approver": [{"action": "approve", "resource": "playbook:*"}]},
        actors={"oncall-alice": ["approver"], "random-bob": []},
    )
    app.state.approvals = {
        "a1": ApprovalRequest(
            id="a1", situation_id="s1", playbook_id="restart-pod", requested_by="action-service"
        ),
    }
    return TestClient(app)


def test_authorized_decider_approves():
    c = _client()
    resp = c.post(
        "/approvals/a1/decide", json={"decision": "approved", "decided_by": "oncall-alice"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    assert resp.json()["decided_by"] == "oncall-alice"


def test_unauthorized_decider_forbidden():
    c = _client()
    resp = c.post("/approvals/a1/decide", json={"decision": "approved", "decided_by": "random-bob"})
    assert resp.status_code == 403
    # the approval must remain pending — no state change on a forbidden decide
    assert (
        c.post(
            "/approvals/a1/decide", json={"decision": "approved", "decided_by": "oncall-alice"}
        ).json()["status"]
        == "approved"
    )


def test_decide_missing_approval_still_404():
    c = _client()
    resp = c.post(
        "/approvals/missing/decide", json={"decision": "approved", "decided_by": "oncall-alice"}
    )
    assert resp.status_code == 404
