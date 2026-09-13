from datetime import UTC, datetime

from common.contracts import RemediationOutcome, RemediationResult, Situation, SituationStatus
from services.read.projection import ReadModel


def _sit(sid, status=SituationStatus.DETECTED, t=datetime(2026, 8, 16, tzinfo=UTC)):
    return Situation(
        id=sid,
        status=status,
        member_events=[],
        severity="high",
        first_seen=t,
        last_seen=t,
        signature=sid.replace("sit-", ""),
    )


MS = 1000


def test_terminal_old_situation_is_aged_out():
    rm = ReadModel(ttl_seconds=10, max_situations=50)
    rm.apply_detected(_sit("sit-1"))
    rm.apply_outcome(
        RemediationOutcome(
            situation_id="sit-1",
            playbook_id="p",
            result=RemediationResult.SUCCESS,
            health_after="healthy",
            ts=datetime(2026, 8, 16, tzinfo=UTC),
        )
    )
    # far in the future: > ttl past the outcome ts
    base = int(datetime(2026, 8, 16, tzinfo=UTC).timestamp() * 1000)
    assert len(rm.situations(now_ms=base + 20 * MS)) == 0


def test_active_situation_never_aged_out():
    rm = ReadModel(ttl_seconds=1, max_situations=50)
    rm.apply_detected(_sit("sit-1", status=SituationStatus.DETECTED))
    base = int(datetime(2026, 8, 16, tzinfo=UTC).timestamp() * 1000)
    assert len(rm.situations(now_ms=base + 999999)) == 1  # still detected → kept


def test_cap_evicts_oldest_terminal_first():
    rm = ReadModel(ttl_seconds=10_000, max_situations=2)
    for i in range(3):
        t = datetime(2026, 8, 16, 0, 0, i, tzinfo=UTC)
        rm.apply_detected(_sit(f"sit-{i}", t=t))
        rm.apply_outcome(
            RemediationOutcome(
                situation_id=f"sit-{i}",
                playbook_id="p",
                result=RemediationResult.SUCCESS,
                health_after="healthy",
                ts=t,
            )
        )
    ids = {s["id"] for s in rm.situations()}
    assert "sit-0" not in ids and len(ids) == 2  # oldest terminal evicted


def _escalate(rm, sid, t):
    rm.apply_detected(_sit(sid, t=t))
    rm.apply_outcome(
        RemediationOutcome(
            situation_id=sid,
            playbook_id="",
            result=RemediationResult.ESCALATED,
            health_after="escalated:no-diagnosis",
            ts=t,
            mode="none",
        )
    )


def _resolve(rm, sid, t):
    rm.apply_detected(_sit(sid, t=t))
    rm.apply_outcome(
        RemediationOutcome(
            situation_id=sid,
            playbook_id="p",
            result=RemediationResult.SUCCESS,
            health_after="healthy",
            ts=t,
        )
    )


def test_needs_attention_situation_is_never_aged_out():
    rm = ReadModel(ttl_seconds=10, max_situations=50)
    t = datetime(2026, 8, 16, tzinfo=UTC)
    _escalate(rm, "sit-1", t)
    base = int(t.timestamp() * 1000)
    # the incident that most needs a human must not silently disappear
    assert len(rm.situations(now_ms=base + 20 * MS)) == 1


def test_cap_evicts_terminal_before_needs_attention():
    rm = ReadModel(ttl_seconds=10_000, max_situations=2)
    _escalate(rm, "sit-esc", datetime(2026, 8, 16, 0, 0, 0, tzinfo=UTC))  # oldest
    _resolve(rm, "sit-ok", datetime(2026, 8, 16, 0, 0, 1, tzinfo=UTC))
    rm.apply_detected(_sit("sit-open", t=datetime(2026, 8, 16, 0, 0, 2, tzinfo=UTC)))
    ids = {s["id"] for s in rm.situations()}
    assert ids == {"sit-esc", "sit-open"}  # resolved dropped despite being newer

    # once no terminal situation remains, needs_attention is the fallback victim
    rm.apply_detected(_sit("sit-open-2", t=datetime(2026, 8, 16, 0, 0, 3, tzinfo=UTC)))
    ids = {s["id"] for s in rm.situations()}
    assert ids == {"sit-open", "sit-open-2"}
