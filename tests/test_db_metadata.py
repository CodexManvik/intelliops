# tests/test_db_metadata.py
from common.db import METADATA, audit_records, training_records, playbooks, to_payload, from_payload

def test_tables_registered():
    names = set(METADATA.tables)
    assert {"audit_records", "training_records", "playbooks"} <= names

def test_audit_columns():
    cols = {c.name for c in audit_records.columns}
    assert {"id", "correlation_id", "actor", "action", "resource", "decision", "ts", "payload"} <= cols

def test_playbooks_pk_is_id():
    assert [c.name for c in playbooks.primary_key.columns] == ["id"]

def test_payload_roundtrip():
    from datetime import UTC, datetime
    from common.contracts import AuditRecord
    rec = AuditRecord(actor="a", action="x", resource="r", decision="allow",
                      ts=datetime(2026, 8, 20, tzinfo=UTC), correlation_id="sit-1")
    payload = to_payload(rec)
    assert isinstance(payload, dict) and payload["correlation_id"] == "sit-1"
    back = from_payload(payload, AuditRecord)
    assert back == rec
