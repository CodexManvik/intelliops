from datetime import UTC, datetime

from fastapi.testclient import TestClient

from common.contracts import (
    AuthorDecisionDisposition,
    HitlMode,
    Playbook,
    RemediationStep,
    Situation,
    SituationStatus,
)
from services.governance.adapters.audit_sink import InMemoryAuditSink
from services.governance.adapters.author_decision_store import InMemoryAuthorDecisionStore
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
    def __init__(self, result):  # result is (Playbook, rationale)[, cited_facts] or None
        self._result = result

    def draft(self, situation, hint=None):
        return self._result


class _RaisingAuthorDecisionStore:
    """A decision store whose record() always raises — proves the write is
    best-effort and never fails the proposal/approve/reject request."""

    def record(self, decision):
        raise RuntimeError("decision store unavailable")

    def by_signature(self, signature):
        return []

    def update_disposition(self, proposal_id, disposition, decided_by):
        raise RuntimeError("decision store unavailable")


def _client(author, decision_store=None):
    from services.governance.app import app

    app.state.audit_sink = InMemoryAuditSink()
    app.state.playbook_store = InMemoryPlaybookStore()
    app.state.proposed_store = InMemoryProposedPlaybookStore()
    app.state.author_decision_store = decision_store or InMemoryAuthorDecisionStore()
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


def test_propose_records_pending_author_decision():
    # A fixed 3-tuple (playbook, rationale, cited_facts), as RunbookAuthorAgent
    # returns — proves propose_playbook unpacks the agent's shape correctly
    # and records a pending AuthorDecision alongside the proposal.
    decision_store = InMemoryAuthorDecisionStore()
    c = _client(
        _StubAuthor((_draft_playbook(), "because cpu", ["fact-1"])),
        decision_store=decision_store,
    )
    resp = c.post(
        "/playbooks/proposed", json={"situation": _situation_json(), "requested_by": "oncall-alice"}
    )
    assert resp.status_code == 200
    body = resp.json()

    decisions = decision_store.by_signature("sig-1")
    assert len(decisions) == 1
    d = decisions[0]
    assert d.disposition == AuthorDecisionDisposition.PENDING
    assert d.playbook_id == body["playbook"]["id"]  # the server-assigned ai-<sig>-<uuid> id
    assert d.actions == [s["action"] for s in body["playbook"]["steps"]]
    assert d.cited_facts == ["fact-1"]


def test_approve_marks_author_decision_accepted():
    decision_store = InMemoryAuthorDecisionStore()
    c = _client(
        _StubAuthor((_draft_playbook(), "r", ["fact-1"])),
        decision_store=decision_store,
    )
    pid = c.post(
        "/playbooks/proposed", json={"situation": _situation_json(), "requested_by": "oncall-alice"}
    ).json()["id"]
    resp = c.post(f"/playbooks/proposed/{pid}/approve", json={"decided_by": "oncall-alice"})
    assert resp.status_code == 200

    decisions = decision_store.by_signature("sig-1")
    assert len(decisions) == 1
    assert decisions[0].proposal_id == pid
    assert decisions[0].disposition == AuthorDecisionDisposition.ACCEPTED
    assert decisions[0].decided_by == "oncall-alice"


def test_reject_marks_author_decision_rejected():
    decision_store = InMemoryAuthorDecisionStore()
    c = _client(
        _StubAuthor((_draft_playbook(), "r", ["fact-1"])),
        decision_store=decision_store,
    )
    pid = c.post(
        "/playbooks/proposed", json={"situation": _situation_json(), "requested_by": "oncall-alice"}
    ).json()["id"]
    resp = c.post(f"/playbooks/proposed/{pid}/reject", json={"decided_by": "oncall-alice"})
    assert resp.status_code == 200

    decisions = decision_store.by_signature("sig-1")
    assert len(decisions) == 1
    assert decisions[0].proposal_id == pid
    assert decisions[0].disposition == AuthorDecisionDisposition.REJECTED
    assert decisions[0].decided_by == "oncall-alice"


def test_propose_succeeds_when_decision_store_raises():
    # Best-effort: a decision-store failure on record() must never fail the
    # proposal itself — the proposal is still created and returned.
    c = _client(
        _StubAuthor((_draft_playbook(), "r", ["fact-1"])),
        decision_store=_RaisingAuthorDecisionStore(),
    )
    resp = c.post(
        "/playbooks/proposed", json={"situation": _situation_json(), "requested_by": "oncall-alice"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "proposed"
    # the proposal is retrievable afterward too
    assert c.get(f"/playbooks/proposed/{body['id']}").status_code == 200


def test_approve_succeeds_when_decision_store_raises():
    # Best-effort: a decision-store failure on update_disposition() must
    # never fail the approve request.
    c = _client(_StubAuthor((_draft_playbook(), "r", ["fact-1"])))
    pid = c.post(
        "/playbooks/proposed", json={"situation": _situation_json(), "requested_by": "oncall-alice"}
    ).json()["id"]
    c.app.state.author_decision_store = _RaisingAuthorDecisionStore()  # swap after propose
    resp = c.post(f"/playbooks/proposed/{pid}/approve", json={"decided_by": "oncall-alice"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


def test_finalize_proposal_called_directly_matches_sync_endpoint_shape():
    # _finalize_proposal is the factored helper both the sync
    # POST /playbooks/proposed endpoint AND the async draft-and-trace worker
    # (POST /playbooks/draft-async, task 5) call after a successful draft.
    # Calling it directly (bypassing the HTTP/RBAC layer entirely) proves the
    # helper itself — not just the route wrapping it — normalizes the
    # playbook, stores the proposal, writes the audit record, and records the
    # pending AuthorDecision.
    from services.governance.app import _finalize_proposal

    decision_store = InMemoryAuthorDecisionStore()
    c = _client(_StubAuthor((_draft_playbook(), "r", ["fact-1"])), decision_store=decision_store)
    situation = Situation.model_validate(_situation_json())

    proposal = _finalize_proposal(
        situation,
        (_draft_playbook(), "because cpu", ["fact-1"]),
        "oncall-alice",
    )

    assert proposal.playbook.hitl_mode == HitlMode.HITL  # forced
    assert proposal.playbook.id != "ai-supplied-id"  # server-assigned
    assert proposal.source_situation_id == "sit-1"
    assert c.app.state.proposed_store.get(proposal.id) is proposal

    decisions = decision_store.by_signature("sig-1")
    assert len(decisions) == 1
    assert decisions[0].proposal_id == proposal.id
    assert decisions[0].playbook_id == proposal.playbook.id
    assert decisions[0].disposition == AuthorDecisionDisposition.PENDING
    assert decisions[0].cited_facts == ["fact-1"]
