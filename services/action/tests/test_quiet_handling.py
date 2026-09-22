"""Quiet handling: a reliably-fixed signature is remediated without paging a human,
but only on the playbook's own real track record, and never out of sight.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from common.contracts import (
    ApprovalRequest,
    DiagnosedSituation,
    HitlMode,
    Playbook,
    PreflightResult,
    RemediationOutcome,
    RemediationResult,
    RemediationStep,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
    TrainingRecord,
)
from common.envelope import decode_model
from services.action.adapters.health import AlwaysHealthyChecker
from services.action.adapters.remediator import RecordingRemediator
from services.action.adapters.sandbox import NullSandbox
from services.action.consumer import run_consumer
from services.feedback.adapters.training_store import InMemoryTrainingStore
from services.governance.adapters.playbook_store import InMemoryPlaybookStore

NOW = datetime(2026, 9, 23, tzinfo=UTC)


class Gate:
    """Records whether anyone was asked; approves if asked."""

    def __init__(self):
        self.audits = []
        self.approval_requests = []

    def check_rbac(self, actor, action, resource):
        return True

    def request_approval(self, request):
        self.approval_requests.append(request)
        return request

    def await_decision(self, approval_id, timeout_seconds):
        return ApprovalRequest(
            id=approval_id,
            situation_id="s1",
            playbook_id="restart-pod",
            requested_by="action-service",
            status="approved",
            decided_by="oncall-alice",
        )

    def write_audit(self, record):
        self.audits.append(record)


class Bus:
    def __init__(self, script):
        self._script = script
        self.published = []

    def publish(self, topic, message):
        self.published.append((topic, message))

    def consume(self, topic, group):
        yield from self._script

    def send_to_dlq(self, *a, **k):
        return False


class FailingSandbox:
    def rehearse(self, situation, plan):
        return PreflightResult(passed=False, mode="k8s", detail="clone never became ready")


def _msg(handling="quiet"):
    sit = Situation(
        id="s1",
        status=SituationStatus.DIAGNOSED,
        member_events=[
            TelemetryEvent(
                source="p",
                kind=TelemetryKind.METRIC,
                name="memory_usage_mb",
                value=900.0,
                labels={"service": "meridian-gateway"},
                ts=NOW,
                fingerprint="f",
            )
        ],
        severity="high",
        first_seen=NOW,
        last_seen=NOW,
        signature="sig",
        handling=handling,
    )
    d = DiagnosedSituation(situation=sit, hypotheses=[], suggested_runbook_id="restart-pod")
    return {"data": d.model_dump_json()}


def _playbooks(mode=HitlMode.HITL):
    s = InMemoryPlaybookStore()
    s.register(
        Playbook(
            id="restart-pod",
            name="Restart",
            match_rule="x",
            steps=[RemediationStep(action="restart")],
            hitl_mode=mode,
            reversible=True,
            rollback_steps=[RemediationStep(action="restart")],
        )
    )
    return s


def _history(n, mode="k8s", worked=True, pid="restart-pod"):
    store = InMemoryTrainingStore()
    for _ in range(n):
        store.append(
            TrainingRecord(
                situation_id="sit-sig",
                signature="sig",
                playbook_id=pid,
                result=RemediationResult.SUCCESS if worked else RemediationResult.ROLLED_BACK,
                worked=worked,
                ts=NOW,
                mode=mode,
            )
        )
    return store


def _run(msg, history, playbooks=None, sandbox=None, skip_allowed=True):
    bus, gate, rem = Bus([msg]), Gate(), RecordingRemediator()
    run_consumer(
        bus,
        playbooks or _playbooks(),
        gate,
        rem,
        AlwaysHealthyChecker(),
        sandbox or NullSandbox(),
        1.0,
        0.01,
        threading.Event(),
        training_store=history,
        quiet_threshold=0.8,
        quiet_min_samples=3,
        quiet_skip_approval=skip_allowed,
    )
    outcome = decode_model(bus.published[-1][1], RemediationOutcome)
    return outcome, gate, rem


def _audit_decisions(gate):
    return [(a.action, a.decision) for a in gate.audits]


def test_proven_playbook_runs_without_asking_and_is_audited():
    outcome, gate, rem = _run(_msg(), _history(3))
    assert gate.approval_requests == []  # nobody was paged
    assert rem.executed_plan is not None  # but the fix ran
    assert outcome.result == RemediationResult.SUCCESS
    assert outcome.handling == "quiet"
    decisions = _audit_decisions(gate)
    assert ("quiet-handling", "quiet:skip-approval (3/3 real runs worked)") in decisions
    assert ("execute", "quiet-approved") in decisions


def test_too_little_evidence_falls_back_to_a_human():
    outcome, gate, _rem = _run(_msg(), _history(2))
    assert len(gate.approval_requests) == 1
    assert outcome.handling == "normal"
    assert ("quiet-handling", "quiet:needs-approval (2/2 real runs worked)") in _audit_decisions(
        gate
    )


def test_dry_run_history_is_not_evidence():
    outcome, gate, _ = _run(_msg(), _history(10, mode="dry_run"))
    assert len(gate.approval_requests) == 1
    assert outcome.handling == "normal"


def test_another_playbooks_record_does_not_count():
    _, gate, _ = _run(_msg(), _history(5, pid="scale-service"))
    assert len(gate.approval_requests) == 1


def test_a_poor_record_falls_back():
    store = _history(3)
    for r in _history(2, worked=False).read_all():
        store.append(r)  # 3/5 = 0.6 < 0.8
    _, gate, _ = _run(_msg(), store)
    assert len(gate.approval_requests) == 1


def test_skipping_approval_can_be_switched_off():
    outcome, gate, _ = _run(_msg(), _history(3), skip_allowed=False)
    assert len(gate.approval_requests) == 1
    assert outcome.handling == "normal"


def test_a_failed_rehearsal_blocks_a_quiet_run():
    # With no human to read the preflight verdict, a failed rehearsal is a hard stop.
    outcome, gate, rem = _run(_msg(), _history(3), sandbox=FailingSandbox())
    assert rem.executed_plan is None
    assert outcome.health_after == "preflight-failed"
    assert gate.approval_requests == []


def test_normal_situations_are_untouched():
    outcome, gate, _ = _run(_msg(handling="normal"), _history(10))
    assert len(gate.approval_requests) == 1
    assert outcome.handling == "normal"
    assert not any(a.action == "quiet-handling" for a in gate.audits)


def test_an_auto_playbook_is_marked_quiet_without_a_waiver():
    outcome, gate, _ = _run(_msg(), _history(0), playbooks=_playbooks(HitlMode.AUTO))
    assert gate.approval_requests == []
    assert outcome.handling == "quiet"


def test_the_other_gates_still_apply_to_a_quiet_run():
    # A waived approval is the ONLY thing quiet handling changes.
    s = InMemoryPlaybookStore()
    s.register(
        Playbook(
            id="restart-pod",
            name="Restart",
            match_rule="x",
            steps=[RemediationStep(action="restart")],
            hitl_mode=HitlMode.HITL,
            reversible=False,
        )
    )
    outcome, _, rem = _run(_msg(), _history(3), playbooks=s)
    assert outcome.health_after == "refused:not-reversible"
    assert rem.executed_plan is None
