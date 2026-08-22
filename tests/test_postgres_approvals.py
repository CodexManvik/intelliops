"""Postgres approval-store adapter tests.

Pending HITL approvals are live runtime state that must survive a governance
restart mid-incident. These exercise the Postgres adapter against a real
throwaway Postgres (the `postgres` marker + `clean_db` fixture) with the same
shape as `tests/test_postgres_playbooks.py`.
"""

import pytest

from common.contracts import ApprovalRequest
from services.governance.adapters.approval_store import PostgresApprovalStore


def _appr(aid="a1", status="pending"):
    return ApprovalRequest(
        id=aid,
        situation_id="s1",
        playbook_id="restart-pod",
        requested_by="action-service",
        status=status,
    )


@pytest.mark.postgres
def test_create_get_roundtrip(clean_db):
    s = PostgresApprovalStore(clean_db)
    s.create(_appr("a1"))
    got = s.get("a1")
    assert got is not None
    assert got.id == "a1"
    assert got.situation_id == "s1"
    assert got.playbook_id == "restart-pod"
    assert got.requested_by == "action-service"
    assert got.status == "pending"
    assert got.decided_by is None
    assert s.get("missing") is None


@pytest.mark.postgres
def test_decide_upserts_in_place(clean_db):
    s = PostgresApprovalStore(clean_db)
    s.create(_appr("a1"))
    updated = s.decide("a1", status="approved", decided_by="alice")
    # status flips and the decider is recorded
    assert updated is not None
    assert updated.status == "approved"
    assert updated.decided_by == "alice"
    # ...and the durable read agrees
    got = s.get("a1")
    assert got.status == "approved"
    assert got.decided_by == "alice"
    # decide is an UPSERT on id, not an insert — exactly one row for a1
    assert len([a for a in s.list_pending() if a.id == "a1"]) == 0
    # deciding a missing approval is a no-op returning None
    assert s.decide("missing", "approved", "alice") is None


@pytest.mark.postgres
def test_list_pending_excludes_decided(clean_db):
    s = PostgresApprovalStore(clean_db)
    s.create(_appr("a1"))
    s.create(_appr("a2"))
    s.decide("a1", status="approved", decided_by="alice")
    pending_ids = {a.id for a in s.list_pending()}
    assert "a2" in pending_ids
    assert "a1" not in pending_ids


@pytest.mark.postgres
def test_jsonb_payload_roundtrip(clean_db):
    # The full ApprovalRequest is stored as a JSONB payload and reconstructed
    # via from_payload — every field must survive the round-trip byte-for-byte.
    s = PostgresApprovalStore(clean_db)
    original = _appr("a-json").model_copy(update={"status": "rejected", "decided_by": "bob"})
    s.create(original)
    got = s.get("a-json")
    assert got == original
