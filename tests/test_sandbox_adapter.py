from datetime import UTC, datetime

from common.contracts import (
    PreflightResult,
    RemediationOutcome,
    RemediationPlan,
    RemediationResult,
    RemediationTarget,
    Situation,
    SituationStatus,
)
from services.action.adapters.sandbox import NullSandbox


def _situation() -> Situation:
    now = datetime.now(UTC)
    return Situation(
        id="sit-1",
        status=SituationStatus.DIAGNOSED,
        severity="high",
        first_seen=now,
        last_seen=now,
        signature="sig-1",
    )


def _plan() -> RemediationPlan:
    return RemediationPlan(target=RemediationTarget(namespace="intelliops", deployment="demo-app"))


def test_null_sandbox_passes_through():
    result = NullSandbox().rehearse(_situation(), _plan())
    assert isinstance(result, PreflightResult)
    assert result.passed is True
    assert result.mode == "off"
    assert result.sandbox_namespace is None


def test_preflight_is_additive_and_optional():
    # Existing constructions must still work with no preflight supplied.
    outcome = RemediationOutcome(
        situation_id="sit-1",
        playbook_id="pb-1",
        result=RemediationResult.SUCCESS,
        health_after="healthy",
        ts=datetime.now(UTC),
    )
    assert outcome.preflight is None
