"""Governance service: RBAC gate, audit log, playbook registry, approvals."""

from __future__ import annotations

from fastapi import HTTPException
from pydantic import BaseModel

from common.config import get_settings
from common.contracts import ApprovalRequest, AuditRecord, Playbook
from services.base import create_app
from services.governance.adapters.audit_sink import FileAuditSink
from services.governance.adapters.playbook_store import FilePlaybookStore
from services.governance.rbac import RbacPolicy

app = create_app("governance-service")


def _init_state() -> None:
    settings = get_settings()
    app.state.audit_sink = FileAuditSink(settings.audit_store_path)
    app.state.playbook_store = FilePlaybookStore(settings.playbook_store_path)
    app.state.rbac = RbacPolicy.from_file(settings.rbac_policy_path)
    app.state.approvals = {}


_init_state()


class RbacCheck(BaseModel):
    actor: str
    action: str
    resource: str


class Decision(BaseModel):
    decision: str
    decided_by: str


@app.post("/audit")
def write_audit(record: AuditRecord) -> dict[str, str]:
    app.state.audit_sink.write(record)
    return {"status": "ok"}


@app.get("/audit")
def query_audit(correlation_id: str | None = None) -> list[AuditRecord]:
    records = app.state.audit_sink.records()
    if correlation_id is not None:
        records = [r for r in records if r.correlation_id == correlation_id]
    return records


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
    app.state.approvals[request.id] = request
    return request


@app.post("/approvals/{approval_id}/decide")
def decide_approval(approval_id: str, decision: Decision) -> ApprovalRequest:
    req = app.state.approvals.get(approval_id)
    if req is None:
        raise HTTPException(status_code=404, detail="approval not found")
    updated = req.model_copy(update={"status": decision.decision, "decided_by": decision.decided_by})
    app.state.approvals[approval_id] = updated
    return updated
