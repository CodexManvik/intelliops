from datetime import UTC, datetime

from common.contracts import RemediationResult, TrainingRecord
from services.feedback.metrics import compute_metrics

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _rec(sig, result):
    return TrainingRecord(
        situation_id=f"sit-{sig}",
        signature=sig,
        playbook_id="pb",
        result=result,
        worked=result == RemediationResult.SUCCESS,
        ts=NOW,
    )


def test_empty_records_are_zeros():
    m = compute_metrics([])
    assert m["total_outcomes"] == 0
    assert m["success_rate"] == 0.0
    assert m["rollback_rate"] == 0.0
    assert m["failure_rate"] == 0.0
    assert m["by_signature"] == {}


def test_rates_and_counts():
    recs = [
        _rec("a", RemediationResult.SUCCESS),
        _rec("a", RemediationResult.SUCCESS),
        _rec("a", RemediationResult.ROLLED_BACK),
        _rec("b", RemediationResult.FAILURE),
    ]
    m = compute_metrics(recs)
    assert m["total_outcomes"] == 4
    assert m["success_rate"] == 0.5  # 2/4
    assert m["rollback_rate"] == 0.25  # 1/4
    assert m["failure_rate"] == 0.25  # 1/4
    assert m["by_result"] == {"success": 2, "failure": 1, "rolled_back": 1}
    assert m["by_signature"]["a"] == {"worked": 2, "total": 3}
    assert m["by_signature"]["b"] == {"worked": 0, "total": 1}


def test_note_present():
    assert "MTTR" in compute_metrics([])["note"]
