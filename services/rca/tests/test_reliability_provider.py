from datetime import UTC, datetime

from common.contracts import RemediationResult, TrainingRecord
from services.rca.app import ReliabilityProvider

NOW = datetime(2026, 9, 23, tzinfo=UTC)


def _rec(sig, pid, worked):
    return TrainingRecord(
        situation_id=f"sit-{sig}",
        signature=sig,
        playbook_id=pid,
        result=RemediationResult.SUCCESS if worked else RemediationResult.ROLLED_BACK,
        worked=worked,
        ts=NOW,
    )


class _Store:
    def __init__(self, records):
        self.records = records
        self.reads = 0

    def read_all(self):
        self.reads += 1
        return list(self.records)


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_track_record_is_per_runbook():
    store = _Store(
        [
            _rec("s", "scale-service", True),
            _rec("s", "restart-pod", False),
            _rec("s", "restart-pod", True),
        ]
    )
    rel = ReliabilityProvider(store)
    assert rel("s", "scale-service") == 1.0
    assert rel("s", "restart-pod") == 0.5
    assert rel("s") == 2 / 3
    assert rel("other", "scale-service") == 0.0


def test_new_outcomes_are_seen_after_the_refresh_period():
    store = _Store([])
    clock = _Clock()
    rel = ReliabilityProvider(store, refresh_seconds=60, clock=clock)
    assert rel("s", "scale-service") == 0.0

    store.records.append(_rec("s", "scale-service", True))
    clock.t = 30
    assert rel("s", "scale-service") == 0.0  # still cached
    clock.t = 61
    assert rel("s", "scale-service") == 1.0  # the boot-time closure never got here
    assert store.reads == 2


def test_a_failed_read_keeps_the_last_table():
    store = _Store([_rec("s", "scale-service", True)])
    clock = _Clock()
    rel = ReliabilityProvider(store, refresh_seconds=1, clock=clock)
    assert rel("s", "scale-service") == 1.0

    def boom():
        raise RuntimeError("db down")

    store.read_all = boom
    clock.t = 5
    assert rel("s", "scale-service") == 1.0
