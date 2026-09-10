"""Governance service: RBAC gate, audit log, playbook registry, approvals."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text

from common.config import get_settings
from common.contracts import (
    ApprovalRequest,
    AuditRecord,
    AuthorDecision,
    AuthorDecisionDisposition,
    HitlMode,
    Playbook,
    ProposedPlaybook,
    ProposedPlaybookStatus,
    Situation,
)
from common.stores import make_stores
from services.base import create_app, db_ready
from services.governance.adapters.author_tools import AuthorToolbox
from services.governance.adapters.proposed_store import InMemoryProposedPlaybookStore
from services.governance.adapters.runbook_author import (
    NullRunbookAuthor,
    RunbookAuthorAgent,
)
from services.governance.adapters.system_context import SystemContextProvider
from services.governance.adapters.trace_collector import TraceCollector
from services.governance.agent_run_hub import AgentRunHub
from services.governance.consumer import run_consumer
from services.governance.rbac import RbacPolicy

logger = logging.getLogger("intelliops.governance")


def _auth_exempt(method: str, path: str) -> bool:
    # /agent-runs/{run_id}/stream is reached by the browser EventSource API,
    # which cannot set the Authorization header; it authenticates via
    # ?token= inside the route instead (see _stream_authorized below,
    # mirroring services/read/app.py's _auth_exempt/_stream_authorized).
    return method == "GET" and path.startswith("/agent-runs/") and path.endswith("/stream")


app = create_app(
    "governance-service",
    auth_exempt=_auth_exempt,
    readiness=lambda: db_ready(getattr(app.state, "db_engine", None)),
)


def _make_runbook_author(settings, stores, system_context_provider):
    if settings.runbook_author_mode == "openai" and settings.llm_runbook_endpoint:
        toolbox_factory = lambda situation: AuthorToolbox(
            situation,
            system_context_provider,
            stores.training_store,
            stores.audit_sink,
            stores.author_decision_store,
        )
        return RunbookAuthorAgent(
            settings.llm_runbook_endpoint,
            settings.llm_runbook_model,
            api_key=settings.llm_runbook_api_key,
            toolbox_factory=toolbox_factory,
            timeout_seconds=settings.llm_runbook_timeout_seconds,
        )
    return NullRunbookAuthor()


def _init_state() -> None:
    settings = get_settings()
    stores = make_stores(settings)
    app.state.db_engine = stores.engine
    app.state.audit_sink = stores.audit_sink
    app.state.playbook_store = stores.playbook_store
    app.state.training_store = stores.training_store
    app.state.author_decision_store = stores.author_decision_store
    app.state.rbac = RbacPolicy.from_file(settings.rbac_policy_path)
    # Use the approval store make_stores built (Postgres when STORE_BACKEND=postgres).
    # Previously this hardcoded InMemoryApprovalStore(), so approvals never persisted
    # and were lost on every governance restart — the console's Approve then 404'd
    # ("approval not found") because the action-created approval had vanished.
    app.state.approval_store = stores.approval_store
    app.state.proposed_store = InMemoryProposedPlaybookStore()
    app.state.trace_store = stores.trace_store
    app.state.agent_run_hub = AgentRunHub()
    # run_id -> Thread for each in-flight/completed draft-async run, so tests
    # (and any other caller that needs determinism) can join a specific run's
    # thread rather than sleeping/polling. Best-effort bookkeeping only — it
    # is never read by the request/response path itself.
    app.state.draft_threads = {}
    system_context_provider = SystemContextProvider(settings.system_context_path)
    app.state.runbook_author = _make_runbook_author(settings, stores, system_context_provider)


_init_state()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # State is initialized at import time via _init_state(). The lifespan starts
    # the remediation.outcomes consumer (closes the AI author's learning loop —
    # see services/governance/consumer.py) and disposes the engine on shutdown,
    # matching feedback's lifespan.
    stop_event = threading.Event()
    thread = threading.Thread(
        target=run_consumer,
        args=(app.state.bus, app.state.author_decision_store, stop_event),
        daemon=True,
    )
    thread.start()
    app.state.consumer_stop = stop_event
    app.state.consumer_thread = thread
    # The draft-async worker threads publish trace steps via AgentRunHub from
    # OFF the event loop; bind_loop hands the hub the running loop so it can
    # marshal delivery back via call_soon_threadsafe (see agent_run_hub.py).
    app.state.agent_run_hub.bind_loop(asyncio.get_running_loop())
    try:
        yield
    finally:
        stop_event.set()
        engine = getattr(app.state, "db_engine", None)
        if engine is not None:
            engine.dispose()


app.router.lifespan_context = lifespan


class RbacCheck(BaseModel):
    actor: str
    action: str
    resource: str


class Decision(BaseModel):
    decision: str
    decided_by: str


class Graduate(BaseModel):
    decided_by: str


class ProposeRequest(BaseModel):
    situation: Situation
    hint: str | None = None
    requested_by: str


class ProposalDecision(BaseModel):
    decided_by: str


@app.post("/audit")
def write_audit(record: AuditRecord) -> dict[str, str]:
    app.state.audit_sink.write(record)
    return {"status": "ok"}


@app.get("/audit")
def query_audit(correlation_id: str | None = None) -> list[AuditRecord]:
    return app.state.audit_sink.records(correlation_id)


@app.post("/playbooks")
def register_playbook(playbook: Playbook) -> dict[str, str]:
    app.state.playbook_store.register(playbook)
    return {"status": "ok"}


@app.get("/playbooks")
def list_playbooks() -> list[Playbook]:
    return app.state.playbook_store.list()


# Registered before /playbooks/{playbook_id}: Starlette matches routes in
# registration order, so this literal path must come first or the
# {playbook_id} route below would swallow "proposed" as an id and 404.
@app.get("/playbooks/proposed")
def list_proposed(status: str | None = None) -> list[ProposedPlaybook]:
    st = ProposedPlaybookStatus(status) if status else None
    return app.state.proposed_store.list(status=st)


@app.get("/playbooks/{playbook_id}")
def get_playbook(playbook_id: str) -> Playbook:
    pb = app.state.playbook_store.get(playbook_id)
    if pb is None:
        raise HTTPException(status_code=404, detail="playbook not found")
    return pb


@app.post("/rbac/check")
def rbac_check(body: RbacCheck) -> dict[str, bool]:
    return {"allowed": app.state.rbac.check(body.actor, body.action, body.resource)}


@app.post("/approvals")
def create_approval(request: ApprovalRequest) -> ApprovalRequest:
    return app.state.approval_store.create(request)


@app.get("/approvals")
def list_approvals() -> list[ApprovalRequest]:
    return app.state.approval_store.list_pending()


@app.get("/approvals/{approval_id}")
def get_approval(approval_id: str) -> ApprovalRequest:
    req = app.state.approval_store.get(approval_id)
    if req is None:
        raise HTTPException(status_code=404, detail="approval not found")
    return req


@app.post("/approvals/{approval_id}/decide")
def decide_approval(approval_id: str, decision: Decision) -> ApprovalRequest:
    req = app.state.approval_store.get(approval_id)
    if req is None:
        raise HTTPException(status_code=404, detail="approval not found")
    if not app.state.rbac.check(decision.decided_by, "approve", f"playbook:{req.playbook_id}"):
        raise HTTPException(status_code=403, detail="decider lacks approve permission")
    updated = app.state.approval_store.decide(
        approval_id, status=decision.decision, decided_by=decision.decided_by
    )
    return updated


@app.post("/reset-approvals")
def reset_approvals() -> dict:
    db = getattr(app.state, "db_engine", None)
    if db is not None:
        with db.begin() as conn:
            conn.execute(text("DELETE FROM approvals"))
    store = getattr(app.state, "approval_store", None)
    if hasattr(store, "_by_id"):
        store._by_id.clear()
    return {"reset": True}


@app.post("/playbooks/{playbook_id}/graduate")
def graduate_playbook(playbook_id: str, body: Graduate) -> Playbook:
    pb = app.state.playbook_store.get(playbook_id)
    if pb is None:
        raise HTTPException(status_code=404, detail="playbook not found")
    if not app.state.rbac.check(body.decided_by, "graduate", f"playbook:{playbook_id}"):
        raise HTTPException(status_code=403, detail="actor lacks graduate permission")
    updated = pb.model_copy(update={"hitl_mode": HitlMode.AUTO})
    app.state.playbook_store.register(updated)
    app.state.audit_sink.write(
        AuditRecord(
            actor=body.decided_by,
            action="graduate",
            resource=f"playbook:{playbook_id}",
            decision="allow",
            ts=datetime.now(UTC),
            correlation_id=f"playbook:{playbook_id}",
        )
    )
    return updated


def _finalize_proposal(
    situation: Situation, drafted: tuple, requested_by: str
) -> ProposedPlaybook:
    """Turn a raw `runbook_author.draft(...)` result into a stored, audited
    ProposedPlaybook. The ONE place that does this — both the sync
    `POST /playbooks/proposed` endpoint and the async draft-and-trace worker
    thread (`POST /playbooks/draft-async`) call this after a successful draft,
    so their observable results (normalization, storage, audit, best-effort
    AuthorDecision) are identical.

    `drafted` is the non-None return of `RunbookAuthor.draft(...)`: a 3-tuple
    (playbook, rationale, cited_facts) — RunbookAuthorAgent's shape — or,
    for back-compat with older stubs, a 2-tuple (playbook, rationale).
    """
    if len(drafted) == 3:
        playbook, rationale, cited_facts = drafted
    else:
        playbook, rationale = drafted
        cited_facts = []
    # normalize: force HITL and a server-assigned id (the AI never sets these).
    normalized = playbook.model_copy(
        update={
            "hitl_mode": HitlMode.HITL,
            "id": f"ai-{situation.signature}-{uuid4().hex[:6]}",
        }
    )
    proposal = ProposedPlaybook(
        id=f"prop-{uuid4().hex[:8]}",
        playbook=normalized,
        proposed_by="runbook-author",
        rationale=rationale,
        source_situation_id=situation.id,
        ts=datetime.now(UTC),
    )
    app.state.proposed_store.add(proposal)
    app.state.audit_sink.write(
        AuditRecord(
            actor=requested_by,
            action="propose",
            resource=f"proposal:{proposal.id}",
            decision="allow",
            ts=datetime.now(UTC),
            correlation_id=situation.id,
        )
    )
    # Best-effort: the author's own memory of this drafting decision. A store
    # blip here must never fail the proposal itself (the operator already has
    # a valid proposal to review) — log and move on.
    try:
        app.state.author_decision_store.record(
            AuthorDecision(
                signature=situation.signature,
                proposal_id=proposal.id,
                playbook_id=normalized.id,
                actions=[s.action for s in normalized.steps],
                cited_facts=cited_facts,
                note=None,
                ts=datetime.now(UTC),
            )
        )
    except Exception:
        logger.warning("failed to record author decision for %s", proposal.id, exc_info=True)
    return proposal


@app.post("/playbooks/proposed")
def propose_playbook(body: ProposeRequest) -> ProposedPlaybook:
    if not app.state.rbac.check(body.requested_by, "approve", "playbook:*"):
        raise HTTPException(status_code=403, detail="requester lacks permission")
    drafted = app.state.runbook_author.draft(body.situation, body.hint)
    if drafted is None:
        raise HTTPException(status_code=422, detail="author could not produce a valid runbook")
    return _finalize_proposal(body.situation, drafted, body.requested_by)


def _run_draft_async(run_id: str, situation: Situation, hint: str | None, requested_by: str) -> None:
    """Runs in a daemon thread. Drives the author's tool-calling draft loop,
    streaming/storing its trace via `sink`, then finalizes exactly like the
    sync endpoint on success. Never raises — any author exception is caught
    and recorded as a "failed" terminal outcome so the thread always ends
    cleanly and `mark_ended` always fires.
    """

    def sink(step) -> None:
        # Each half is independently best-effort: a hub blip must never skip
        # the durable store write, and vice versa.
        try:
            app.state.agent_run_hub.publish(run_id, step)
        except Exception:
            logger.warning("agent_run_hub.publish failed for %s", run_id, exc_info=True)
        try:
            app.state.trace_store.append_step(step)
        except Exception:
            logger.warning("trace_store.append_step failed for %s", run_id, exc_info=True)

    collector = TraceCollector(run_id, sink)
    drafted = None
    status = "gave_up"
    try:
        drafted = app.state.runbook_author.draft(situation, hint, trace=collector)
    except Exception:
        logger.exception("runbook author raised during draft-async run %s", run_id)
        status = "failed"

    try:
        if drafted is not None:
            proposal = _finalize_proposal(situation, drafted, requested_by)
            collector.outcome("succeeded", proposal.id)
            try:
                app.state.trace_store.finish_run(run_id, "succeeded", proposal.id, datetime.now(UTC))
            except Exception:
                logger.warning("trace_store.finish_run failed for %s", run_id, exc_info=True)
        else:
            collector.outcome(status)
            try:
                app.state.trace_store.finish_run(run_id, status, None, datetime.now(UTC))
            except Exception:
                logger.warning("trace_store.finish_run failed for %s", run_id, exc_info=True)
    finally:
        try:
            app.state.agent_run_hub.mark_ended(run_id)
        except Exception:
            logger.warning("agent_run_hub.mark_ended failed for %s", run_id, exc_info=True)


@app.post("/playbooks/draft-async")
def draft_playbook_async(body: ProposeRequest) -> JSONResponse:
    if not app.state.rbac.check(body.requested_by, "approve", "playbook:*"):
        raise HTTPException(status_code=403, detail="requester lacks permission")
    run_id = f"run-{uuid4().hex[:8]}"
    try:
        app.state.trace_store.start_run(run_id, body.situation.signature, datetime.now(UTC))
    except Exception:
        logger.warning("trace_store.start_run failed for %s", run_id, exc_info=True)
    thread = threading.Thread(
        target=_run_draft_async,
        args=(run_id, body.situation, body.hint, body.requested_by),
        daemon=True,
    )
    app.state.draft_threads[run_id] = thread
    thread.start()
    return JSONResponse({"run_id": run_id}, status_code=202)


@app.get("/agent-runs")
def list_agent_runs() -> dict:
    try:
        runs = app.state.trace_store.recent_runs(50)
    except Exception:
        logger.warning("trace_store.recent_runs failed", exc_info=True)
        return {"runs": []}
    return {"runs": [r.model_dump(mode="json") for r in runs]}


@app.get("/agent-runs/{run_id}")
def get_agent_run(run_id: str) -> dict:
    try:
        steps = app.state.trace_store.steps(run_id)
    except Exception:
        logger.warning("trace_store.steps failed for %s", run_id, exc_info=True)
        steps = []
    if not steps:
        # steps() returns [] both for "run exists but has no steps yet" and
        # "run_id unknown". Disambiguate via recent_runs' header list — if the
        # run isn't there either, this run_id was never started (or its store
        # blew up on write): treat as not found. A best-effort recent_runs
        # failure here (already logged above/below) degrades safely to "not
        # found" rather than crashing this endpoint.
        try:
            known = {r.run_id for r in app.state.trace_store.recent_runs(1000)}
        except Exception:
            logger.warning("trace_store.recent_runs failed for %s lookup", run_id, exc_info=True)
            known = set()
        if run_id not in known:
            raise HTTPException(status_code=404, detail="run not found")
    return {"run_id": run_id, "steps": [s.model_dump(mode="json") for s in steps]}


def _stream_authorized(request: Request) -> bool:
    # Mirrors services/read/app.py's _stream_authorized. EventSource cannot
    # set the Authorization header, so this route authenticates via the
    # ?token= query param instead of the header-based auth middleware.
    settings = get_settings()
    if settings.auth_mode != "token":
        return True
    token = request.query_params.get("token", "")
    return bool(settings.auth_token) and hmac.compare_digest(token, settings.auth_token)


@app.get("/agent-runs/{run_id}/stream")
async def stream_agent_run(run_id: str, request: Request):
    if not _stream_authorized(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    hub = app.state.agent_run_hub
    store = app.state.trace_store

    async def gen():
        # SUBSCRIBE BEFORE READING STORED STEPS. This is the correctness
        # crux: if we read the store first and subscribe second, a step
        # published in the gap between those two calls would be lost — never
        # in the stored-steps snapshot we already read, and never delivered
        # because we weren't subscribed yet when it was published. Subscribing
        # first means the queue buffers every live step from this point
        # onward (including ones we're about to also see in the replay); the
        # seq de-dup below skips any live step whose seq we already replayed.
        q = hub.subscribe(run_id)
        try:
            yield ": connected\n\n"
            max_seq = -1
            # Best-effort replay: a trace_store outage degrades to "no stored
            # steps" rather than failing the whole stream (matches the
            # best-effort guards on the other /agent-runs* endpoints above).
            try:
                stored = store.steps(run_id)
            except Exception:
                logger.warning("trace_store.steps failed for %s stream", run_id, exc_info=True)
                stored = []
            for step in stored:
                yield f"data: {json.dumps(step.model_dump(mode='json'))}\n\n"
                max_seq = max(max_seq, step.seq)
            if hub.is_ended(run_id):
                # The run already finished before this client connected — the
                # stored steps above are the entire run. A live subscriber
                # would wait forever for an `outcome` step that was published
                # (and already captured in the store) before we subscribed,
                # so close now instead of hanging on the queue.
                return
            while True:
                try:
                    step = await asyncio.wait_for(q.get(), timeout=15.0)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if step.seq <= max_seq:
                    # Already delivered via the replay above (it was
                    # published in the subscribe/replay race window) — skip
                    # to avoid a duplicate step reaching the client.
                    continue
                max_seq = step.seq
                yield f"data: {json.dumps(step.model_dump(mode='json'))}\n\n"
                if step.kind == "outcome":
                    # Terminal step: the run is done, close the stream.
                    return
        finally:
            hub.unsubscribe(run_id, q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/playbooks/proposed/{proposal_id}")
def get_proposed(proposal_id: str) -> ProposedPlaybook:
    p = app.state.proposed_store.get(proposal_id)
    if p is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    return p


@app.post("/playbooks/proposed/{proposal_id}/approve")
def approve_proposed(proposal_id: str, body: ProposalDecision) -> ProposedPlaybook:
    p = app.state.proposed_store.get(proposal_id)
    if p is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    if not app.state.rbac.check(body.decided_by, "approve", f"playbook:{p.playbook.id}"):
        raise HTTPException(status_code=403, detail="decider lacks approve permission")
    updated = app.state.proposed_store.set_status(
        proposal_id, ProposedPlaybookStatus.APPROVED, body.decided_by
    )
    app.state.playbook_store.register(updated.playbook)  # enters the live registry
    app.state.audit_sink.write(
        AuditRecord(
            actor=body.decided_by,
            action="approve-proposal",
            resource=f"proposal:{proposal_id}",
            decision="allow",
            ts=datetime.now(UTC),
            correlation_id=proposal_id,
        )
    )
    try:
        app.state.author_decision_store.update_disposition(
            proposal_id, AuthorDecisionDisposition.ACCEPTED, body.decided_by
        )
    except Exception:
        logger.warning(
            "failed to update author decision disposition for %s", proposal_id, exc_info=True
        )
    return updated


@app.post("/playbooks/proposed/{proposal_id}/reject")
def reject_proposed(proposal_id: str, body: ProposalDecision) -> ProposedPlaybook:
    p = app.state.proposed_store.get(proposal_id)
    if p is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    if not app.state.rbac.check(body.decided_by, "reject", f"playbook:{p.playbook.id}"):
        raise HTTPException(status_code=403, detail="decider lacks reject permission")
    updated = app.state.proposed_store.set_status(
        proposal_id, ProposedPlaybookStatus.REJECTED, body.decided_by
    )
    app.state.audit_sink.write(
        AuditRecord(
            actor=body.decided_by,
            action="reject-proposal",
            resource=f"proposal:{proposal_id}",
            decision="allow",
            ts=datetime.now(UTC),
            correlation_id=proposal_id,
        )
    )
    try:
        app.state.author_decision_store.update_disposition(
            proposal_id, AuthorDecisionDisposition.REJECTED, body.decided_by
        )
    except Exception:
        logger.warning(
            "failed to update author decision disposition for %s", proposal_id, exc_info=True
        )
    return updated


@app.post("/reset-proposed")
def reset_proposed() -> dict:
    store = getattr(app.state, "proposed_store", None)
    if store is not None:
        store.clear()
    return {"reset": True}
