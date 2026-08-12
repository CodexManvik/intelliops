from datetime import datetime, timezone

import pytest

from common.contracts import (
    ApprovalRequest,
    AuditRecord,
    HitlMode,
    Playbook,
    RemediationOutcome,
    RemediationResult,
    RootCauseHypothesis,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
)

NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)


def _telemetry_event() -> TelemetryEvent:
    return TelemetryEvent(
        source="prometheus",
        kind=TelemetryKind.METRIC,
        name="cpu_usage",
        value=0.97,
        labels={"pod": "web-1"},
        ts=NOW,
        fingerprint="abc123",
    )


@pytest.mark.parametrize(
    "model",
    [
        _telemetry_event(),
        Situation(
            id="s1",
            status=SituationStatus.DETECTED,
            member_events=[_telemetry_event()],
            severity="high",
            first_seen=NOW,
            last_seen=NOW,
            signature="sig1",
        ),
        RootCauseHypothesis(
            situation_id="s1",
            description="pod crash loop",
            confidence=0.8,
            evidence=["restart count spiked"],
            suggested_runbook_id="restart-pod",
        ),
        Playbook(
            id="restart-pod",
            name="Restart Pod",
            match_rule="signature == 'sig1'",
            steps=["kubectl rollout restart deploy/web"],
            hitl_mode=HitlMode.HITL,
            reversible=True,
            rollback_steps=["kubectl rollout undo deploy/web"],
        ),
        ApprovalRequest(
            id="a1",
            situation_id="s1",
            playbook_id="restart-pod",
            requested_by="action-service",
            status="pending",
        ),
        RemediationOutcome(
            situation_id="s1",
            playbook_id="restart-pod",
            result=RemediationResult.SUCCESS,
            health_after="healthy",
            ts=NOW,
        ),
        AuditRecord(
            actor="action-service",
            action="execute_playbook",
            resource="restart-pod",
            decision="allow",
            ts=NOW,
            correlation_id="corr-1",
        ),
    ],
)
def test_contract_roundtrips(model):
    """Every contract survives a dump -> validate round-trip unchanged."""
    restored = type(model).model_validate(model.model_dump())
    assert restored == model


def test_enums_have_exact_values():
    assert {s.value for s in SituationStatus} == {
        "detected",
        "diagnosed",
        "acting",
        "resolved",
        "failed",
    }
    assert {m.value for m in HitlMode} == {"auto", "hitl", "disabled"}
    assert {r.value for r in RemediationResult} == {"success", "failure", "rolled_back"}


def test_reversible_playbook_defaults():
    pb = Playbook(id="p", name="p", match_rule="true", steps=["x"], hitl_mode=HitlMode.AUTO)
    assert pb.reversible is False
    assert pb.rollback_steps == []
