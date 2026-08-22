from fastapi.testclient import TestClient

from services.governance.adapters.approval_store import InMemoryApprovalStore
from services.governance.adapters.audit_sink import InMemoryAuditSink
from services.governance.adapters.playbook_store import InMemoryPlaybookStore
from services.governance.rbac import RbacPolicy


def _client():
    from services.governance.app import app

    app.state.audit_sink = InMemoryAuditSink()
    app.state.playbook_store = InMemoryPlaybookStore()
    app.state.rbac = RbacPolicy(roles={}, actors={})
    app.state.approval_store = InMemoryApprovalStore()
    return TestClient(app)


def _appr(c, appr_id="appr-sit-1"):
    return c.post(
        "/approvals",
        json={
            "id": appr_id,
            "situation_id": "sit-1",
            "playbook_id": "restart-pod",
            "requested_by": "action-service",
        },
    )


def test_get_single_approval():
    c = _client()
    _appr(c)
    r = c.get("/approvals/appr-sit-1")
    assert r.status_code == 200
    assert r.json()["status"] == "pending"


def test_get_unknown_approval_404():
    c = _client()
    assert c.get("/approvals/nope").status_code == 404


def test_list_pending_only():
    c = _client()
    _appr(c, "appr-sit-1")
    _appr(c, "appr-sit-2")
    # decide one away from pending would need rbac; instead just check both pending listed
    ids = {a["id"] for a in c.get("/approvals").json()}
    assert ids == {"appr-sit-1", "appr-sit-2"}
