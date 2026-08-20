from datetime import UTC, datetime

import pytest

from common.contracts import RemediationResult, TrainingRecord
from services.feedback.adapters.training_store import PostgresTrainingStore


def _rec(sig, worked=True):
    return TrainingRecord(
        situation_id="sit-1",
        signature=sig,
        playbook_id="restart-pod",
        result=RemediationResult.SUCCESS,
        worked=worked,
        ts=datetime(2026, 8, 20, tzinfo=UTC),
    )


@pytest.mark.postgres
def test_append_and_read_all_in_order(clean_db):
    s = PostgresTrainingStore(clean_db)
    s.append(_rec("aaa"))
    s.append(_rec("bbb"))
    got = s.read_all()
    assert [r.signature for r in got] == ["aaa", "bbb"]


@pytest.mark.postgres
def test_training_roundtrip_lossless(clean_db):
    s = PostgresTrainingStore(clean_db)
    original = _rec("sig-x", worked=False)
    s.append(original)
    assert s.read_all()[0] == original
