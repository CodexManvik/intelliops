from datetime import UTC, datetime
from common.contracts import AuthorDecision
from services.governance.adapters.author_decision_store import InMemoryAuthorDecisionStore


def _decision(**kw):
    base = dict(
        signature="sig-x",
        proposal_id="prop-1",
        playbook_id="ai-sig-x-abc123",
        actions=["restart", "scale"],
        cited_facts=["restart worked 4/5 for sig-x"],
        note=None,
        disposition="pending",
        outcome="unknown",
        decided_by=None,
        ts=datetime.now(UTC),
    )
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
    s.update_disposition("nope", "accepted", "x")   # no matching proposal_id
    s.update_outcome("nope", "worked", "healthy")   # no matching playbook_id
    d = s.by_signature("sig-x")[0]
    assert d.disposition == "pending" and d.outcome == "unknown"
