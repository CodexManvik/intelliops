from datetime import UTC, datetime

from common.contracts import RemediationResult, TrainingRecord
from services.feedback.graduate import playbook_stats, should_graduate

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _rec(pb, result):
    return TrainingRecord(
        situation_id="sit-x",
        signature="x",
        playbook_id=pb,
        result=result,
        worked=result == RemediationResult.SUCCESS,
        ts=NOW,
    )


def test_playbook_stats_counts_per_playbook():
    recs = [
        _rec("pb1", RemediationResult.SUCCESS),
        _rec("pb1", RemediationResult.SUCCESS),
        _rec("pb1", RemediationResult.ROLLED_BACK),
        _rec("pb2", RemediationResult.FAILURE),
    ]
    s1 = playbook_stats(recs, "pb1")
    assert s1 == {"successes": 2, "failures": 0, "rollbacks": 1}
    s2 = playbook_stats(recs, "pb2")
    assert s2 == {"successes": 0, "failures": 1, "rollbacks": 0}


def test_should_graduate_true_on_clean_successes():
    assert should_graduate({"successes": 3, "failures": 0, "rollbacks": 0}, min_successes=3) is True


def test_should_graduate_false_below_threshold():
    assert (
        should_graduate({"successes": 2, "failures": 0, "rollbacks": 0}, min_successes=3) is False
    )


def test_should_graduate_false_with_any_rollback():
    assert (
        should_graduate({"successes": 5, "failures": 0, "rollbacks": 1}, min_successes=3) is False
    )


def test_should_graduate_false_with_any_failure():
    assert (
        should_graduate({"successes": 5, "failures": 1, "rollbacks": 0}, min_successes=3) is False
    )


def test_escalation_is_counted_in_no_bucket():
    """An escalation is not evidence about the playbook — nothing was attempted."""
    recs = [
        _rec("pb1", RemediationResult.SUCCESS),
        _rec("pb1", RemediationResult.ESCALATED),
    ]
    assert playbook_stats(recs, "pb1") == {"successes": 1, "failures": 0, "rollbacks": 0}


def test_escalation_does_not_block_graduation():
    """Regression: escalations used to arrive as FAILUREs, and should_graduate
    demands failures == 0 over all history — so one un-runnable suggestion
    disqualified a playbook forever, on a remediation nobody ever ran."""
    recs = [_rec("pb1", RemediationResult.SUCCESS) for _ in range(3)]
    recs.append(_rec("pb1", RemediationResult.ESCALATED))
    assert should_graduate(playbook_stats(recs, "pb1"), min_successes=3) is True
