from datetime import UTC, datetime

from common.contracts import RemediationResult, TrainingRecord
from common.interfaces import TrainingStore
from services.feedback.adapters.training_store import (
    FileTrainingStore,
    InMemoryTrainingStore,
)

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _record(sig="abc", worked=True):
    return TrainingRecord(
        situation_id=f"sit-{sig}",
        signature=sig,
        playbook_id="restart-pod",
        result=RemediationResult.SUCCESS if worked else RemediationResult.FAILURE,
        worked=worked,
        ts=NOW,
    )


def test_inmemory_satisfies_protocol():
    assert isinstance(InMemoryTrainingStore(), TrainingStore)


def test_inmemory_append_read():
    s = InMemoryTrainingStore()
    s.append(_record("a"))
    s.append(_record("b", worked=False))
    recs = s.read_all()
    assert len(recs) == 2
    assert recs[0].signature == "a"
    assert recs[1].worked is False


def test_file_store_roundtrips(tmp_path):
    path = tmp_path / "sub" / "training.jsonl"  # parent missing
    s = FileTrainingStore(str(path))
    s.append(_record("a"))
    s.append(_record("b", worked=False))
    reread = FileTrainingStore(str(path)).read_all()
    assert [r.signature for r in reread] == ["a", "b"]
    assert all(isinstance(r, TrainingRecord) for r in reread)


def test_file_store_missing_is_empty(tmp_path):
    assert FileTrainingStore(str(tmp_path / "none.jsonl")).read_all() == []


def test_file_satisfies_protocol(tmp_path):
    assert isinstance(FileTrainingStore(str(tmp_path / "t.jsonl")), TrainingStore)
