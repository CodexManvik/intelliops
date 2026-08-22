from datetime import UTC, datetime

from common.contracts import (
    DiagnosedSituation,
    HitlMode,
    Playbook,
    RemediationStep,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
)
from services.action.select import select_playbook
from services.governance.adapters.playbook_store import InMemoryPlaybookStore

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _diagnosed(runbook_id):
    sit = Situation(
        id="s1",
        status=SituationStatus.DIAGNOSED,
        member_events=[
            TelemetryEvent(
                source="p",
                kind=TelemetryKind.METRIC,
                name="cpu",
                value=1.0,
                labels={},
                ts=NOW,
                fingerprint="f",
            )
        ],
        severity="high",
        first_seen=NOW,
        last_seen=NOW,
        signature="sig",
    )
    return DiagnosedSituation(situation=sit, hypotheses=[], suggested_runbook_id=runbook_id)


def _store():
    s = InMemoryPlaybookStore()
    s.register(
        Playbook(
            id="restart-pod",
            name="Restart",
            match_rule="x",
            steps=[RemediationStep(action="restart")],
            hitl_mode=HitlMode.HITL,
            reversible=True,
            rollback_steps=[],
        )
    )
    return s


def test_selects_known_playbook():
    pb = select_playbook(_diagnosed("restart-pod"), _store())
    assert pb is not None
    assert pb.id == "restart-pod"


def test_none_when_no_runbook_id():
    assert select_playbook(_diagnosed(None), _store()) is None


def test_none_when_unknown_runbook_id():
    assert select_playbook(_diagnosed("does-not-exist"), _store()) is None
