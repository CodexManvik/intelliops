from datetime import UTC, datetime

from common.contracts import AuditRecord
from common.interfaces import AuditSink
from services.governance.adapters.audit_sink import FileAuditSink, InMemoryAuditSink

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _record(cid="sit-1"):
    return AuditRecord(
        actor="rca-service",
        action="diagnose",
        resource="situation:sit-1",
        decision="allow",
        ts=NOW,
        correlation_id=cid,
    )


def test_inmemory_sink_satisfies_protocol():
    assert isinstance(InMemoryAuditSink(), AuditSink)


def test_inmemory_write_and_read():
    sink = InMemoryAuditSink()
    sink.write(_record())
    sink.write(_record("sit-2"))
    assert len(sink.records()) == 2
    assert sink.records()[0].correlation_id == "sit-1"


def test_file_sink_roundtrips(tmp_path):
    path = tmp_path / "sub" / "audit.jsonl"  # parent dir does not exist yet
    sink = FileAuditSink(str(path))
    sink.write(_record())
    sink.write(_record("sit-2"))
    # a fresh sink reads the same file back
    reread = FileAuditSink(str(path)).records()
    assert [r.correlation_id for r in reread] == ["sit-1", "sit-2"]
    assert all(isinstance(r, AuditRecord) for r in reread)


def test_file_sink_satisfies_protocol(tmp_path):
    assert isinstance(FileAuditSink(str(tmp_path / "a.jsonl")), AuditSink)
