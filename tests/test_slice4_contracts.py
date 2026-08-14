from datetime import UTC, datetime

from common.config import get_settings
from common.contracts import RemediationResult, TrainingRecord
from common.interfaces import TrainingStore

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def test_training_record_roundtrips():
    r = TrainingRecord(situation_id="sit-abc", signature="abc", playbook_id="restart-pod",
                       result=RemediationResult.SUCCESS, worked=True, ts=NOW)
    restored = TrainingRecord.model_validate(r.model_dump())
    assert restored == r
    assert restored.worked is True


def test_training_store_runtime_checkable():
    class FakeStore:
        def append(self, record): ...
        def read_all(self): return []

    assert isinstance(FakeStore(), TrainingStore)


def test_settings_have_slice4_fields():
    s = get_settings()
    assert s.training_store_path.endswith(".jsonl")
    assert 0.0 < s.reliability_suppress_threshold <= 1.0
    assert s.graduation_min_successes >= 1
