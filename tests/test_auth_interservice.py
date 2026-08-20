"""Verify AUTH_MODE=token protects ALL governance endpoints, including internal.

With the authenticated-internal-calls design, every governance endpoint
(except /health) is gated by AUTH_MODE=token.  Internal callers
(action, feedback) send the shared token in their requests.  These tests
verify the two properties we care about:

1. Without a token, every protected endpoint returns 401.
2. With the correct token, every endpoint succeeds (200 or domain error).
"""

from fastapi.testclient import TestClient

from common.config import get_settings
from services.governance.app import app


def _client(monkeypatch) -> TestClient:
    """A governance TestClient running in AUTH_MODE=token."""
    monkeypatch.setenv("INTELLIOPS_AUTH_MODE", "token")
    monkeypatch.setenv("INTELLIOPS_AUTH_TOKEN", "test-secret")
    get_settings.cache_clear()
    return TestClient(app)


def _cleanup():
    get_settings.cache_clear()


_AUTH = {"Authorization": "Bearer test-secret"}


# --- Internal-bus endpoints: gated (token required) ---


def test_internal_rbac_check_requires_token(monkeypatch):
    """POST /rbac/check returns 401 without a token."""
    client = _client(monkeypatch)
    r = client.post("/rbac/check", json={"actor": "a", "action": "execute", "resource": "r"})
    assert r.status_code == 401
    _cleanup()


def test_internal_rbac_check_passes_with_token(monkeypatch):
    """POST /rbac/check succeeds with a valid token."""
    client = _client(monkeypatch)
    r = client.post(
        "/rbac/check", json={"actor": "a", "action": "execute", "resource": "r"}, headers=_AUTH
    )
    assert r.status_code == 200
    _cleanup()


def test_internal_audit_write_requires_token(monkeypatch):
    """POST /audit returns 401 without a token."""
    client = _client(monkeypatch)
    r = client.post(
        "/audit",
        json={
            "actor": "action-service",
            "action": "execute",
            "resource": "playbook:restart-pod",
            "decision": "allow",
            "ts": "2026-08-13T00:00:00Z",
            "correlation_id": "s1",
        },
    )
    assert r.status_code == 401
    _cleanup()


def test_internal_audit_write_passes_with_token(monkeypatch):
    """POST /audit succeeds with a valid token."""
    client = _client(monkeypatch)
    r = client.post(
        "/audit",
        json={
            "actor": "action-service",
            "action": "execute",
            "resource": "playbook:restart-pod",
            "decision": "allow",
            "ts": "2026-08-13T00:00:00Z",
            "correlation_id": "s1",
        },
        headers=_AUTH,
    )
    assert r.status_code == 200
    _cleanup()


def test_internal_approval_create_requires_token(monkeypatch):
    """POST /approvals returns 401 without a token."""
    client = _client(monkeypatch)
    r = client.post(
        "/approvals",
        json={
            "id": "appr-test",
            "situation_id": "s1",
            "playbook_id": "restart-pod",
            "requested_by": "action-service",
        },
    )
    assert r.status_code == 401
    _cleanup()


def test_internal_approval_create_passes_with_token(monkeypatch):
    """POST /approvals succeeds with a valid token."""
    client = _client(monkeypatch)
    r = client.post(
        "/approvals",
        json={
            "id": "appr-test2",
            "situation_id": "s1",
            "playbook_id": "restart-pod",
            "requested_by": "action-service",
        },
        headers=_AUTH,
    )
    assert r.status_code == 200
    _cleanup()


def test_internal_approval_poll_requires_token(monkeypatch):
    """GET /approvals/{id} returns 401 without a token."""
    client = _client(monkeypatch)
    # Create via token first
    client.post(
        "/approvals",
        json={
            "id": "appr-poll",
            "situation_id": "s1",
            "playbook_id": "restart-pod",
            "requested_by": "action-service",
        },
        headers=_AUTH,
    )
    # Poll without token
    r = client.get("/approvals/appr-poll")
    assert r.status_code == 401
    _cleanup()


def test_internal_approval_poll_passes_with_token(monkeypatch):
    """GET /approvals/{id} succeeds with a valid token."""
    client = _client(monkeypatch)
    client.post(
        "/approvals",
        json={
            "id": "appr-poll2",
            "situation_id": "s1",
            "playbook_id": "restart-pod",
            "requested_by": "action-service",
        },
        headers=_AUTH,
    )
    r = client.get("/approvals/appr-poll2", headers=_AUTH)
    assert r.status_code == 200
    _cleanup()


def test_internal_graduate_requires_token(monkeypatch):
    """POST /playbooks/{id}/graduate returns 401 without a token."""
    client = _client(monkeypatch)
    r = client.post("/playbooks/restart-pod/graduate", json={"decided_by": "feedback-service"})
    assert r.status_code == 401
    _cleanup()


def test_internal_graduate_passes_with_token(monkeypatch):
    """POST /playbooks/{id}/graduate succeeds with a valid token (may 404 if not seeded)."""
    client = _client(monkeypatch)
    r = client.post(
        "/playbooks/restart-pod/graduate", json={"decided_by": "feedback-service"}, headers=_AUTH
    )
    # 200 or 404 (not seeded) — but never 401 (auth)
    assert r.status_code != 401, f"Expected non-401, got {r.status_code}"
    _cleanup()


# --- External-facing endpoints: gated (token required) ---


def test_external_get_audit_gated(monkeypatch):
    """GET /audit (frontend) is gated — 401 without a token."""
    client = _client(monkeypatch)
    r = client.get("/audit")
    assert r.status_code == 401
    _cleanup()


def test_external_get_audit_passes_with_token(monkeypatch):
    """GET /audit (frontend) succeeds with a valid token."""
    client = _client(monkeypatch)
    r = client.get("/audit", headers=_AUTH)
    assert r.status_code == 200
    _cleanup()


def test_external_get_playbooks_gated(monkeypatch):
    """GET /playbooks (frontend) is gated — 401 without a token."""
    client = _client(monkeypatch)
    r = client.get("/playbooks")
    assert r.status_code == 401
    _cleanup()


def test_external_get_approvals_gated(monkeypatch):
    """GET /approvals (frontend) is gated — 401 without a token."""
    client = _client(monkeypatch)
    r = client.get("/approvals")
    assert r.status_code == 401
    _cleanup()


def test_external_decide_gated(monkeypatch):
    """POST /approvals/{id}/decide (frontend) is gated — 401 without a token."""
    client = _client(monkeypatch)
    r = client.post(
        "/approvals/appr-test/decide", json={"decision": "approved", "decided_by": "oncall-alice"}
    )
    assert r.status_code == 401
    _cleanup()


def test_external_decide_passes_with_token(monkeypatch):
    """POST /approvals/{id}/decide (frontend) succeeds with a valid token."""
    client = _client(monkeypatch)
    # Create approval first (with token)
    client.post(
        "/approvals",
        json={
            "id": "appr-decide",
            "situation_id": "s1",
            "playbook_id": "restart-pod",
            "requested_by": "action-service",
        },
        headers=_AUTH,
    )
    r = client.post(
        "/approvals/appr-decide/decide",
        json={"decision": "approved", "decided_by": "oncall-alice"},
        headers=_AUTH,
    )
    # 200 or 403 (RBAC) — but never 401 (auth)
    assert r.status_code != 401, f"Expected non-401, got {r.status_code}"
    _cleanup()
