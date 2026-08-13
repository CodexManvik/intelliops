from common.config import get_settings
from common.interfaces import GovernanceGate, HealthChecker


def test_governance_gate_runtime_checkable():
    class FakeGate:
        def check_rbac(self, actor, action, resource): return True
        def request_approval(self, request): return request
        def await_decision(self, approval_id, timeout_seconds): return None
        def write_audit(self, record): ...

    assert isinstance(FakeGate(), GovernanceGate)


def test_health_checker_runtime_checkable():
    class FakeHealth:
        def check(self, situation): return True

    assert isinstance(FakeHealth(), HealthChecker)


def test_settings_have_hitl_timeouts():
    s = get_settings()
    assert isinstance(s.hitl_poll_timeout_seconds, float)
    assert isinstance(s.hitl_poll_interval_seconds, float)
    assert s.hitl_poll_timeout_seconds > 0
