"""Governance service: RBAC gate, audit log, playbook registry, approvals."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from common.config import get_settings
from common.contracts import ApprovalRequest, AuditRecord, HitlMode, Playbook
from common.stores import make_stores
from services.base import create_app
from services.governance.adapters.approval_store import InMemoryApprovalStore
from services.governance.rbac import RbacPolicy

app = create_app("governance-service")  # default: only /health is exempt


def _init_state() -> None:
    settings = get_settings()
    stores = make_stores(settings)
    app.state.db_engine = stores.engine
    app.state.audit_sink = stores.audit_sink
    app.state.playbook_store = stores.playbook_store
    app.state.rbac = RbacPolicy.from_file(settings.rbac_policy_path)
    app.state.approval_store = InMemoryApprovalStore()


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
