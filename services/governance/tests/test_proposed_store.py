from datetime import UTC, datetime

from common.contracts import (
    HitlMode,
    Playbook,
    ProposedPlaybook,
    ProposedPlaybookStatus,
    RemediationStep,
)
from common.db import proposed_playbooks
from services.governance.adapters.proposed_store import InMemoryProposedPlaybookStore

NOW = datetime(2026, 9, 4, tzinfo=UTC)


def test_proposed_playbooks_table_shape():
    # No DB needed — just the table metadata (issue #56: proposals must be
    # persistable, mirroring author_decisions' promoted-column style).
    assert proposed_playbooks.name == "proposed_playbooks"
    cols = {c.name for c in proposed_playbooks.columns}
    assert cols == {"id", "proposal_id", "status", "source_situation_id", "ts", "payload"}
    assert proposed_playbooks.c.id.primary_key
    assert proposed_playbooks.c.proposal_id.nullable is False
    assert proposed_playbooks.c.status.nullable is False
    assert proposed_playbooks.c.payload.nullable is False


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
