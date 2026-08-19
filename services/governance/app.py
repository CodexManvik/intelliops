"""Governance service: RBAC gate, audit log, playbook registry, approvals."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from pydantic import BaseModel

from common.config import get_settings
from common.contracts import ApprovalRequest, AuditRecord, HitlMode, Playbook
from services.base import create_app
from services.governance.adapters.audit_sink import FileAuditSink
from services.governance.adapters.playbook_store import FilePlaybookStore
from services.governance.rbac import RbacPolicy


def _governance_exempt(method: str, path: str) -> bool:
    """Exempt /health + internal service-to-service paths from auth.

    The governance service hosts endpoints called by *both* external clients
    (the React console) and internal services (action, feedback) over the
    compose network.  Internal-bus paths are never exposed outside compose,
    so gating them only locks IntelliOps out of itself when AUTH_MODE=token.

    Exempt (internal bus):
        POST /rbac/check        — action → governance (RBAC gate)
        POST /audit             — action → governance (audit write)
        POST /approvals         — action → governance (create approval)
        GET  /approvals/{id}    — action polls approval status
        POST /playbooks/{id}/graduate — feedback → governance

    Gated (external / frontend):
        GET  /audit             — frontend reads audit log
        GET  /playbooks         — frontend lists playbooks
        GET  /playbooks/{id}    — frontend reads a playbook
        GET  /approvals         — frontend lists pending approvals
        POST /approvals/{id}/decide — frontend decides an approval
    """
    if path == "/health":
        return True
    # POST-only internal endpoints (action → governance)
    if method == "POST" and path in {"/rbac/check", "/audit", "/approvals"}:
        return True
    # GET /approvals/{id} (action polls) — but NOT POST /approvals/{id}/decide
    if path.startswith("/approvals/") and not path.endswith("/decide"):
        return True
    # POST /playbooks/{id}/graduate (feedback → governance)
    return bool(method == "POST" and path.startswith("/playbooks/") and path.endswith("/graduate"))


app = create_app("governance-service", auth_exempt=_governance_exempt)


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


class Graduate(BaseModel):
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


@app.get("/approvals")
def list_approvals() -> list[ApprovalRequest]:
    return [a for a in app.state.approvals.values() if a.status == "pending"]


@app.get("/approvals/{approval_id}")
def get_approval(approval_id: str) -> ApprovalRequest:
    req = app.state.approvals.get(approval_id)
    if req is None:
        raise HTTPException(status_code=404, detail="approval not found")
    return req


@app.post("/approvals/{approval_id}/decide")
def decide_approval(approval_id: str, decision: Decision) -> ApprovalRequest:
    req = app.state.approvals.get(approval_id)
    if req is None:
        raise HTTPException(status_code=404, detail="approval not found")
    if not app.state.rbac.check(decision.decided_by, "approve", f"playbook:{req.playbook_id}"):
        raise HTTPException(status_code=403, detail="decider lacks approve permission")
    updated = req.model_copy(update={"status": decision.decision, "decided_by": decision.decided_by})
    app.state.approvals[approval_id] = updated
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
    app.state.audit_sink.write(AuditRecord(
        actor=body.decided_by, action="graduate", resource=f"playbook:{playbook_id}",
        decision="allow", ts=datetime.now(UTC), correlation_id=f"playbook:{playbook_id}",
    ))
    return updated
