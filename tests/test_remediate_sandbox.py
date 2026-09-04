from datetime import UTC, datetime

from common.contracts import (
    ApprovalRequest,
    HitlMode,
    Playbook,
    PreflightResult,
    RemediationOutcome,
    RemediationResult,
    RemediationStep,
    Situation,
    SituationStatus,
)
from services.action.remediate import execute_remediation


def _situation() -> Situation:
    now = datetime.now(UTC)
    return Situation(
        id="sit-1",
        status=SituationStatus.DIAGNOSED,
        severity="high",
        first_seen=now,
        last_seen=now,
        signature="sig-1",
    )


def _playbook(hitl: HitlMode) -> Playbook:
    return Playbook(
        id="pb-1",
        name="restart demo-app",
        match_rule="*",
        steps=[RemediationStep(action="restart")],
        hitl_mode=hitl,
        reversible=True,
    )


class _Gate:
    """Records audit decisions; approves HITL; captures the approval request."""

    def __init__(self, approve: bool = True):
        self._approve = approve
        self.audits: list[str] = []
        self.approval_request: ApprovalRequest | None = None

    def write_audit(self, record):
        self.audits.append(record.decision)

    def check_rbac(self, actor, action, resource):
        return True

    def request_approval(self, request: ApprovalRequest) -> ApprovalRequest:
        self.approval_request = request
        return request

    def await_decision(self, request_id, timeout):
        status = "approved" if self._approve else "rejected"
        return ApprovalRequest(
            id=request_id,
            situation_id="sit-1",
            playbook_id="pb-1",
            requested_by="action-service",
            status=status,
        )


class _Remediator:
    def __init__(self):
        self.executed = False

    def execute(self, plan):
        self.executed = True
        return True

    def rollback(self, plan):
        return True


class _Health:
    def check(self, situation, target):
        return True


class _StubSandbox:
    """Returns a fixed verdict; records the plan it was handed."""

    def __init__(self, passed: bool):
        self._passed = passed
        self.rehearsed_plan = None

    def rehearse(self, situation, plan) -> PreflightResult:
        self.rehearsed_plan = plan
        return PreflightResult(
            passed=self._passed,
            detail="sandbox: pod healthy" if self._passed else "sandbox: clone crashlooped",
            mode="k8s",
            sandbox_namespace="intelliops-sandbox-deadbeef",
        )


def test_auto_blocks_when_sandbox_fails():
    gate, remediator, health = _Gate(), _Remediator(), _Health()
    sandbox = _StubSandbox(passed=False)
    outcome = execute_remediation(
        _situation(), _playbook(HitlMode.AUTO), gate, remediator, health, sandbox, 1.0, 0.01
    )
    assert isinstance(outcome, RemediationOutcome)
    assert outcome.health_after == "preflight-failed"
    assert outcome.result == RemediationResult.FAILURE
    assert remediator.executed is False  # blocked — never touched the live target
    assert outcome.preflight is not None and outcome.preflight.passed is False


def test_hitl_proceeds_with_verdict_attached_when_sandbox_fails():
    gate, remediator, health = _Gate(approve=True), _Remediator(), _Health()
    sandbox = _StubSandbox(passed=False)
    outcome = execute_remediation(
        _situation(), _playbook(HitlMode.HITL), gate, remediator, health, sandbox, 1.0, 0.01
    )
    # HITL advises, not blocks: the human approved, so it executed.
    assert gate.approval_request is not None
    assert gate.approval_request.preflight is not None
    assert gate.approval_request.preflight.passed is False
    assert remediator.executed is True
    assert outcome.preflight is not None


def test_sandbox_pass_flows_to_outcome():
    gate, remediator, health = _Gate(), _Remediator(), _Health()
    sandbox = _StubSandbox(passed=True)
    outcome = execute_remediation(
        _situation(), _playbook(HitlMode.AUTO), gate, remediator, health, sandbox, 1.0, 0.01
    )
    assert remediator.executed is True
    assert outcome.result == RemediationResult.SUCCESS
    assert outcome.preflight is not None and outcome.preflight.passed is True
    assert sandbox.rehearsed_plan is not None  # the sandbox got the built plan


# ---------------------------------------------------------------------------
# Denylist gate (Gate 2.5) tests
# ---------------------------------------------------------------------------


def _pb(steps, hitl=HitlMode.HITL):
    return Playbook(
        id="pb-1", name="n", match_rule="*", steps=steps, hitl_mode=hitl, reversible=True
    )


class _SpySandbox:
    def __init__(self):
        self.rehearsed = False

    def rehearse(self, situation, plan):
        self.rehearsed = True
        return PreflightResult(passed=True, detail="", mode="off")


def test_denylist_blocks_unsafe_scale_before_sandbox():
    gate, remediator, health = _Gate(), _Remediator(), _Health()
    sandbox = _SpySandbox()
    # a scale delta that would drive replicas to zero
    pb = _pb([RemediationStep(action="scale", replicas=-50)])
    outcome = execute_remediation(_situation(), pb, gate, remediator, health, sandbox, 1.0, 0.01)
    assert outcome.result == RemediationResult.FAILURE
    assert outcome.health_after == "denied:unsafe-scale"
    assert remediator.executed is False  # never executed
    assert sandbox.rehearsed is False  # gate precedes the sandbox


def test_denylist_blocks_unsafe_limits():
    gate, remediator, health, sandbox = _Gate(), _Remediator(), _Health(), _SpySandbox()
    pb = _pb([RemediationStep(action="patch_resource_limits", cpu_limit="1m", mem_limit="1Ki")])
    outcome = execute_remediation(_situation(), pb, gate, remediator, health, sandbox, 1.0, 0.01)
    assert outcome.health_after == "denied:unsafe-limits"
    assert sandbox.rehearsed is False
    assert remediator.executed is False


def test_denylist_blocks_unsafe_probe():
    gate, remediator, health, sandbox = _Gate(), _Remediator(), _Health(), _SpySandbox()
    pb = _pb([RemediationStep(action="patch_probe", probe="liveness", failure_threshold=0)])
    outcome = execute_remediation(_situation(), pb, gate, remediator, health, sandbox, 1.0, 0.01)
    assert outcome.health_after == "denied:unsafe-probe"
    assert sandbox.rehearsed is False
    assert remediator.executed is False


def test_denylist_allows_safe_tier2_and_reaches_sandbox():
    gate, remediator, health, sandbox = _Gate(), _Remediator(), _Health(), _SpySandbox()
    pb = _pb([RemediationStep(action="patch_resource_limits", cpu_limit="500m", mem_limit="512Mi")])
    execute_remediation(_situation(), pb, gate, remediator, health, sandbox, 1.0, 0.01)
    assert sandbox.rehearsed is True  # safe step passed the gate → sandbox ran
    assert remediator.executed is True


def test_denylist_blocks_patch_probe_with_no_probe_field():
    gate, remediator, health, sandbox = _Gate(), _Remediator(), _Health(), _SpySandbox()
    # probe=None is the default — we can't know which probe to target
    pb = _pb([RemediationStep(action="patch_probe", period_seconds=10)])
    outcome = execute_remediation(_situation(), pb, gate, remediator, health, sandbox, 1.0, 0.01)
    assert outcome.health_after == "denied:unsafe-probe"
    assert sandbox.rehearsed is False
    assert remediator.executed is False


def test_denylist_blocks_patch_resource_limits_with_no_limits():
    gate, remediator, health, sandbox = _Gate(), _Remediator(), _Health(), _SpySandbox()
    # neither cpu_limit nor mem_limit set — a no-op step
    pb = _pb([RemediationStep(action="patch_resource_limits")])
    outcome = execute_remediation(_situation(), pb, gate, remediator, health, sandbox, 1.0, 0.01)
    assert outcome.health_after == "denied:unsafe-limits"
    assert sandbox.rehearsed is False
    assert remediator.executed is False


def test_denylist_blocks_rollback_to_revision_with_no_revision():
    gate, remediator, health, sandbox = _Gate(), _Remediator(), _Health(), _SpySandbox()
    # revision=None — indeterminate rollback target
    pb = _pb([RemediationStep(action="rollback_to_revision")])
    outcome = execute_remediation(_situation(), pb, gate, remediator, health, sandbox, 1.0, 0.01)
    assert outcome.health_after == "denied:unsafe-revision"
    assert sandbox.rehearsed is False
    assert remediator.executed is False
