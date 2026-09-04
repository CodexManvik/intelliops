"""Governance service: RBAC gate, audit log, playbook registry, approvals."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from common.config import get_settings
from common.contracts import (
    ApprovalRequest,
    AuditRecord,
    HitlMode,
    Playbook,
    ProposedPlaybook,
    ProposedPlaybookStatus,
    Situation,
)
from common.stores import make_stores
from services.base import create_app, db_ready
from services.governance.adapters.approval_store import InMemoryApprovalStore
from services.governance.adapters.proposed_store import InMemoryProposedPlaybookStore
from services.governance.adapters.runbook_author import (
    NullRunbookAuthor,
    OpenAICompatibleRunbookAuthor,
)
from services.governance.rbac import RbacPolicy

app = create_app(
    "governance-service",
    readiness=lambda: db_ready(getattr(app.state, "db_engine", None)),
)  # default: only /health is exempt


def _make_runbook_author(settings):
    if settings.runbook_author_mode == "openai" and settings.llm_runbook_endpoint:
        return OpenAICompatibleRunbookAuthor(
            settings.llm_runbook_endpoint,
            settings.llm_runbook_model,
            api_key=settings.llm_runbook_api_key,
            timeout_seconds=settings.llm_runbook_timeout_seconds,
        )
    return NullRunbookAuthor()


def _init_state() -> None:
    settings = get_settings()
    stores = make_stores(settings)
    app.state.db_engine = stores.engine
    app.state.audit_sink = stores.audit_sink
    app.state.playbook_store = stores.playbook_store
    app.state.rbac = RbacPolicy.from_file(settings.rbac_policy_path)
    app.state.approval_store = InMemoryApprovalStore()
    app.state.proposed_store = InMemoryProposedPlaybookStore()
    app.state.runbook_author = _make_runbook_author(settings)


_init_state()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # State is initialized at import time via _init_state(); the lifespan exists
    # only to dispose the engine on shutdown, matching rca/action/feedback.
    try:
        yield
    finally:
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


@app.post("/playbooks/proposed")
def propose_playbook(body: ProposeRequest) -> ProposedPlaybook:
    if not app.state.rbac.check(body.requested_by, "approve", "playbook:*"):
        raise HTTPException(status_code=403, detail="requester lacks permission")
    drafted = app.state.runbook_author.draft(body.situation, body.hint)
    if drafted is None:
        raise HTTPException(status_code=422, detail="author could not produce a valid runbook")
    playbook, rationale = drafted
    # normalize: force HITL and a server-assigned id (the AI never sets these).
    normalized = playbook.model_copy(
        update={
            "hitl_mode": HitlMode.HITL,
            "id": f"ai-{body.situation.signature}-{uuid4().hex[:6]}",
        }
    )
    proposal = ProposedPlaybook(
        id=f"prop-{uuid4().hex[:8]}",
        playbook=normalized,
        proposed_by="runbook-author",
        rationale=rationale,
        source_situation_id=body.situation.id,
        ts=datetime.now(UTC),
    )
    app.state.proposed_store.add(proposal)
    app.state.audit_sink.write(
        AuditRecord(
            actor=body.requested_by,
            action="propose",
            resource=f"proposal:{proposal.id}",
            decision="allow",
            ts=datetime.now(UTC),
            correlation_id=body.situation.id,
        )
    )
    return proposal


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
    return updated


@app.post("/reset-proposed")
def reset_proposed() -> dict:
    store = getattr(app.state, "proposed_store", None)
    if store is not None:
        store.clear()
    return {"reset": True}
