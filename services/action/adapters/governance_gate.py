"""In-process GovernanceGate: action's synchronous seam to governance.

Constructed with references to the SAME RbacPolicy, approval store, and audit
sink governance uses — so it shares state rather than duplicating it (the
consolidation the Slice-2 review flagged). await_decision polls the shared
approval store with a timeout; a still-pending result on timeout lets the
caller fail closed (ADR-003). An HTTP gate is a deferred alternative."""

from __future__ import annotations

import time

from common.contracts import ApprovalRequest, AuditRecord


class InProcessGovernanceGate:
    def __init__(self, rbac, approvals: dict, audit_sink, poll_interval_seconds: float = 0.5) -> None:
        self._rbac = rbac
        self._approvals = approvals
        self._audit_sink = audit_sink
        self._poll = poll_interval_seconds

    def check_rbac(self, actor: str, action: str, resource: str) -> bool:
        return self._rbac.check(actor, action, resource)

    def request_approval(self, request: ApprovalRequest) -> ApprovalRequest:
        self._approvals[request.id] = request
        return request

    def await_decision(self, approval_id: str, timeout_seconds: float) -> ApprovalRequest:
        deadline = time.monotonic() + timeout_seconds
        while True:
            req = self._approvals[approval_id]
            if req.status != "pending":
                return req
            if time.monotonic() >= deadline:
                return req
            time.sleep(self._poll)

    def write_audit(self, record: AuditRecord) -> None:
        self._audit_sink.write(record)
