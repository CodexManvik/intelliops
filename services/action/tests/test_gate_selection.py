from services.action.adapters.governance_gate import HttpGovernanceGate, InProcessGovernanceGate
from services.action.app import _make_gate
from services.governance.adapters.audit_sink import InMemoryAuditSink


class _S:
    governance_mode = "in_process"
    governance_url = "http://gov:8000"
    rbac_policy_path = "policies/rbac_policy.yaml"
    audit_store_path = "data/audit.jsonl"
    hitl_poll_interval_seconds = 0.5


def test_in_process_default():
    assert isinstance(_make_gate(_S(), InMemoryAuditSink()), InProcessGovernanceGate)


def test_http_when_mode_http():
    s = _S(); s.governance_mode = "http"
    assert isinstance(_make_gate(s, InMemoryAuditSink()), HttpGovernanceGate)
