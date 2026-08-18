from common.contracts import RemediationPlan, RemediationStep, RemediationTarget
from services.action.adapters.remediator import DryRunRemediator, RecordingRemediator


def _plan():
    return RemediationPlan(
        target=RemediationTarget(namespace="ns", deployment="demo-app"),
        steps=[RemediationStep(action="restart")],
        rollback_steps=[RemediationStep(action="restart")],
    )

def test_dry_run_always_succeeds():
    r = DryRunRemediator()
    assert r.execute(_plan()) is True
    assert r.rollback(_plan()) is True

def test_recording_captures_plan():
    r = RecordingRemediator()
    p = _plan()
    r.execute(p)
    r.rollback(p)
    assert r.executed_plan is p
    assert r.rolled_back_plan is p

def test_recording_execute_result_configurable():
    r = RecordingRemediator(execute_result=False)
    assert r.execute(_plan()) is False
    assert r.rollback(_plan()) is True
