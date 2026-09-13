"""Cold-start rebuild of the read projection (issue #58, P2.2)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from common.contracts import (
    DiagnosedSituation,
    RemediationOutcome,
    RemediationResult,
    RootCauseHypothesis,
    Situation,
    SituationStatus,
)
from common.envelope import publish_model
from services.read.rebuild import rebuild

TS = datetime(2026, 9, 14, tzinfo=UTC)


class _Settings:
    read_outcomes_max = 100
    read_situation_ttl_seconds = 600.0
    read_situations_max = 100
    read_rebuild_mode = "replay"
    read_rebuild_window_seconds = 3600.0
    read_rebuild_max_entries = 20_000


@pytest.fixture()
def bus():
    fakeredis = pytest.importorskip("fakeredis")
    from common.bus import RedisBus

    return RedisBus(client=fakeredis.FakeStrictRedis(decode_responses=True))


def _sit(sid="sit-1", status=SituationStatus.DETECTED):
    return Situation(
        id=sid,
        status=status,
        member_events=[],
        severity="high",
        first_seen=TS,
        last_seen=TS,
        signature=sid.replace("sit-", ""),
    )


def test_disabled_by_default_returns_none(bus):
    s = _Settings()
    s.read_rebuild_mode = "off"
    assert rebuild(bus, s) is None


def test_replays_history_into_a_fresh_model(bus):
    publish_model(bus, "situations.detected", _sit())
    model = rebuild(bus, _Settings())
    assert model is not None
    assert [x["id"] for x in model.situations()] == ["sit-1"]


def test_outcome_is_applied_after_its_situation(bus):
    """Ordering is correctness, not style: apply_outcome only mutates a situation
    it already knows, so replaying outcomes before detected would silently drop
    the terminal status and MTTR."""
    publish_model(bus, "situations.detected", _sit())
    publish_model(
        bus,
        "situations.diagnosed",
        DiagnosedSituation(
            situation=_sit(status=SituationStatus.DIAGNOSED),
            hypotheses=[
                RootCauseHypothesis(
                    situation_id="sit-1",
                    description="d",
                    confidence=0.8,
                    suggested_runbook_id="restart-pod",
                )
            ],
            suggested_runbook_id="restart-pod",
        ),
    )
    publish_model(
        bus,
        "remediation.outcomes",
        RemediationOutcome(
            situation_id="sit-1",
            playbook_id="restart-pod",
            result=RemediationResult.SUCCESS,
            health_after="healthy",
            ts=TS,
        ),
    )
    model = rebuild(bus, _Settings())
    assert model is not None
    assert model.situations()[0]["status"] == "resolved"


def test_escalation_survives_the_rebuild(bus):
    publish_model(bus, "situations.detected", _sit("sit-esc"))
    publish_model(
        bus,
        "remediation.outcomes",
        RemediationOutcome(
            situation_id="sit-esc",
            playbook_id="",
            result=RemediationResult.ESCALATED,
            health_after="escalated:no-diagnosis",
            ts=TS,
        ),
    )
    model = rebuild(bus, _Settings())
    assert model is not None
    assert model.situations()[0]["status"] == "needs_attention"
    assert model.metrics()["needsAttention"] == 1


def test_partial_rebuild_is_never_swapped_in(bus):
    """Tripping the safety valve must discard the WHOLE shadow model, not serve
    a half-built projection."""
    for i in range(5):
        publish_model(bus, "situations.detected", _sit(f"sit-{i}"))
    s = _Settings()
    s.read_rebuild_max_entries = 2
    assert rebuild(bus, s) is None


def test_rebuild_advances_the_live_consumer_group(bus):
    """After a successful replay the shadow owns that history, so the live tail
    must not re-apply it."""
    publish_model(bus, "situations.detected", _sit())
    assert rebuild(bus, _Settings()) is not None
    pending = bus._r.xpending("situations.detected", "read-model")["pending"]
    assert pending == 0
    # Nothing new to serve: the group was advanced past the replayed entry.
    info = bus._r.xinfo_groups("situations.detected")[0]
    assert info["lag"] == 0 or info["last-delivered-id"] != "0-0"
