from datetime import UTC, datetime

from common.contracts import RemediationOutcome, RemediationResult, TrainingRecord
from services.feedback.label import label_outcome, signature_from_situation_id

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def test_signature_from_situation_id_strips_prefix():
    assert signature_from_situation_id("sit-abc123") == "abc123"
    # no prefix -> unchanged
    assert signature_from_situation_id("abc123") == "abc123"


def _outcome(result):
    return RemediationOutcome(
        situation_id="sit-abc123",
        playbook_id="restart-pod",
        result=result,
        health_after="healthy",
        ts=NOW,
    )


def test_label_success_sets_worked_true():
    r = label_outcome(_outcome(RemediationResult.SUCCESS))
    assert isinstance(r, TrainingRecord)
    assert r.signature == "abc123"
    assert r.playbook_id == "restart-pod"
    assert r.result == RemediationResult.SUCCESS
    assert r.worked is True


def test_label_failure_sets_worked_false():
    assert label_outcome(_outcome(RemediationResult.FAILURE)).worked is False


def test_label_rolled_back_sets_worked_false():
    assert label_outcome(_outcome(RemediationResult.ROLLED_BACK)).worked is False
