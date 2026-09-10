"""Tests for the async draft-and-trace flow: POST /playbooks/draft-async runs
the AI runbook author in a background thread (so the request returns a run_id
immediately) and records its progress into the TraceStore + AgentRunHub; the
GET /agent-runs* endpoints read that trace back. `_finalize_proposal` is the
shared post-draft helper both the sync and async paths call — see
test_proposed_routes.py for the sync-path-unchanged proof.

Determinism: the draft always runs in a daemon thread (even for the fake,
synchronous author below) so behavior matches production. Tests retrieve the
thread from `app.state.draft_threads[run_id]` and `.join()` it before
asserting on the trace/store side effects.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from common.contracts import (
    AuthorDecisionDisposition,
    HitlMode,
    Playbook,
    RemediationStep,
    Situation,
    SituationStatus,
    TraceStepKind,
)
from services.governance.adapters.audit_sink import InMemoryAuditSink
from services.governance.adapters.author_decision_store import InMemoryAuthorDecisionStore
from services.governance.adapters.playbook_store import InMemoryPlaybookStore
from services.governance.adapters.proposed_store import InMemoryProposedPlaybookStore
from services.governance.adapters.trace_store import InMemoryTraceStore
from services.governance.agent_run_hub import AgentRunHub
from services.governance.rbac import RbacPolicy

NOW = datetime(2026, 9, 10, tzinfo=UTC)

_JOIN_TIMEOUT = 5.0


def _situation_json():
    return Situation(
        id="sit-1",
        status=SituationStatus.DIAGNOSED,
        severity="high",
        first_seen=NOW,
        last_seen=NOW,
        signature="sig-1",
    ).model_dump(mode="json")


def _draft_playbook(action="restart", hitl=HitlMode.AUTO):
    return Playbook(
        id="ai-supplied-id",
        name="drafted",
        match_rule="*",
        steps=[RemediationStep(action=action)],
        hitl_mode=hitl,
        reversible=True,
    )


class _FakeAuthor:
    """Records a couple of trace steps via the collector (mirroring what
    RunbookAuthorAgent does internally), then returns a fixed result — so
    tests never hit a real LLM. `result` is a 3-tuple or None; `raises=True`
    makes draft() raise instead."""

    def __init__(self, result, raises=False):
        self._result = result
        self._raises = raises

    def draft(self, situation, hint=None, trace=None):
        if trace is not None:
            trace.model_turn("investigating the situation")
            trace.tool_call("get_situation", {"id": situation.id}, "ok")
        if self._raises:
            raise RuntimeError("author blew up")
        if self._result is not None and trace is not None:
            playbook, rationale, cited_facts = self._result
            trace.submit(
                {
                    "name": playbook.name,
                    "actions": [s.action for s in playbook.steps],
                    "rationale": rationale,
                    "cited_facts": cited_facts,
                }
            )
        return self._result


def _client(author, decision_store=None, trace_store=None, hub=None):
    from services.governance.app import app

    app.state.audit_sink = InMemoryAuditSink()
    app.state.playbook_store = InMemoryPlaybookStore()
    app.state.proposed_store = InMemoryProposedPlaybookStore()
    app.state.author_decision_store = decision_store or InMemoryAuthorDecisionStore()
    app.state.trace_store = trace_store or InMemoryTraceStore()
    app.state.agent_run_hub = hub or AgentRunHub()
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
    app.state.draft_threads = {}
    return TestClient(app)


def _post_draft(c, requested_by="oncall-alice"):
    return c.post(
        "/playbooks/draft-async",
        json={"situation": _situation_json(), "requested_by": requested_by},
    )


def _join_run(c, run_id):
    from services.governance.app import app

    thread = app.state.draft_threads.get(run_id)
    assert thread is not None, f"no thread recorded for {run_id}"
    thread.join(timeout=_JOIN_TIMEOUT)
    assert not thread.is_alive(), "draft thread did not finish in time"


def test_draft_async_returns_202_with_run_id():
    c = _client(_FakeAuthor((_draft_playbook(), "because cpu", ["fact-1"])))
    resp = _post_draft(c)
    assert resp.status_code == 202
    body = resp.json()
    assert body.get("run_id")
    _join_run(c, body["run_id"])


def test_draft_async_succeeds_records_trace_and_proposal():
    from services.governance.app import app

    c = _client(_FakeAuthor((_draft_playbook(), "because cpu", ["fact-1"])))
    resp = _post_draft(c)
    run_id = resp.json()["run_id"]
    _join_run(c, run_id)

    steps = app.state.trace_store.steps(run_id)
    assert len(steps) >= 1
    # steps are in seq order
    assert [s.seq for s in steps] == sorted(s.seq for s in steps)
    last = steps[-1]
    assert last.kind == TraceStepKind.OUTCOME
    assert last.detail["status"] == "succeeded"
    proposal_id = last.detail["proposal_id"]
    assert proposal_id

    # the proposal exists in proposed_store
    proposals = app.state.proposed_store.list()
    assert len(proposals) == 1
    assert proposals[0].id == proposal_id
    assert proposals[0].playbook.hitl_mode == HitlMode.HITL  # forced, same as sync path

    # an AuthorDecision was recorded (via _finalize_proposal)
    decisions = app.state.author_decision_store.by_signature("sig-1")
    assert len(decisions) == 1
    assert decisions[0].proposal_id == proposal_id
    assert decisions[0].disposition == AuthorDecisionDisposition.PENDING


def test_draft_async_none_author_gives_up_no_proposal():
    from services.governance.app import app

    c = _client(_FakeAuthor(None))
    resp = _post_draft(c)
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    _join_run(c, run_id)

    steps = app.state.trace_store.steps(run_id)
    assert len(steps) >= 1
    last = steps[-1]
    assert last.kind == TraceStepKind.OUTCOME
    assert last.detail["status"] == "gave_up"
    assert last.detail["proposal_id"] is None
    assert app.state.proposed_store.list() == []


def test_draft_async_null_author_accepts_trace_kwarg():
    """Regression: NullRunbookAuthor.draft must accept trace kwarg (passed by
    _run_draft_async). Previously it raised TypeError, causing the run to report
    "failed" instead of the correct "gave_up". This test verifies:
    1. NullRunbookAuthor.draft accepts trace=None without raising
    2. Async draft yields a clean "gave_up" outcome (not "failed")
    3. No proposal is created
    """
    from services.governance.adapters.runbook_author import NullRunbookAuthor
    from services.governance.app import app

    c = _client(NullRunbookAuthor())
    resp = _post_draft(c)
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    _join_run(c, run_id)

    steps = app.state.trace_store.steps(run_id)
    assert len(steps) >= 1
    last = steps[-1]
    assert last.kind == TraceStepKind.OUTCOME
    # The key assertion: status must be "gave_up", not "failed"
    assert last.detail["status"] == "gave_up"
    assert last.detail["proposal_id"] is None
    assert app.state.proposed_store.list() == []


def test_draft_async_author_raises_failed_no_crash():
    from services.governance.app import app

    c = _client(_FakeAuthor(None, raises=True))
    resp = _post_draft(c)
    assert resp.status_code == 202  # endpoint itself never fails
    run_id = resp.json()["run_id"]
    _join_run(c, run_id)

    steps = app.state.trace_store.steps(run_id)
    assert len(steps) >= 1
    last = steps[-1]
    assert last.kind == TraceStepKind.OUTCOME
    assert last.detail["status"] == "failed"
    assert last.detail["proposal_id"] is None
    assert app.state.proposed_store.list() == []


def test_draft_async_forbidden_for_actor_without_permission():
    c = _client(_FakeAuthor((_draft_playbook(), "r", [])))
    resp = _post_draft(c, requested_by="random-bob")
    assert resp.status_code == 403
    # no run should have been started
    from services.governance.app import app

    assert app.state.draft_threads == {}


def test_agent_runs_lists_recent_run():
    c = _client(_FakeAuthor((_draft_playbook(), "r", [])))
    run_id = _post_draft(c).json()["run_id"]
    _join_run(c, run_id)

    resp = c.get("/agent-runs")
    assert resp.status_code == 200
    runs = resp.json()["runs"]
    assert any(r["run_id"] == run_id for r in runs)
    match = next(r for r in runs if r["run_id"] == run_id)
    assert match["status"] == "succeeded"
    assert match["signature"] == "sig-1"
    assert match["step_count"] >= 1


def test_agent_run_detail_returns_steps_in_order():
    c = _client(_FakeAuthor((_draft_playbook(), "r", [])))
    run_id = _post_draft(c).json()["run_id"]
    _join_run(c, run_id)

    resp = c.get(f"/agent-runs/{run_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == run_id
    seqs = [s["seq"] for s in body["steps"]]
    assert seqs == sorted(seqs)
    assert seqs == list(range(len(seqs)))  # monotonic from 0


def test_agent_run_detail_unknown_returns_404():
    c = _client(_FakeAuthor((_draft_playbook(), "r", [])))
    resp = c.get("/agent-runs/unknown-run")
    assert resp.status_code == 404


def test_agent_runs_guards_store_exception():
    class _RaisingTraceStore:
        def recent_runs(self, limit=50):
            raise RuntimeError("store unavailable")

        def steps(self, run_id):
            raise RuntimeError("store unavailable")

        def start_run(self, run_id, signature, ts):
            raise RuntimeError("store unavailable")

        def append_step(self, step):
            raise RuntimeError("store unavailable")

        def finish_run(self, run_id, status, proposal_id, ts):
            raise RuntimeError("store unavailable")

    c = _client(_FakeAuthor((_draft_playbook(), "r", [])), trace_store=_RaisingTraceStore())
    resp = _post_draft(c)
    # the draft-async request must never fail even if the trace store is down
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    _join_run(c, run_id)

    assert c.get("/agent-runs").json() == {"runs": []}
    assert c.get(f"/agent-runs/{run_id}").status_code == 404
