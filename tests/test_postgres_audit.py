from datetime import UTC, datetime

import pytest

from common.contracts import AuditRecord
from services.governance.adapters.audit_sink import PostgresAuditSink


def _rec(cid, actor="a"):
    return AuditRecord(
        actor=actor,
        action="execute",
        resource="playbook:x",
        decision="allow",
        ts=datetime(2026, 8, 20, tzinfo=UTC),
        correlation_id=cid,
    )


@pytest.mark.postgres
def test_write_and_read_all(clean_db):
    s = PostgresAuditSink(clean_db)
    s.write(_rec("sit-1"))
    s.write(_rec("sit-2"))
    got = s.records()
    assert len(got) == 2 and {r.correlation_id for r in got} == {"sit-1", "sit-2"}


@pytest.mark.postgres
def test_filter_by_correlation_id(clean_db):
    s = PostgresAuditSink(clean_db)
    s.write(_rec("sit-1"))
    s.write(_rec("sit-1"))
    s.write(_rec("sit-2"))
    assert len(s.records(correlation_id="sit-1")) == 2
    assert len(s.records(correlation_id="nope")) == 0


@pytest.mark.postgres
def test_jsonb_roundtrip_lossless(clean_db):
    s = PostgresAuditSink(clean_db)
    original = _rec("sit-9", actor="oncall-alice")
    s.write(original)
    assert s.records()[0] == original  # reconstructed from payload, field-for-field
