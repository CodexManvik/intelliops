from datetime import UTC, datetime

from common.contracts import RemediationResult, TrainingRecord
from common.evidence import is_real, quiet_eligible, real_records, track_record

NOW = datetime(2026, 9, 23, tzinfo=UTC)


def _rec(worked=True, mode="k8s", sig="s", pid="restart-pod"):
    return TrainingRecord(
        situation_id=f"sit-{sig}",
        signature=sig,
        playbook_id=pid,
        result=RemediationResult.SUCCESS if worked else RemediationResult.ROLLED_BACK,
        worked=worked,
        ts=NOW,
        mode=mode,
    )


def test_only_real_runs_are_evidence():
    assert is_real(_rec(mode="k8s"))
    assert not is_real(_rec(mode="dry_run"))
    assert not is_real(_rec(mode=None))  # predates the mode field: unknowable
    assert not is_real({"mode": "none"})  # escalation: nothing ran
    assert len(real_records([_rec(), _rec(mode="dry_run"), _rec(mode=None)])) == 1


def test_track_record_narrows_to_signature_and_playbook():
    recs = [_rec(), _rec(worked=False), _rec(pid="scale-service"), _rec(sig="other")]
    assert (track_record(recs, "s").worked, track_record(recs, "s").total) == (2, 3)
    rp = track_record(recs, "s", "restart-pod")
    assert (rp.worked, rp.total) == (1, 2)


def test_quiet_needs_enough_real_runs_of_this_playbook():
    three = [_rec(), _rec(), _rec()]
    assert quiet_eligible(three, "s", "restart-pod", 0.8, 3)[0] is True
    assert quiet_eligible(three[:2], "s", "restart-pod", 0.8, 3)[0] is False  # too few
    assert quiet_eligible(three, "s", "scale-service", 0.8, 3)[0] is False  # other playbook
    dry = [_rec(mode="dry_run")] * 5
    assert quiet_eligible(dry, "s", "restart-pod", 0.8, 3)[0] is False  # simulations


def test_quiet_needs_a_high_enough_success_rate():
    recs = [_rec(), _rec(), _rec(), _rec(worked=False)]  # 3/4 = 0.75
    ok, record = quiet_eligible(recs, "s", "restart-pod", 0.8, 3)
    assert ok is False
    assert str(record) == "3/4 real runs worked"
