from datetime import UTC, datetime

from fastapi.testclient import TestClient

from common.contracts import HitlMode, Playbook, RemediationStep
from services.governance.adapters.audit_sink import InMemoryAuditSink
from services.governance.adapters.playbook_store import InMemoryPlaybookStore
from services.governance.rbac import RbacPolicy

NOW = datetime(2026, 8, 13, tzinfo=UTC).isoformat()


def _client():
    from services.governance.app import app

    app.state.audit_sink = InMemoryAuditSink()
    app.state.playbook_store = InMemoryPlaybookStore()
    app.state.rbac = RbacPolicy(
        roles={
            "operator": [{"action": "diagnose", "resource": "situation:*"}],
            "approver": [{"action": "approve", "resource": "playbook:*"}],
        },
        actors={"rca-service": ["operator"], "oncall-alice": ["approver"]},
    )
    app.state.approvals = {}
    return TestClient(app)


def test_health_still_works():
    c = _client()
    assert c.get("/health").json() == {"service": "governance-service", "status": "ok"}


def test_audit_write_and_query():
    c = _client()
    rec = {
        "actor": "rca-service",
        "action": "diagnose",
        "resource": "situation:sit-1",
        "decision": "allow",
        "ts": NOW,
        "correlation_id": "sit-1",
    }
    assert c.post("/audit", json=rec).status_code == 200
    got = c.get("/audit", params={"correlation_id": "sit-1"}).json()
    assert len(got) == 1
    assert got[0]["actor"] == "rca-service"
    # a different correlation_id returns nothing
    assert c.get("/audit", params={"correlation_id": "other"}).json() == []


def test_playbook_register_and_list():
    c = _client()
    pb = Playbook(
        id="restart-pod",
        name="Restart Pod",
        match_rule="x",
        steps=[RemediationStep(action="restart")],
        hitl_mode=HitlMode.HITL,
    ).model_dump(mode="json")
    assert c.post("/playbooks", json=pb).status_code == 200
    assert c.get("/playbooks/restart-pod").json()["name"] == "Restart Pod"
    assert [p["id"] for p in c.get("/playbooks").json()] == ["restart-pod"]
    assert c.get("/playbooks/missing").status_code == 404


def test_rbac_check():
    c = _client()
    allow = c.post(
        "/rbac/check",
        json={"actor": "rca-service", "action": "diagnose", "resource": "situation:sit-1"},
    ).json()
    assert allow == {"allowed": True}
    deny = c.post(
        "/rbac/check", json={"actor": "rca-service", "action": "approve", "resource": "playbook:x"}
    ).json()
    assert deny == {"allowed": False}


def test_approval_create_and_decide():
    c = _client()
    created = c.post(
        "/approvals",
        json={
            "id": "a1",
            "situation_id": "sit-1",
            "playbook_id": "restart-pod",
            "requested_by": "action-service",
        },
    ).json()
    assert created["status"] == "pending"
    decided = c.post(
        "/approvals/a1/decide", json={"decision": "approved", "decided_by": "oncall-alice"}
    ).json()
    assert decided["status"] == "approved"
    assert decided["decided_by"] == "oncall-alice"
    assert (
        c.post(
            "/approvals/missing/decide", json={"decision": "approved", "decided_by": "x"}
        ).status_code
        == 404
    )
