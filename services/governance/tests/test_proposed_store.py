from datetime import UTC, datetime

from common.contracts import (
    HitlMode,
    Playbook,
    ProposedPlaybook,
    ProposedPlaybookStatus,
    RemediationStep,
)
from services.governance.adapters.proposed_store import InMemoryProposedPlaybookStore

NOW = datetime(2026, 9, 4, tzinfo=UTC)


def _prop(pid="prop-1", status=ProposedPlaybookStatus.PROPOSED):
    pb = Playbook(
        id="pb",
        name="n",
        match_rule="*",
        steps=[RemediationStep(action="restart")],
        hitl_mode=HitlMode.HITL,
    )
    return ProposedPlaybook(
        id=pid, playbook=pb, status=status, proposed_by="runbook-author", ts=NOW
    )


def test_add_get_list_set_status():
    s = InMemoryProposedPlaybookStore()
    s.add(_prop("prop-1"))
    s.add(_prop("prop-2"))
    assert s.get("prop-1").id == "prop-1"
    assert s.get("missing") is None
    assert len(s.list()) == 2
    updated = s.set_status("prop-1", ProposedPlaybookStatus.APPROVED, "oncall-alice")
    assert updated.status == ProposedPlaybookStatus.APPROVED
    assert updated.decided_by == "oncall-alice"
    assert len(s.list(status=ProposedPlaybookStatus.PROPOSED)) == 1  # only prop-2 remains proposed
    assert s.set_status("missing", ProposedPlaybookStatus.REJECTED, "x") is None


def test_clear():
    s = InMemoryProposedPlaybookStore()
    s.add(_prop())
    s.clear()
    assert s.list() == []
