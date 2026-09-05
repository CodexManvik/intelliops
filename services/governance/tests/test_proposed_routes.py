from datetime import UTC, datetime

from fastapi.testclient import TestClient

from common.contracts import HitlMode, Playbook, RemediationStep, Situation, SituationStatus
from services.governance.adapters.audit_sink import InMemoryAuditSink
from services.governance.adapters.playbook_store import InMemoryPlaybookStore
from services.governance.adapters.proposed_store import InMemoryProposedPlaybookStore
from services.governance.rbac import RbacPolicy

NOW = datetime(2026, 9, 4, tzinfo=UTC)


def _situation_json():
    return Situation(
        id="sit-1",
        status=SituationStatus.DIAGNOSED,
        severity="high",
        first_seen=NOW,
        last_seen=NOW,
        signature="sig-1",
    ).model_dump(mode="json")


class _StubAuthor:
    def __init__(self, result):  # result is (Playbook, rationale) or None
        self._result = result

    def draft(self, situation, hint=None):
        return self._result


def _client(author):
    from services.governance.app import app

    app.state.audit_sink = InMemoryAuditSink()
    app.state.playbook_store = InMemoryPlaybookStore()
    app.state.proposed_store = InMemoryProposedPlaybookStore()
    app.state.rbac = RbacPolicy(
        roles={
            "approver": [
                {"action": "approve", "resource": "playbook:*"},
                {"action": "reject", "resource": "playbook:*"},
            ]
        },
        actors={"oncall-alice": ["approver"], "random-bob": []},
    )
    app.state.runbook_author = author
    return TestClient(app)


def _draft_playbook(action="restart", hitl=HitlMode.AUTO):
    # note hitl=AUTO here to prove the route FORCES hitl to HITL
    return Playbook(
        id="ai-supplied-id",
        name="drafted",
        match_rule="*",
        steps=[RemediationStep(action=action)],
        hitl_mode=hitl,
        reversible=True,
    )


def test_propose_stores_proposal_not_registry():
    c = _client(_StubAuthor((_draft_playbook(), "because cpu")))
    resp = c.post(
        "/playbooks/proposed", json={"situation": _situation_json(), "requested_by": "oncall-alice"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "proposed"
    assert body["playbook"]["hitl_mode"] == "hitl"  # FORCED
    assert body["playbook"]["id"] != "ai-supplied-id"  # server-assigned
    assert body["source_situation_id"] == "sit-1"
    # not in the live registry yet
    assert c.get("/playbooks").json() == [] or all(
        p["id"] != body["playbook"]["id"] for p in c.get("/playbooks").json()
    )


def test_propose_none_author_returns_422_stores_nothing():
    c = _client(_StubAuthor(None))
    resp = c.post(
        "/playbooks/proposed", json={"situation": _situation_json(), "requested_by": "oncall-alice"}
    )
    assert resp.status_code == 422
    assert c.get("/playbooks/proposed").json() == []


def test_propose_forbidden_for_actor_without_permission():
    c = _client(_StubAuthor((_draft_playbook(), None)))
    resp = c.post(
        "/playbooks/proposed", json={"situation": _situation_json(), "requested_by": "random-bob"}
    )
    assert resp.status_code == 403


def test_approve_registers_into_live_registry():
    c = _client(_StubAuthor((_draft_playbook(), "r")))
    pid = c.post(
        "/playbooks/proposed", json={"situation": _situation_json(), "requested_by": "oncall-alice"}
    ).json()["id"]
    resp = c.post(f"/playbooks/proposed/{pid}/approve", json={"decided_by": "oncall-alice"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    # now it IS in the live registry
    live = c.get("/playbooks").json()
    assert len(live) == 1 and live[0]["hitl_mode"] == "hitl"


def test_reject_does_not_register():
    c = _client(_StubAuthor((_draft_playbook(), "r")))
    pid = c.post(
        "/playbooks/proposed", json={"situation": _situation_json(), "requested_by": "oncall-alice"}
    ).json()["id"]
    resp = c.post(f"/playbooks/proposed/{pid}/reject", json={"decided_by": "oncall-alice"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"
    assert c.get("/playbooks").json() == []


def test_approve_forbidden_and_unknown():
    c = _client(_StubAuthor((_draft_playbook(), "r")))
    pid = c.post(
        "/playbooks/proposed", json={"situation": _situation_json(), "requested_by": "oncall-alice"}
    ).json()["id"]
    assert (
        c.post(f"/playbooks/proposed/{pid}/approve", json={"decided_by": "random-bob"}).status_code
        == 403
    )
    assert (
        c.post("/playbooks/proposed/nope/approve", json={"decided_by": "oncall-alice"}).status_code
        == 404
    )
