from common.interfaces import Remediator
from services.action.adapters.remediator import DryRunRemediator, RecordingRemediator


def test_dryrun_satisfies_protocol_and_succeeds():
    r = DryRunRemediator()
    assert isinstance(r, Remediator)
    assert r.execute(["kubectl rollout restart deploy/web"]) is True
    assert r.rollback(["kubectl rollout undo deploy/web"]) is True


def test_recording_captures_calls():
    r = RecordingRemediator()
    r.execute(["step-a", "step-b"])
    r.rollback(["undo-a"])
    assert r.executed_steps == ["step-a", "step-b"]
    assert r.rolled_back_steps == ["undo-a"]


def test_recording_injects_results():
    r = RecordingRemediator(execute_result=False, rollback_result=True)
    assert r.execute(["x"]) is False
    assert r.rollback(["y"]) is True


def test_recording_satisfies_protocol():
    assert isinstance(RecordingRemediator(), Remediator)
