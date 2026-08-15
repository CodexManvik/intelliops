import httpx

from common.contracts import ApprovalRequest
from services.action.adapters.governance_gate import HttpGovernanceGate


def _gate(handler, poll=0.0):
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    return HttpGovernanceGate("http://gov:8000", poll_interval_seconds=poll, http_client=client)


def test_check_rbac_true():
    def h(req):
        assert req.url.path == "/rbac/check"
        return httpx.Response(200, json={"allowed": True})
    assert _gate(h).check_rbac("action-service", "execute", "playbook:x") is True


def test_await_decision_returns_when_approved():
    calls = {"n": 0}

    def h(req):
        if req.url.path == "/approvals/appr-1" and req.method == "GET":
            calls["n"] += 1
            status = "approved" if calls["n"] >= 2 else "pending"
            return httpx.Response(200, json={
                "id": "appr-1", "situation_id": "sit-1", "playbook_id": "p",
                "requested_by": "action-service", "status": status, "decided_by": None,
            })
        return httpx.Response(404)

    decided = _gate(h).await_decision("appr-1", timeout_seconds=5.0)
    assert decided.status == "approved"
    assert calls["n"] >= 2


def test_await_decision_times_out_still_pending():
    def h(req):
        return httpx.Response(200, json={
            "id": "appr-1", "situation_id": "sit-1", "playbook_id": "p",
            "requested_by": "action-service", "status": "pending", "decided_by": None,
        })
    decided = _gate(h).await_decision("appr-1", timeout_seconds=0.05)
    assert decided.status == "pending"
