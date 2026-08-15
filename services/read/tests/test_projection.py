from datetime import UTC, datetime

from common.contracts import (
    DiagnosedSituation,
    RemediationOutcome,
    RemediationResult,
    RootCauseHypothesis,
    Situation,
    SituationStatus,
)
from services.read.projection import ReadModel

TS = datetime(2026, 8, 15, tzinfo=UTC)


def _sit(sid="sit-1", status=SituationStatus.DETECTED):
    return Situation(id=sid, status=status, member_events=[], severity="high",
                     first_seen=TS, last_seen=TS, signature=sid.replace("sit-", ""))


def test_detected_then_diagnosed_then_resolved():
    rm = ReadModel(max_outcomes=10)
    rm.apply_detected(_sit())
    assert rm.situations()[0]["status"] == "detected"

    rm.apply_diagnosed(DiagnosedSituation(
        situation=_sit(status=SituationStatus.DIAGNOSED),
        hypotheses=[RootCauseHypothesis(situation_id="sit-1", description="deploy",
                                        confidence=0.8, suggested_runbook_id="rollback-deploy")],
        suggested_runbook_id="rollback-deploy",
    ))
    s = rm.situations()[0]
    assert s["status"] == "diagnosed"
    assert s["hypotheses"][0]["confidence"] == 0.8
    assert s["suggested_runbook_id"] == "rollback-deploy"

    rm.apply_outcome(RemediationOutcome(situation_id="sit-1", playbook_id="rollback-deploy",
                                        result=RemediationResult.SUCCESS, health_after="healthy", ts=TS))
    assert rm.situations()[0]["status"] == "resolved"
    assert rm.outcomes()[0]["reason"] == "healthy"


def test_failure_outcome_marks_situation_failed():
    rm = ReadModel(max_outcomes=10)
    rm.apply_detected(_sit())
    rm.apply_outcome(RemediationOutcome(situation_id="sit-1", playbook_id="p",
                                        result=RemediationResult.FAILURE,
                                        health_after="aborted:timeout", ts=TS))
    assert rm.situations()[0]["status"] == "failed"


def test_outcomes_capped_most_recent_first():
    rm = ReadModel(max_outcomes=2)
    for i in range(3):
        rm.apply_outcome(RemediationOutcome(situation_id=f"sit-{i}", playbook_id="p",
                                            result=RemediationResult.SUCCESS,
                                            health_after="healthy", ts=TS))
    outs = rm.outcomes()
    assert len(outs) == 2
    assert outs[0]["situation_id"] == "sit-2"
