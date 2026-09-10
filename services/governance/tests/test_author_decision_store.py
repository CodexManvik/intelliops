from datetime import UTC, datetime

from common.contracts import AuthorDecision
from common.interfaces import AuthorDecisionStore
from services.governance.adapters.author_decision_store import (
    InMemoryAuthorDecisionStore,
    PostgresAuthorDecisionStore,
)


def _decision(**kw):
    base = {
        "signature": "sig-x",
        "proposal_id": "prop-1",
        "playbook_id": "ai-sig-x-abc123",
        "actions": ["restart", "scale"],
        "cited_facts": ["restart worked 4/5 for sig-x"],
        "note": None,
        "disposition": "pending",
        "outcome": "unknown",
        "decided_by": None,
        "ts": datetime.now(UTC),
    }
    base.update(kw)
    return AuthorDecision(**base)


def test_record_and_query_by_signature():
    s = InMemoryAuthorDecisionStore()
    s.record(_decision())
    got = s.by_signature("sig-x")
    assert len(got) == 1
    assert got[0].actions == ["restart", "scale"]
    assert s.by_signature("other") == []


def test_update_disposition_by_proposal_id():
    s = InMemoryAuthorDecisionStore()
    s.record(_decision())
    s.update_disposition("prop-1", "accepted", "oncall-alice")
    d = s.by_signature("sig-x")[0]
    assert d.disposition == "accepted"
    assert d.decided_by == "oncall-alice"


def test_update_outcome_by_playbook_id():
    s = InMemoryAuthorDecisionStore()
    s.record(_decision())
    s.update_outcome("ai-sig-x-abc123", "worked", "healthy")
    d = s.by_signature("sig-x")[0]
    assert d.outcome == "worked"


def test_updates_are_noops_when_no_match():
    s = InMemoryAuthorDecisionStore()
    s.record(_decision())
    s.update_disposition("nope", "accepted", "x")  # no matching proposal_id
    s.update_outcome("nope", "worked", "healthy")  # no matching playbook_id
    d = s.by_signature("sig-x")[0]
    assert d.disposition == "pending" and d.outcome == "unknown"


def test_inmemory_satisfies_protocol():
    assert isinstance(InMemoryAuthorDecisionStore(), AuthorDecisionStore)


def test_postgres_store_satisfies_protocol_and_shape():
    # No live DB in this environment (unit-test tier) — assert the adapter's
    # shape (constructible from a bare engine handle, satisfies the same
    # Protocol as InMemory, exposes the 4 required methods) and leave live-DB
    # behavior to tests/test_postgres_author_decisions.py (@pytest.mark.postgres,
    # a real throwaway Postgres via testcontainers), matching how
    # PostgresTrainingStore is covered.
    store = PostgresAuthorDecisionStore(engine=object())
    assert isinstance(store, AuthorDecisionStore)
    for name in ("record", "by_signature", "update_disposition", "update_outcome"):
        assert callable(getattr(store, name))
