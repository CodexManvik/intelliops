from datetime import UTC, datetime

from common.contracts import (
    ApprovalRequest,
    HitlMode,
    Playbook,
    RemediationResult,
    RemediationStep,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
)
from services.action.adapters.health import FixedHealthChecker
from services.action.adapters.remediator import RecordingRemediator
from services.action.adapters.sandbox import NullSandbox
from services.action.remediate import execute_remediation

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _situation():
    return Situation(
        id="s1",
        status=SituationStatus.DIAGNOSED,
        member_events=[
            TelemetryEvent(
                source="p",
                kind=TelemetryKind.METRIC,
                name="cpu",
                value=1.0,
                labels={},
                ts=NOW,
                fingerprint="f",
            )
        ],
        severity="high",
        first_seen=NOW,
        last_seen=NOW,
        signature="sig",
    )


def _playbook(hitl=HitlMode.AUTO, reversible=True):
    return Playbook(
        id="restart-pod",
        name="Restart",
        match_rule="x",
        steps=[RemediationStep(action="restart")],
        hitl_mode=hitl,
        reversible=reversible,
        rollback_steps=[RemediationStep(action="restart")],
    )


class FakeGate:
    """A GovernanceGate whose behavior each test controls."""

    def __init__(self, rbac_allow=True, decision_status="approved"):
        self._rbac_allow = rbac_allow
        self._decision_status = decision_status
        self.audits = []

    def check_rbac(self, actor, action, resource):
        return self._rbac_allow

    def request_approval(self, request):
        return request

    def await_decision(self, approval_id, timeout_seconds):
        return ApprovalRequest(
            id=approval_id,
            situation_id="s1",
            playbook_id="restart-pod",
            requested_by="action-service",
            status=self._decision_status,
            decided_by="oncall-alice",
        )

    def write_audit(self, record):
        self.audits.append(record)


def _run(playbook, gate, remediator, health):
    return execute_remediation(
        _situation(),
        playbook,
        gate,
        remediator,
        health,
        NullSandbox(),
        timeout_seconds=1.0,
        poll_interval_seconds=0.01,
    )


# --- The three gates BLOCK (each asserts execute was NOT called) ---


def test_disabled_playbook_skips_no_execute():
    r = RecordingRemediator()
    out = _run(_playbook(hitl=HitlMode.DISABLED), FakeGate(), r, FixedHealthChecker(True))
    assert out.result == RemediationResult.FAILURE
    assert out.health_after == "skipped:disabled"
    assert r.executed_plan is None  # SAFETY: nothing executed


def test_non_reversible_refused_no_execute():
    r = RecordingRemediator()
    out = _run(_playbook(reversible=False), FakeGate(), r, FixedHealthChecker(True))
    assert out.result == RemediationResult.FAILURE
    assert out.health_after == "refused:not-reversible"
    assert r.executed_plan is None  # SAFETY: nothing executed


def test_rbac_denied_no_execute():
    r = RecordingRemediator()
    out = _run(_playbook(), FakeGate(rbac_allow=False), r, FixedHealthChecker(True))
    assert out.result == RemediationResult.FAILURE
    assert out.health_after == "denied:rbac"
    assert r.executed_plan is None  # SAFETY: fail closed


def test_hitl_rejected_no_execute():
    r = RecordingRemediator()
    out = _run(
        _playbook(hitl=HitlMode.HITL),
        FakeGate(decision_status="rejected"),
        r,
        FixedHealthChecker(True),
    )
    assert out.result == RemediationResult.FAILURE
    assert out.health_after == "aborted:rejected"
    assert r.executed_plan is None  # SAFETY: no execute on reject


def test_hitl_timeout_no_execute():
    r = RecordingRemediator()
    out = _run(
        _playbook(hitl=HitlMode.HITL),
        FakeGate(decision_status="pending"),
        r,
        FixedHealthChecker(True),
    )
    assert out.result == RemediationResult.FAILURE
    assert out.health_after == "aborted:timeout"
    assert r.executed_plan is None  # SAFETY: fail closed on timeout


# --- The happy + rollback paths ---


def test_auto_approved_executes_healthy_success():
    r = RecordingRemediator()
    out = _run(_playbook(hitl=HitlMode.AUTO), FakeGate(), r, FixedHealthChecker(True))
    assert out.result == RemediationResult.SUCCESS
    assert out.health_after == "healthy"
    assert out.hitl_mode == HitlMode.AUTO  # stamped from the playbook
    assert r.executed_plan is not None  # executed
    assert r.rolled_back_plan is None  # no rollback


def test_hitl_approved_executes():
    r = RecordingRemediator()
    out = _run(
        _playbook(hitl=HitlMode.HITL),
        FakeGate(decision_status="approved"),
        r,
        FixedHealthChecker(True),
    )
    assert out.result == RemediationResult.SUCCESS
    assert r.executed_plan is not None


def test_unhealthy_triggers_rollback():
    r = RecordingRemediator()
    out = _run(_playbook(), FakeGate(), r, FixedHealthChecker(False))
    assert out.result == RemediationResult.ROLLED_BACK
    assert out.health_after == "unhealthy:rolled-back"
    assert r.executed_plan is not None  # executed
    assert r.rolled_back_plan is not None  # then rolled back


def test_execute_failure_reported():
    r = RecordingRemediator(execute_result=False)
    out = _run(_playbook(), FakeGate(), r, FixedHealthChecker(True))
    assert out.result == RemediationResult.FAILURE
    assert out.health_after == "execute-failed"


def test_outcome_carries_situation_and_playbook_ids():
    out = _run(_playbook(), FakeGate(), RecordingRemediator(), FixedHealthChecker(True))
    assert out.situation_id == "s1"
    assert out.playbook_id == "restart-pod"


def test_audit_written_on_success():
    g = FakeGate()
    _run(_playbook(), g, RecordingRemediator(), FixedHealthChecker(True))
    assert any(a.action == "execute" and a.correlation_id == "s1" for a in g.audits)


def test_successful_outcome_records_steps_and_mode():
    playbook = _playbook(hitl=HitlMode.AUTO, reversible=True).model_copy(
        update={"steps": [RemediationStep(action="scale", replicas=2)]}
    )
    out = _run(
        playbook, FakeGate(), RecordingRemediator(execute_result=True), FixedHealthChecker(True)
    )
    assert out.result == RemediationResult.SUCCESS
    assert out.steps  # non-empty, human-readable
    assert any("scale" in s for s in out.steps)
    assert out.mode in ("dry_run", "k8s")
