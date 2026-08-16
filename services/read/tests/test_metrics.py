# services/read/tests/test_metrics.py
from datetime import UTC, datetime, timedelta

from common.contracts import (
    DiagnosedSituation,
    HitlMode,
    RemediationOutcome,
    RemediationResult,
    RootCauseHypothesis,
    Situation,
    SituationStatus,
)
from services.read.projection import ReadModel

T0 = datetime(2026, 8, 16, tzinfo=UTC)

def _sit(sid, status=SituationStatus.DETECTED, members=1, t=T0):
    from common.contracts import TelemetryEvent, TelemetryKind
    evs = [TelemetryEvent(source="p", kind=TelemetryKind.METRIC, name="cpu_usage",
           value=90.0, labels={"service":"web"}, ts=t, fingerprint=f"f{i}") for i in range(members)]
    return Situation(id=sid, status=status, member_events=evs, severity="high",
                     first_seen=t, last_seen=t, signature=sid.replace("sit-",""))

def test_empty_metrics_all_zero():
    m = ReadModel().metrics()
    assert m["successRate"] == 0.0 and m["mttrMinutes"] == 0.0
    assert m["situationsOpen"] == 0 and m["noiseReductionPct"] == 0.0
    assert set(m) == {"alertsIngested","situationsOpen","noiseReductionPct","mttrMinutes",
                      "autoRemediatedPct","suppressedToday","approvalsPending","successRate"}

def test_mttr_and_rates():
    rm = ReadModel()
    rm.apply_detected(_sit("sit-1", members=10))         # 10 alerts collapsed
    # resolve 2 minutes later, auto
    rm.apply_outcome(RemediationOutcome(situation_id="sit-1", playbook_id="p",
        result=RemediationResult.SUCCESS, health_after="healthy",
        ts=T0 + timedelta(minutes=2), hitl_mode=HitlMode.AUTO))
    m = rm.metrics()
    assert m["alertsIngested"] == 10
    assert abs(m["noiseReductionPct"] - 90.0) < 0.01      # 1 situation from 10 alerts
    assert abs(m["mttrMinutes"] - 2.0) < 0.01
    assert m["successRate"] == 1.0
    assert m["autoRemediatedPct"] == 100.0

def test_suppressed_count_increments():
    from services.read.projection import ReadModel
    rm = ReadModel()
    rm.apply_suppressed(_sit("sit-9"))
    rm.apply_suppressed(_sit("sit-10"))
    assert rm.metrics()["suppressedToday"] == 2

def test_open_and_pending_counts():
    rm = ReadModel()
    d = DiagnosedSituation(situation=_sit("sit-2", status=SituationStatus.DIAGNOSED),
        hypotheses=[RootCauseHypothesis(situation_id="sit-2", description="x",
            confidence=0.6, suggested_runbook_id="scale-service")],
        suggested_runbook_id="scale-service")
    rm.apply_diagnosed(d)
    m = rm.metrics()
    assert m["situationsOpen"] == 1
    assert m["approvalsPending"] == 1   # diagnosed + hitl + not resolved
