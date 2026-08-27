from datetime import UTC, datetime

from common.contracts import (
    RemediationOutcome,
    RemediationResult,
    Situation,
    SituationStatus,
)


def test_remediation_outcome_has_steps_and_mode_defaults():
    o = RemediationOutcome(
        situation_id="sit-x",
        playbook_id="scale-service",
        result=RemediationResult.SUCCESS,
        health_after="healthy",
        ts=datetime.now(UTC),
    )
    assert o.steps == []
    assert o.mode == "dry_run"


def test_situation_has_optional_peak_score_and_baseline():
    s = Situation(
        id="sit-x",
        status=SituationStatus.DETECTED,
        severity="high",
        first_seen=datetime.now(UTC),
        last_seen=datetime.now(UTC),
        signature="x",
    )
    assert s.peak_score is None
    assert s.baseline is None
    s2 = s.model_copy(
        update={"peak_score": 6.3, "baseline": {"cpu_usage": {"mean": 18.0, "std": 2.0}}}
    )
    assert s2.peak_score == 6.3
    assert s2.baseline["cpu_usage"]["mean"] == 18.0
