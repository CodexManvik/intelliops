from datetime import UTC, datetime

from common.contracts import HitlMode, RemediationOutcome, RemediationResult


def test_outcome_defaults_hitl_mode():
    # existing constructors omit hitl_mode → must still work, defaulting to HITL
    o = RemediationOutcome(situation_id="s", playbook_id="p",
                           result=RemediationResult.SUCCESS, health_after="healthy",
                           ts=datetime.now(UTC))
    assert o.hitl_mode == HitlMode.HITL


def test_outcome_accepts_hitl_mode():
    o = RemediationOutcome(situation_id="s", playbook_id="p",
                           result=RemediationResult.SUCCESS, health_after="healthy",
                           ts=datetime.now(UTC), hitl_mode=HitlMode.AUTO)
    assert o.hitl_mode == HitlMode.AUTO
