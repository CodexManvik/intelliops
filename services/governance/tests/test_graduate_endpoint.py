from fastapi.testclient import TestClient

from common.contracts import HitlMode, Playbook, RemediationStep
from services.governance.adapters.audit_sink import InMemoryAuditSink
from services.governance.adapters.playbook_store import InMemoryPlaybookStore
from services.governance.rbac import RbacPolicy


def _client():
    from services.governance.app import app

    store = InMemoryPlaybookStore()
    store.register(Playbook(id="restart-pod", name="Restart", match_rule="x",
                            steps=[RemediationStep(action="restart")],
                            hitl_mode=HitlMode.HITL, reversible=True, rollback_steps=[]))
    app.state.playbook_store = store
    app.state.audit_sink = InMemoryAuditSink()
    app.state.rbac = RbacPolicy(
        roles={"coe-admin": [{"action": "graduate", "resource": "playbook:*"}]},
        actors={"feedback-service": ["coe-admin"], "random-bob": []},
    )
    app.state.approvals = {}
    return TestClient(app), app.state


def test_graduate_flips_hitl_to_auto():
    c, state = _client()
    resp = c.post("/playbooks/restart-pod/graduate", json={"decided_by": "feedback-service"})
    assert resp.status_code == 200
    assert resp.json()["hitl_mode"] == "auto"
    # persisted in the store
    assert state.playbook_store.get("restart-pod").hitl_mode == HitlMode.AUTO
    # audited
    assert any(a.action == "graduate" for a in state.audit_sink.records())


def test_graduate_unauthorized_forbidden():
    c, state = _client()
    resp = c.post("/playbooks/restart-pod/graduate", json={"decided_by": "random-bob"})
    assert resp.status_code == 403
    # unchanged — still hitl
    assert state.playbook_store.get("restart-pod").hitl_mode == HitlMode.HITL


def test_graduate_unknown_playbook_404():
    c, _ = _client()
    resp = c.post("/playbooks/missing/graduate", json={"decided_by": "feedback-service"})
    assert resp.status_code == 404
