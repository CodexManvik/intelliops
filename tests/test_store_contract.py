# tests/test_store_contract.py
"""Every store backend must be observably interchangeable behind its interface.

Fast path (`-m "not postgres"`): inmem + file backends run the same contract
with no Docker required. Full path adds the postgres-marked tests, which pull
in the `clean_db` fixture (testcontainers Postgres) and exercise the same
shared assertion helpers so all three backends are provably interchangeable.
"""

from datetime import UTC, datetime

import pytest

from common.contracts import AuditRecord, HitlMode, Playbook, RemediationStep


def _audit(cid):
    return AuditRecord(
        actor="a",
        action="x",
        resource="r",
        decision="allow",
        ts=datetime(2026, 8, 20, tzinfo=UTC),
        correlation_id=cid,
    )


def _pb(pid, mode=HitlMode.HITL):
    return Playbook(
        id=pid,
        name="n",
        match_rule="x",
        steps=[RemediationStep(action="restart")],
        hitl_mode=mode,
        reversible=True,
        rollback_steps=[],
    )


def _assert_audit_contract(sink) -> None:
    """Shared write->read contract every AuditSink backend must satisfy identically."""
    sink.write(_audit("sit-1"))
    sink.write(_audit("sit-2"))
    sink.write(_audit("sit-1"))
    assert len(sink.records()) == 3
    assert len(sink.records(correlation_id="sit-1")) == 2
    assert sink.records(correlation_id="sit-1")[0] == _audit("sit-1")


def _assert_playbook_contract(store) -> None:
    """Shared register/upsert contract every PlaybookStore backend must satisfy identically."""
    store.register(_pb("p1", HitlMode.HITL))
    store.register(_pb("p1", HitlMode.AUTO))  # upsert on every backend
    assert store.get("p1").hitl_mode == HitlMode.AUTO
    assert len([p for p in store.list() if p.id == "p1"]) == 1


# --- inmem/file: unmarked, infra-free ---


def _inmem_audit():
    from services.governance.adapters.audit_sink import InMemoryAuditSink

    return InMemoryAuditSink()


def _file_audit(tmp_path):
    from services.governance.adapters.audit_sink import FileAuditSink

    return FileAuditSink(str(tmp_path / "audit.jsonl"))


@pytest.mark.parametrize("kind", ["inmem", "file"])
def test_audit_backends_agree(kind, tmp_path):
    sink = _inmem_audit() if kind == "inmem" else _file_audit(tmp_path)
    _assert_audit_contract(sink)


def _inmem_playbooks():
    from services.governance.adapters.playbook_store import InMemoryPlaybookStore

    return InMemoryPlaybookStore()


def _file_playbooks(tmp_path):
    from services.governance.adapters.playbook_store import FilePlaybookStore

    # fresh tmp_path has no seed YAMLs, so the store starts empty
    return FilePlaybookStore(str(tmp_path))


@pytest.mark.parametrize("kind", ["inmem", "file"])
def test_playbook_backends_agree(kind, tmp_path):
    store = _inmem_playbooks() if kind == "inmem" else _file_playbooks(tmp_path)
    _assert_playbook_contract(store)


# --- postgres: separate marked tests, same helpers, keeps Docker off the fast path ---


@pytest.mark.postgres
def test_audit_backend_agrees_postgres(clean_db):
    from services.governance.adapters.audit_sink import PostgresAuditSink

    _assert_audit_contract(PostgresAuditSink(clean_db))


@pytest.mark.postgres
def test_playbook_backend_agrees_postgres(clean_db, tmp_path):
    from services.governance.adapters.playbook_store import PostgresPlaybookStore

    # empty seed dir so seeding doesn't add rows that break the "exactly one p1" assertion
    _assert_playbook_contract(PostgresPlaybookStore(clean_db, seed_path=str(tmp_path)))
