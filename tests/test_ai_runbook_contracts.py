from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from common.contracts import (
    HitlMode,
    Playbook,
    ProposedPlaybook,
    ProposedPlaybookStatus,
    RemediationStep,
)

NOW = datetime(2026, 9, 4, tzinfo=UTC)


def _playbook(action="restart"):
    return Playbook(
        id="pb-1",
        name="n",
        match_rule="*",
        steps=[RemediationStep(action=action)],
        hitl_mode=HitlMode.HITL,
        reversible=True,
    )


def test_proposed_playbook_validates_and_defaults_status():
    p = ProposedPlaybook(id="prop-1", playbook=_playbook(), proposed_by="runbook-author", ts=NOW)
    assert p.status == ProposedPlaybookStatus.PROPOSED
    assert p.playbook.steps[0].action == "restart"
    assert p.rationale is None and p.decided_by is None


def test_inner_playbook_with_unsafe_action_cannot_be_constructed():
    # The load-bearing guarantee: an out-of-set action fails at the inner Playbook.
    with pytest.raises(ValidationError):
        ProposedPlaybook(
            id="prop-2",
            playbook={
                "id": "x",
                "name": "n",
                "match_rule": "*",
                "steps": [{"action": "delete"}],
                "hitl_mode": "hitl",
            },
            proposed_by="runbook-author",
            ts=NOW,
        )


def test_tier2_action_is_accepted_in_a_proposal():
    p = ProposedPlaybook(
        id="prop-3", playbook=_playbook(action="patch_probe"), proposed_by="runbook-author", ts=NOW
    )
    assert p.playbook.steps[0].action == "patch_probe"
