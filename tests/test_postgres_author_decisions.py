"""Postgres author-decision-store adapter tests.

The AI runbook author's memory of its own drafting decisions must survive a
governance restart. These exercise the Postgres adapter against a real
throwaway Postgres (the `postgres` marker + `clean_db` fixture) with the same
shape as `tests/test_postgres_approvals.py` / `tests/test_postgres_training.py`.
"""

from datetime import UTC, datetime

import pytest

from common.contracts import AuthorDecision
from services.governance.adapters.author_decision_store import PostgresAuthorDecisionStore

NOW = datetime(2026, 9, 10, tzinfo=UTC)


def _decision(sig="sig-x", proposal_id="prop-1", playbook_id="ai-sig-x-abc123", **kw):
    base = {
        "signature": sig,
        "proposal_id": proposal_id,
        "playbook_id": playbook_id,
        "actions": ["restart", "scale"],
        "cited_facts": ["restart worked 4/5 for sig-x"],
        "note": None,
        "ts": NOW,
    }
    base.update(kw)
    return AuthorDecision(**base)


@pytest.mark.postgres
def test_record_and_query_by_signature(clean_db):
    s = PostgresAuthorDecisionStore(clean_db)
    s.record(_decision())
    got = s.by_signature("sig-x")
    assert len(got) == 1
    assert got[0].actions == ["restart", "scale"]
    assert got[0].disposition == "pending"
    assert got[0].outcome == "unknown"
    assert s.by_signature("other") == []


@pytest.mark.postgres
def test_by_signature_orders_by_insertion(clean_db):
    s = PostgresAuthorDecisionStore(clean_db)
    s.record(_decision(proposal_id="prop-1"))
    s.record(_decision(proposal_id="prop-2"))
    got = s.by_signature("sig-x")
    assert [d.proposal_id for d in got] == ["prop-1", "prop-2"]


@pytest.mark.postgres
def test_update_disposition_by_proposal_id(clean_db):
    s = PostgresAuthorDecisionStore(clean_db)
    s.record(_decision())
    s.update_disposition("prop-1", "accepted", "oncall-alice")
    d = s.by_signature("sig-x")[0]
    assert d.disposition == "accepted"
    assert d.decided_by == "oncall-alice"


@pytest.mark.postgres
def test_update_outcome_by_playbook_id(clean_db):
    s = PostgresAuthorDecisionStore(clean_db)
    s.record(_decision())
    s.update_outcome("ai-sig-x-abc123", "worked", "healthy")
    d = s.by_signature("sig-x")[0]
    assert d.outcome == "worked"


@pytest.mark.postgres
def test_updates_are_noops_when_no_match(clean_db):
    # Never raises on a missing target — a missed update just leaves less
    # signal, never wrong signal (matches InMemoryAuthorDecisionStore).
    s = PostgresAuthorDecisionStore(clean_db)
    s.record(_decision())
    s.update_disposition("nope", "accepted", "x")
    s.update_outcome("nope", "worked", "healthy")
    d = s.by_signature("sig-x")[0]
    assert d.disposition == "pending" and d.outcome == "unknown"


@pytest.mark.postgres
def test_jsonb_payload_roundtrip(clean_db):
    # The full AuthorDecision is stored as a JSONB payload and reconstructed
    # via from_payload — every field must survive the round-trip byte-for-byte.
    s = PostgresAuthorDecisionStore(clean_db)
    original = _decision(sig="sig-json", proposal_id="prop-json", playbook_id="ai-sig-json-xyz")
    s.record(original)
    assert s.by_signature("sig-json")[0] == original


@pytest.mark.postgres
def test_update_disposition_keeps_payload_consistent_with_column(clean_db):
    # The typed `disposition` column and the JSONB payload must never disagree
    # — the update is a read-modify-write in one transaction, not two writes.
    from sqlalchemy import text

    s = PostgresAuthorDecisionStore(clean_db)
    s.record(_decision())
    s.update_disposition("prop-1", "rejected", "oncall-bob")
    with clean_db.connect() as conn:
        row = conn.execute(
            text("SELECT disposition, payload FROM author_decisions WHERE proposal_id = 'prop-1'")
        ).one()
    assert row.disposition == "rejected"
    assert row.payload["disposition"] == "rejected"
    assert row.payload["decided_by"] == "oncall-bob"
