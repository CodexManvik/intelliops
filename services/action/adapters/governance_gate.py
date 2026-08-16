"""In-process GovernanceGate: action's synchronous seam to governance.

Constructed with references to the SAME RbacPolicy, approval store, and audit
sink governance uses — so it shares state rather than duplicating it (the
consolidation the Slice-2 review flagged). await_decision polls the shared
approval store with a timeout; a still-pending result on timeout lets the
caller fail closed (ADR-003). An HTTP gate is a deferred alternative."""

from __future__ import annotations

import logging
import time

import httpx

from common.contracts import ApprovalRequest, AuditRecord

logger = logging.getLogger(__name__)


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


class HttpGovernanceGate:
    """The cross-process gate: action talks to governance over REST.

    Closes the compose gap where an in-process approvals dict cannot span
    containers. Same interface remediate.py already calls on the in-process gate.
    """

    def __init__(self, base_url: str, poll_interval_seconds: float = 0.5,
                 http_client: httpx.Client | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._poll = poll_interval_seconds
        self._client = http_client or httpx.Client(timeout=5.0)

    def check_rbac(self, actor: str, action: str, resource: str) -> bool:
        resp = self._client.post(f"{self._base}/rbac/check",
                                 json={"actor": actor, "action": action, "resource": resource})
        if resp.status_code != 200:
            return False
        return bool(resp.json().get("allowed", False))

    def request_approval(self, request: ApprovalRequest) -> ApprovalRequest:
        resp = self._client.post(f"{self._base}/approvals", json=request.model_dump())
        return ApprovalRequest.model_validate(resp.json())

    def await_decision(self, approval_id: str, timeout_seconds: float) -> ApprovalRequest:
        # Fail-closed contract: this loop must NEVER raise. It runs inside
        # action's consumer thread (GOVERNANCE_MODE=http), so an unguarded
        # exception here would kill remediation processing instead of just
        # degrading to "stay pending" (ADR-003/ADR-008). Any transient
        # network error, governance 5xx, or malformed body during a poll is
        # treated as "still pending" and polling continues until deadline.
        last_known = ApprovalRequest(
            id=approval_id, situation_id="", playbook_id="", requested_by="", status="pending",
        )
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                resp = self._client.get(f"{self._base}/approvals/{approval_id}")
                req = ApprovalRequest.model_validate(resp.json())
            except Exception as exc:  # noqa: BLE001 — fail closed: any poll error degrades to still-pending
                logger.debug("await_decision poll failed for %s: %s", approval_id, exc)
            else:
                last_known = req
                if req.status != "pending":
                    return req
            if time.monotonic() >= deadline:
                return last_known
            time.sleep(self._poll)

    def write_audit(self, record: AuditRecord) -> None:
        self._client.post(f"{self._base}/audit", json=record.model_dump(mode="json"))
