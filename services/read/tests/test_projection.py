from datetime import UTC, datetime

from common.contracts import (
    DiagnosedSituation,
    RemediationOutcome,
    RemediationResult,
    RootCauseHypothesis,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
)
from services.read.projection import ReadModel

TS = datetime(2026, 8, 15, tzinfo=UTC)


def _sit(sid="sit-1", status=SituationStatus.DETECTED):
    return Situation(
        id=sid,
        status=status,
        member_events=[],
        severity="high",
        first_seen=TS,
        last_seen=TS,
        signature=sid.replace("sit-", ""),
    )


def test_detected_then_diagnosed_then_resolved():
    rm = ReadModel(max_outcomes=10)
    rm.apply_detected(_sit())
    assert rm.situations()[0]["status"] == "detected"

    rm.apply_diagnosed(
        DiagnosedSituation(
            situation=_sit(status=SituationStatus.DIAGNOSED),
            hypotheses=[
                RootCauseHypothesis(
                    situation_id="sit-1",
                    description="deploy",
                    confidence=0.8,
                    suggested_runbook_id="rollback-deploy",
                )
            ],
            suggested_runbook_id="rollback-deploy",
        )
    )
    s = rm.situations()[0]
    assert s["status"] == "diagnosed"
    assert s["hypotheses"][0]["confidence"] == 0.8
    assert s["suggested_runbook_id"] == "rollback-deploy"

    rm.apply_outcome(
        RemediationOutcome(
            situation_id="sit-1",
            playbook_id="rollback-deploy",
            result=RemediationResult.SUCCESS,
            health_after="healthy",
            ts=TS,
        )
    )
    assert rm.situations()[0]["status"] == "resolved"
    assert rm.outcomes()[0]["reason"] == "healthy"


def test_failure_outcome_marks_situation_failed():
    rm = ReadModel(max_outcomes=10)
    rm.apply_detected(_sit())
    rm.apply_outcome(
        RemediationOutcome(
            situation_id="sit-1",
            playbook_id="p",
            result=RemediationResult.FAILURE,
            health_after="aborted:timeout",
            ts=TS,
        )
    )
    assert rm.situations()[0]["status"] == "failed"


def test_outcomes_capped_most_recent_first():
    rm = ReadModel(max_outcomes=2)
    for i in range(3):
        rm.apply_outcome(
            RemediationOutcome(
                situation_id=f"sit-{i}",
                playbook_id="p",
                result=RemediationResult.SUCCESS,
                health_after="healthy",
                ts=TS,
            )
        )
    outs = rm.outcomes()
    assert len(outs) == 2
    assert outs[0]["situation_id"] == "sit-2"


def _sit_with_labels(labels):
    ev = TelemetryEvent(
        source="prometheus",
        kind=TelemetryKind.METRIC,
        name="cpu_usage",
        value=90.0,
        labels=labels,
        ts=datetime(2026, 8, 16, tzinfo=UTC),
        fingerprint="fp",
    )
    return Situation(
        id="sit-x",
        status=SituationStatus.DETECTED,
        member_events=[ev],
        severity="high",
        first_seen=datetime(2026, 8, 16, tzinfo=UTC),
        last_seen=datetime(2026, 8, 16, tzinfo=UTC),
        signature="x",
    )


def test_service_of_precedence_service():
    from services.read.projection import ReadModel

    assert (
        ReadModel._service_of(_sit_with_labels({"service": "web", "job": "j", "instance": "i"}))
        == "web"
    )


def test_service_of_precedence_job_then_instance():
    from services.read.projection import ReadModel

    assert ReadModel._service_of(_sit_with_labels({"job": "api"})) == "api"
    assert ReadModel._service_of(_sit_with_labels({"instance": "host:9100"})) == "host:9100"


def test_service_of_unknown_when_no_labels():
    from services.read.projection import ReadModel

    assert ReadModel._service_of(_sit_with_labels({})) == "unknown"


def test_projection_keeps_evidence_and_joins_outcome():
    from datetime import UTC, datetime

    from common.contracts import (
        DiagnosedSituation,
        RemediationOutcome,
        RemediationResult,
        RootCauseHypothesis,
        Situation,
        SituationStatus,
        TelemetryEvent,
        TelemetryKind,
    )
    from services.read.projection import ReadModel

    ts = datetime(2026, 1, 1, tzinfo=UTC)
    ev = TelemetryEvent(
        source="prom",
        kind=TelemetryKind.METRIC,
        name="cpu_usage",
        value=92.0,
        labels={"service": "web"},
        ts=ts,
        fingerprint="fp",
    )
    sit = Situation(
        id="sit-1",
        status=SituationStatus.DETECTED,
        member_events=[ev],
        severity="high",
        first_seen=ts,
        last_seen=ts,
        signature="1",
        peak_score=6.3,
        baseline={"cpu_usage": {"mean": 18.0, "std": 2.0}},
    )
    hyp = RootCauseHypothesis(
        situation_id="sit-1",
        description="resource saturation",
        confidence=0.6,
        evidence=["metrics: cpu_usage"],
        suggested_runbook_id="scale-service",
        explanation="Likely cause: CPU.",
    )
    m = ReadModel()
    m.apply_detected(sit)
    m.apply_diagnosed(
        DiagnosedSituation(situation=sit, hypotheses=[hyp], suggested_runbook_id="scale-service")
    )
    m.apply_outcome(
        RemediationOutcome(
            situation_id="sit-1",
            playbook_id="scale-service",
            result=RemediationResult.SUCCESS,
            health_after="healthy",
            ts=ts,
            steps=["scale web +2 replicas"],
            mode="dry_run",
        )
    )

    s = next(x for x in m.situations() if x["id"] == "sit-1")
    assert s["member_events"][0]["name"] == "cpu_usage"
    assert s["member_events"][0]["value"] == 92.0
    assert s["hypotheses"][0]["evidence"] == ["metrics: cpu_usage"]
    assert s["hypotheses"][0]["explanation"] == "Likely cause: CPU."
    assert s["peak_score"] == 6.3
    assert s["baseline"]["cpu_usage"]["mean"] == 18.0
    assert "resource saturation" in s["title"].lower()
    assert s["outcome"]["health_after"] == "healthy"
    assert s["outcome"]["steps"] == ["scale web +2 replicas"]
    assert s["stages"]["detected"] is not None
    assert s["stages"]["resolved"] is not None
