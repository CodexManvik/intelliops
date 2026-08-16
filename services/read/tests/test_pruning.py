from datetime import UTC, datetime

from common.contracts import RemediationOutcome, RemediationResult, Situation, SituationStatus
from services.read.projection import ReadModel


def _sit(sid, status=SituationStatus.DETECTED, t=datetime(2026,8,16,tzinfo=UTC)):
    return Situation(id=sid, status=status, member_events=[], severity="high",
                     first_seen=t, last_seen=t, signature=sid.replace("sit-",""))

MS = 1000

def test_terminal_old_situation_is_aged_out():
    rm = ReadModel(ttl_seconds=10, max_situations=50)
    rm.apply_detected(_sit("sit-1"))
    rm.apply_outcome(RemediationOutcome(situation_id="sit-1", playbook_id="p",
        result=RemediationResult.SUCCESS, health_after="healthy",
        ts=datetime(2026,8,16,tzinfo=UTC)))
    # far in the future: > ttl past the outcome ts
    base = int(datetime(2026,8,16,tzinfo=UTC).timestamp()*1000)
    assert len(rm.situations(now_ms=base + 20*MS)) == 0

def test_active_situation_never_aged_out():
    rm = ReadModel(ttl_seconds=1, max_situations=50)
    rm.apply_detected(_sit("sit-1", status=SituationStatus.DETECTED))
    base = int(datetime(2026,8,16,tzinfo=UTC).timestamp()*1000)
    assert len(rm.situations(now_ms=base + 999999)) == 1  # still detected → kept

def test_cap_evicts_oldest_terminal_first():
    rm = ReadModel(ttl_seconds=10_000, max_situations=2)
    for i in range(3):
        t = datetime(2026,8,16,0,0,i,tzinfo=UTC)
        rm.apply_detected(_sit(f"sit-{i}", t=t))
        rm.apply_outcome(RemediationOutcome(situation_id=f"sit-{i}", playbook_id="p",
            result=RemediationResult.SUCCESS, health_after="healthy", ts=t))
    ids = {s["id"] for s in rm.situations()}
    assert "sit-0" not in ids and len(ids) == 2  # oldest terminal evicted
