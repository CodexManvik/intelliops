"""The approval endpoints delegate to app.state.approval_store.

Behavioral contract (in-memory can't persist across processes): create shows the
approval as pending via list_pending, a decide flips it out of pending, and a
decide on a missing id 404s. Uses an RBAC actor authorized to approve, since
POST /approvals/{id}/decide gates on check(decided_by, "approve", "playbook:...").
"""

from fastapi.testclient import TestClient

from services.governance.adapters.approval_store import InMemoryApprovalStore
from services.governance.rbac import RbacPolicy


def _client():
    from services.governance.app import app

    app.state.rbac = RbacPolicy(
        roles={"approver": [{"action": "approve", "resource": "playbook:*"}]},
        actors={"oncall-alice": ["approver"]},
    )
    app.state.approval_store = InMemoryApprovalStore()
    return TestClient(app)


def test_approval_endpoints_use_store():
    client = _client()
    r = client.post(
        "/approvals",
        json={
            "id": "appr-1",
            "situation_id": "s1",
            "playbook_id": "restart-pod",
            "requested_by": "action-service",
        },
    )
    assert r.status_code == 200
    # create → list_pending shows it
    assert any(a["id"] == "appr-1" for a in client.get("/approvals").json())
    # decide → no longer pending
    d = client.post(
        "/approvals/appr-1/decide",
        json={"decision": "approved", "decided_by": "oncall-alice"},
    )
    assert d.status_code == 200 and d.json()["status"] == "approved"
    assert all(a["id"] != "appr-1" for a in client.get("/approvals").json())
    # decide on missing → 404
    missing = client.post(
        "/approvals/nope/decide",
        json={"decision": "approved", "decided_by": "oncall-alice"},
    )
    assert missing.status_code == 404
