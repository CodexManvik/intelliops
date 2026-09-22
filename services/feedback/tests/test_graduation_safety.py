"""Graduation must rest on real evidence, and must be reversible.

DryRunRemediator + AlwaysHealthyChecker report SUCCESS for every run, so three
approved dry runs used to graduate a playbook to AUTO. And nothing ever demoted
an auto playbook after it failed unattended.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from common.contracts import HitlMode, RemediationOutcome, RemediationResult, TrainingRecord
from common.envelope import publish_model
from services.feedback.adapters.training_store import InMemoryTrainingStore
from services.feedback.consumer import run_consumer
from services.feedback.graduate import playbook_stats, should_graduate
from services.feedback.label import label_outcome

NOW = datetime(2026, 9, 23, tzinfo=UTC)


def _rec(mode, result=RemediationResult.SUCCESS):
    return TrainingRecord(
        situation_id="sit-x",
        signature="x",
        playbook_id="pb",
        result=result,
        worked=result == RemediationResult.SUCCESS,
        ts=NOW,
        mode=mode,
    )


def _outcome(result, mode="k8s", hitl=HitlMode.HITL, pid="pb"):
    return RemediationOutcome(
        situation_id="sit-x",
        playbook_id=pid,
        result=result,
        health_after="x",
        ts=NOW,
        hitl_mode=hitl,
        mode=mode,
    )


def test_label_carries_the_mode():
    assert label_outcome(_outcome(RemediationResult.SUCCESS, mode="dry_run")).mode == "dry_run"


def test_dry_run_successes_do_not_graduate_by_default():
    recs = [_rec("dry_run") for _ in range(5)]
    stats = playbook_stats(recs, "pb")
    assert stats["successes"] == 0
    assert should_graduate(stats, 3) is False


def test_dry_run_successes_count_when_explicitly_allowed():
    recs = [_rec("dry_run") for _ in range(3)]
    assert should_graduate(playbook_stats(recs, "pb", count_simulated=True), 3) is True


def test_real_successes_still_graduate():
    recs = [_rec("k8s") for _ in range(3)]
    assert should_graduate(playbook_stats(recs, "pb"), 3) is True


def test_legacy_records_without_mode_keep_counting():
    recs = [_rec(None) for _ in range(3)]
    assert should_graduate(playbook_stats(recs, "pb"), 3) is True


class _ListBus:
    """Minimal bus: publish appends; consume yields everything published so far."""

    def __init__(self):
        self.entries: list[dict] = []

    def publish(self, topic, message):
        self.entries.append(message)

    def consume(self, topic, group):
        yield from list(self.entries)

    def send_to_dlq(self, *a, **k):
        return False


def _drive(outcomes, **kwargs):
    bus = _ListBus()
    for o in outcomes:
        publish_model(bus, "remediation.outcomes", o)
    graduated, demoted = [], []
    run_consumer(
        bus,
        InMemoryTrainingStore(),
        graduated.append,
        3,
        threading.Event(),
        demoter=demoted.append,
        **kwargs,
    )
    return graduated, demoted


def test_consumer_does_not_graduate_on_dry_runs():
    graduated, _ = _drive([_outcome(RemediationResult.SUCCESS, mode="dry_run")] * 4)
    assert graduated == []


def test_consumer_demotes_an_auto_playbook_that_fails():
    _, demoted = _drive([_outcome(RemediationResult.ROLLED_BACK, hitl=HitlMode.AUTO)])
    assert demoted == ["pb"]


def test_consumer_leaves_hitl_failures_alone():
    _, demoted = _drive([_outcome(RemediationResult.FAILURE, hitl=HitlMode.HITL)])
    assert demoted == []
