"""Verify AUTH_MODE=token doesn't break inter-service calls on governance.

The governance service hosts both external-facing endpoints (gated) and
internal service-to-service endpoints (exempt).  These tests exercise the
boundary in token mode, proving that action→governance and feedback→governance
calls succeed without a token while frontend-facing routes still require one.
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


# --- Internal-bus endpoints: exempt (no token needed) ---


def test_internal_rbac_check_exempt(monkeypatch):
    """POST /rbac/check (action→governance) passes without a token."""
    client = _client(monkeypatch)
    r = client.post("/rbac/check", json={"actor": "a", "action": "execute", "resource": "r"})
    assert r.status_code == 200
    _cleanup()


def test_internal_audit_write_exempt(monkeypatch):
    """POST /audit (action→governance) passes without a token."""
    client = _client(monkeypatch)
    r = client.post("/audit", json={
        "actor": "action-service", "action": "execute",
        "resource": "playbook:restart-pod", "decision": "allow",
        "ts": "2026-08-13T00:00:00Z", "correlation_id": "s1",
    })
    assert r.status_code == 200
    _cleanup()


def test_internal_approval_create_exempt(monkeypatch):
    """POST /approvals (action→governance) passes without a token."""
    client = _client(monkeypatch)
    r = client.post("/approvals", json={
        "id": "appr-test", "situation_id": "s1",
        "playbook_id": "restart-pod", "requested_by": "action-service",
    })
    assert r.status_code == 200
    _cleanup()


def test_internal_approval_poll_exempt(monkeypatch):
    """GET /approvals/{id} (action polls) passes without a token."""
    client = _client(monkeypatch)
    # First create an approval (exempt POST)
    client.post("/approvals", json={
        "id": "appr-poll", "situation_id": "s1",
        "playbook_id": "restart-pod", "requested_by": "action-service",
    })
    # Then poll it (exempt GET)
    r = client.get("/approvals/appr-poll")
    assert r.status_code == 200
    _cleanup()


def test_internal_graduate_exempt(monkeypatch):
    """POST /playbooks/{id}/graduate (feedback→governance) passes without a token."""
    client = _client(monkeypatch)
    # Graduate a seeded playbook — may 404 if not seeded, but must NOT be 401
    r = client.post("/playbooks/restart-pod/graduate",
                    json={"decided_by": "feedback-service"})
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
    r = client.get("/audit", headers={"Authorization": "Bearer test-secret"})
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
    r = client.post("/approvals/appr-test/decide",
                    json={"decision": "approved", "decided_by": "oncall-alice"})
    assert r.status_code == 401
    _cleanup()


def test_external_decide_passes_with_token(monkeypatch):
    """POST /approvals/{id}/decide (frontend) succeeds with a valid token."""
    client = _client(monkeypatch)
    # Create approval first (exempt)
    client.post("/approvals", json={
        "id": "appr-decide", "situation_id": "s1",
        "playbook_id": "restart-pod", "requested_by": "action-service",
    })
    r = client.post("/approvals/appr-decide/decide",
                    json={"decision": "approved", "decided_by": "oncall-alice"},
                    headers={"Authorization": "Bearer test-secret"})
    # 200 or 403 (RBAC) — but never 401 (auth)
    assert r.status_code != 401, f"Expected non-401, got {r.status_code}"
    _cleanup()
