from common.stores import make_stores
from services.governance.adapters.audit_sink import FileAuditSink


class _S:
    store_backend = "file"
    database_url = "postgresql+psycopg://x"
    audit_store_path = "data/audit.jsonl"
    training_store_path = "data/training.jsonl"
    playbook_store_path = "deploy/playbooks"


def test_file_backend_builds_file_adapters():
    s = make_stores(_S())
    assert isinstance(s.audit_sink, FileAuditSink)
    assert s.engine is None


def test_postgres_backend_selected(monkeypatch):
    # don't actually connect — just assert the postgres classes are chosen.
    from services.governance.adapters.audit_sink import PostgresAuditSink

    # PostgresPlaybookStore seeds on construction (opens a connection); stub the
    # seed loader to [] so the factory can be exercised without a live database.
    monkeypatch.setattr(
        "services.governance.adapters.playbook_store.load_seed_playbooks",
        lambda _path: [],
    )

    class _P(_S): store_backend = "postgres"
    # make_engine is called but lazy/pool — constructing the engine object does not connect.
    s = make_stores(_P())
    assert isinstance(s.audit_sink, PostgresAuditSink)
    assert s.engine is not None
