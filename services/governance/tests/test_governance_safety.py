"""Governance controls that used to be missing or bypassable.

- POST /playbooks took no identity, so anyone could overwrite a seed playbook
  with hitl_mode=auto and skip the HITL gate and the AI-proposal review.
- A human's approve/reject was never written to the audit log.
- /decide accepted any status string and could re-decide a closed request.
- A request action-service stopped waiting for stayed "pending" forever.
- A graduated (auto) playbook could never be put back behind a human.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from common.contracts import HitlMode, Playbook, RemediationStep
from services.governance.adapters.approval_store import InMemoryApprovalStore
from services.governance.adapters.audit_sink import InMemoryAuditSink
from services.governance.adapters.playbook_store import InMemoryPlaybookStore
from services.governance.rbac import RbacPolicy


def _client():
    from services.governance.app import app

    app.state.audit_sink = InMemoryAuditSink()
    app.state.playbook_store = InMemoryPlaybookStore()
    app.state.rbac = RbacPolicy.from_file("policies/rbac_policy.yaml")
    app.state.approval_store = InMemoryApprovalStore()
    return TestClient(app), app


def _pb(mode=HitlMode.HITL) -> dict:
    return Playbook(
        id="restart-pod",
        name="Restart Pod",
        match_rule="x",
        steps=[RemediationStep(action="restart")],
        hitl_mode=mode,
    ).model_dump(mode="json")


def _approval(c, aid="a1"):
    c.post(
        "/approvals",
        json={
            "id": aid,
            "situation_id": "sit-1",
            "playbook_id": "restart-pod",
            "requested_by": "action-service",
        },
    )


# --- POST /playbooks -------------------------------------------------------


def test_register_requires_an_identity():
    c, _ = _client()
    assert c.post("/playbooks", json=_pb()).status_code == 422


def test_register_refused_for_an_approver_or_stranger():
    c, app = _client()
    for who in ("oncall-alice", "nobody"):
        r = c.post("/playbooks", params={"registered_by": who}, json=_pb())
        assert r.status_code == 403, who
    assert app.state.playbook_store.get("restart-pod") is None


def test_register_allowed_for_coe_admin_and_audited():
    c, app = _client()
    r = c.post("/playbooks", params={"registered_by": "feedback-service"}, json=_pb())
    assert r.status_code == 200
    recs = app.state.audit_sink.records("playbook:restart-pod")
    assert [(x.actor, x.action) for x in recs] == [("feedback-service", "register")]


def test_register_auto_needs_graduate_permission():
    c, app = _client()
    app.state.rbac = RbacPolicy(
        roles={"reg": [{"action": "register", "resource": "playbook:*"}]},
        actors={"registrar": ["reg"]},
    )
    r = c.post("/playbooks", params={"registered_by": "registrar"}, json=_pb(HitlMode.AUTO))
    assert r.status_code == 403
    r = c.post("/playbooks", params={"registered_by": "registrar"}, json=_pb(HitlMode.HITL))
    assert r.status_code == 200


# --- /approvals/{id}/decide --------------------------------------------------


def test_decide_writes_an_audit_record_naming_the_human():
    c, app = _client()
    _approval(c)
    r = c.post("/approvals/a1/decide", json={"decision": "approved", "decided_by": "oncall-alice"})
    assert r.status_code == 200
    recs = app.state.audit_sink.records("sit-1")
    assert [(x.actor, x.action, x.resource) for x in recs] == [
        ("oncall-alice", "approve", "playbook:restart-pod")
    ]


def test_decide_rejects_an_unknown_decision_string():
    c, app = _client()
    _approval(c)
    r = c.post("/approvals/a1/decide", json={"decision": "APPROVED", "decided_by": "oncall-alice"})
    assert r.status_code == 422
    assert app.state.approval_store.get("a1").status == "pending"


def test_decide_cannot_rewrite_a_closed_request():
    c, app = _client()
    _approval(c)
    c.post("/approvals/a1/decide", json={"decision": "approved", "decided_by": "oncall-alice"})
    r = c.post("/approvals/a1/decide", json={"decision": "rejected", "decided_by": "oncall-alice"})
    assert r.status_code == 409
    assert app.state.approval_store.get("a1").status == "approved"


# --- /approvals/{id}/expire --------------------------------------------------


def test_requester_can_expire_a_pending_request():
    c, app = _client()
    _approval(c)
    r = c.post("/approvals/a1/expire", json={"actor": "action-service"})
    assert r.status_code == 200 and r.json()["status"] == "expired"
    assert app.state.approval_store.list_pending() == []
    # an expired request can no longer be approved
    r = c.post("/approvals/a1/decide", json={"decision": "approved", "decided_by": "oncall-alice"})
    assert r.status_code == 409


def test_only_the_requester_can_expire():
    c, app = _client()
    _approval(c)
    assert c.post("/approvals/a1/expire", json={"actor": "oncall-alice"}).status_code == 403
    assert app.state.approval_store.get("a1").status == "pending"


def test_expiring_a_decided_request_leaves_it_alone():
    c, _app = _client()
    _approval(c)
    c.post("/approvals/a1/decide", json={"decision": "approved", "decided_by": "oncall-alice"})
    r = c.post("/approvals/a1/expire", json={"actor": "action-service"})
    assert r.status_code == 200 and r.json()["status"] == "approved"


# --- /playbooks/{id}/demote --------------------------------------------------


def test_demote_puts_an_auto_playbook_back_behind_a_human():
    c, app = _client()
    app.state.playbook_store.register(Playbook.model_validate(_pb(HitlMode.AUTO)))
    r = c.post("/playbooks/restart-pod/demote", json={"decided_by": "feedback-service"})
    assert r.status_code == 200 and r.json()["hitl_mode"] == "hitl"
    assert app.state.playbook_store.get("restart-pod").hitl_mode == HitlMode.HITL
    actions = [x.action for x in app.state.audit_sink.records("playbook:restart-pod")]
    assert actions == ["demote"]


def test_demote_requires_graduate_permission():
    c, app = _client()
    app.state.playbook_store.register(Playbook.model_validate(_pb(HitlMode.AUTO)))
    r = c.post("/playbooks/restart-pod/demote", json={"decided_by": "oncall-alice"})
    assert r.status_code == 403
    assert app.state.playbook_store.get("restart-pod").hitl_mode == HitlMode.AUTO
