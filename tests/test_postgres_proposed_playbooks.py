"""Postgres proposed-playbook-store adapter tests.

AI runbook proposals awaiting human approval must survive a governance
restart (issue #56). These exercise the Postgres adapter against a real
throwaway Postgres (the `postgres` marker + `clean_db` fixture) with the same
shape as `tests/test_postgres_author_decisions.py`.
"""

from datetime import UTC, datetime

import pytest

from common.contracts import (
    HitlMode,
    Playbook,
    ProposedPlaybook,
    ProposedPlaybookStatus,
    RemediationStep,
)
from services.governance.adapters.proposed_store import PostgresProposedPlaybookStore

NOW = datetime(2026, 9, 10, tzinfo=UTC)


def _proposal(pid="prop-1", status=ProposedPlaybookStatus.PROPOSED, **kw):
    pb = Playbook(
        id="ai-sig-x-abc123",
        name="drafted",
        match_rule="*",
        steps=[RemediationStep(action="restart")],
        hitl_mode=HitlMode.HITL,
    )
    base = {
        "id": pid,
        "playbook": pb,
        "status": status,
        "proposed_by": "runbook-author",
        "rationale": "because cpu",
        "source_situation_id": "sit-1",
        "ts": NOW,
    }
    base.update(kw)
    return ProposedPlaybook(**base)


@pytest.mark.postgres
def test_add_and_get(clean_db):
    s = PostgresProposedPlaybookStore(clean_db)
    s.add(_proposal())
    got = s.get("prop-1")
    assert got is not None
    assert got.id == "prop-1"
    assert got.status == ProposedPlaybookStatus.PROPOSED
    assert got.source_situation_id == "sit-1"
    assert s.get("missing") is None


@pytest.mark.postgres
def test_add_replaces_existing_proposal(clean_db):
    # InMemory's `add` overwrites by id in a dict — Postgres must behave the
    # same way rather than accumulating duplicate rows for the same proposal.
    s = PostgresProposedPlaybookStore(clean_db)
    s.add(_proposal(rationale="first"))
    s.add(_proposal(rationale="second"))
    assert s.get("prop-1").rationale == "second"
    assert len(s.list()) == 1


@pytest.mark.postgres
def test_list_with_and_without_status(clean_db):
    s = PostgresProposedPlaybookStore(clean_db)
    s.add(_proposal("prop-1", status=ProposedPlaybookStatus.PROPOSED))
    s.add(_proposal("prop-2", status=ProposedPlaybookStatus.APPROVED))
    assert len(s.list()) == 2
    assert [p.id for p in s.list(status=ProposedPlaybookStatus.PROPOSED)] == ["prop-1"]
    assert [p.id for p in s.list(status=ProposedPlaybookStatus.APPROVED)] == ["prop-2"]
    assert s.list(status=ProposedPlaybookStatus.REJECTED) == []


@pytest.mark.postgres
def test_list_orders_by_insertion(clean_db):
    s = PostgresProposedPlaybookStore(clean_db)
    s.add(_proposal("prop-1"))
    s.add(_proposal("prop-2"))
    assert [p.id for p in s.list()] == ["prop-1", "prop-2"]


@pytest.mark.postgres
def test_set_status_flips_status_and_decided_by_and_persists(clean_db):
    s = PostgresProposedPlaybookStore(clean_db)
    s.add(_proposal())
    updated = s.set_status("prop-1", ProposedPlaybookStatus.APPROVED, "oncall-alice")
    assert updated.status == ProposedPlaybookStatus.APPROVED
    assert updated.decided_by == "oncall-alice"
    # persisted — a fresh read reflects the change
    got = s.get("prop-1")
    assert got.status == ProposedPlaybookStatus.APPROVED
    assert got.decided_by == "oncall-alice"


@pytest.mark.postgres
def test_set_status_returns_none_when_not_found(clean_db):
    s = PostgresProposedPlaybookStore(clean_db)
    assert s.set_status("missing", ProposedPlaybookStatus.REJECTED, "x") is None


@pytest.mark.postgres
def test_set_status_keeps_payload_consistent_with_column(clean_db):
    # The typed `status` column and the JSONB payload must never disagree —
    # the update is a read-modify-write in one transaction, not two writes.
    from sqlalchemy import text

    s = PostgresProposedPlaybookStore(clean_db)
    s.add(_proposal())
    s.set_status("prop-1", ProposedPlaybookStatus.REJECTED, "oncall-bob")
    with clean_db.connect() as conn:
        row = conn.execute(
            text("SELECT status, payload FROM proposed_playbooks WHERE proposal_id = 'prop-1'")
        ).one()
    assert row.status == "rejected"
    assert row.payload["status"] == "rejected"
    assert row.payload["decided_by"] == "oncall-bob"


@pytest.mark.postgres
def test_clear_empties_store(clean_db):
    s = PostgresProposedPlaybookStore(clean_db)
    s.add(_proposal("prop-1"))
    s.add(_proposal("prop-2"))
    s.clear()
    assert s.list() == []
    assert s.get("prop-1") is None


@pytest.mark.postgres
def test_jsonb_payload_roundtrip(clean_db):
    # The full ProposedPlaybook is stored as a JSONB payload and reconstructed
    # via from_payload — every field must survive the round-trip byte-for-byte.
    s = PostgresProposedPlaybookStore(clean_db)
    original = _proposal("prop-json", rationale="because memory pressure")
    s.add(original)
    assert s.get("prop-json") == original
