"""The same incident recurring under the same id, and events arriving out of order.

A Situation's id is its signature, so a recurrence reuses the id; and the read
model consumes each topic on its own thread, so a detection can land after its
own diagnosis or outcome.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from common.contracts import (
    DiagnosedSituation,
    RemediationOutcome,
    RemediationResult,
    RootCauseHypothesis,
    Situation,
    SituationStatus,
)
from services.read.projection import ReadModel

T1 = datetime(2026, 9, 23, 10, tzinfo=UTC)
T2 = T1 + timedelta(minutes=20)


def _sit(first, status=SituationStatus.DETECTED):
    return Situation(
        id="sit-a",
        status=status,
        member_events=[],
        severity="high",
        first_seen=first,
        last_seen=first,
        signature="a",
    )


def _diag(first):
    return DiagnosedSituation(
        situation=_sit(first, SituationStatus.DIAGNOSED),
        hypotheses=[
            RootCauseHypothesis(
                situation_id="sit-a",
                description="saturation",
                confidence=0.6,
                suggested_runbook_id="scale-service",
            )
        ],
        suggested_runbook_id="scale-service",
    )


def _outcome(ts, result=RemediationResult.SUCCESS):
    return RemediationOutcome(
        situation_id="sit-a",
        playbook_id="scale-service",
        result=result,
        health_after="healthy",
        ts=ts,
    )


def test_a_recurrence_starts_a_clean_card():
    rm = ReadModel()
    rm.apply_detected(_sit(T1))
    rm.apply_diagnosed(_diag(T1))
    rm.apply_outcome(_outcome(T1 + timedelta(minutes=2)))
    assert rm.situation("sit-a")["status"] == "resolved"

    rm.apply_detected(_sit(T2))
    card = rm.situation("sit-a")
    assert card["status"] == "detected"
    assert "outcome" not in card  # not the previous run's result
    assert card["hypotheses"] == []
    assert set(card["stages"]) == {"detected"}


def test_a_late_detection_does_not_reopen_a_resolved_card():
    rm = ReadModel()
    rm.apply_diagnosed(_diag(T1))
    rm.apply_outcome(_outcome(T1 + timedelta(minutes=2)))
    rm.apply_detected(_sit(T1))  # lost the race to its own outcome
    assert rm.situation("sit-a")["status"] == "resolved"


def test_a_late_diagnosis_does_not_reopen_a_resolved_card():
    rm = ReadModel()
    rm.apply_detected(_sit(T1))
    rm.apply_outcome(_outcome(T1 + timedelta(minutes=2)))
    rm.apply_diagnosed(_diag(T1))
    card = rm.situation("sit-a")
    assert card["status"] == "resolved"
    assert card["suggested_runbook_id"] == "scale-service"  # the data still lands


def test_an_event_from_an_earlier_occurrence_is_ignored():
    rm = ReadModel()
    rm.apply_detected(_sit(T2))
    rm.apply_diagnosed(_diag(T1))  # replayed out of order during a rebuild
    card = rm.situation("sit-a")
    assert card["first_seen"] == int(T2.timestamp() * 1000)
    assert card["hypotheses"] == []


def test_quiet_handling_is_visible_on_the_card_and_counted():
    rm = ReadModel()
    rm.apply_detected(_sit(T1).model_copy(update={"handling": "quiet"}))
    assert rm.situation("sit-a")["handling"] == "quiet"
    rm.apply_outcome(_outcome(T1 + timedelta(minutes=1)).model_copy(update={"handling": "quiet"}))
    assert rm.situation("sit-a")["outcome"]["handling"] == "quiet"
    assert rm.outcomes()[0]["handling"] == "quiet"
    assert rm.metrics()["quietlyHandled"] == 1
