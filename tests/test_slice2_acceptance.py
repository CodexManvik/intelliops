"""Slice-2 acceptance: a detected Situation is diagnosed end-to-end in-process."""

import threading
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
from common.envelope import decode_model, publish_model
from services.governance.adapters.audit_sink import InMemoryAuditSink
from services.governance.adapters.playbook_store import InMemoryPlaybookStore
from services.rca.consumer import run_consumer

NOW = datetime(2026, 8, 13, tzinfo=UTC)


class InMemoryBus:
    def __init__(self):
        self.topics: dict[str, list[dict]] = {}

    def publish(self, topic, message):
        self.topics.setdefault(topic, []).append(message)

    def consume(self, topic, group):
        yield from list(self.topics.get(topic, []))


class DeployProvider:
    """Context provider that reports a recent deploy of 'web'."""

    def recent_deploys(self):
        return [{"service": "web", "version": "v2", "ts": NOW.isoformat()}]

    def topology_for(self, labels):
        return {"web": ["db"]}

    def config_changes(self):
        return []


def test_detected_situation_is_diagnosed_with_recent_deploy_hypothesis():
    bus = InMemoryBus()
    audit = InMemoryAuditSink()
    store = InMemoryPlaybookStore()
    store.register(Playbook(id="rollback-deploy", name="Rollback Deployment",
                            match_rule="x", steps=[RemediationStep(action="restart")],
                            hitl_mode=HitlMode.HITL, reversible=True,
                            rollback_steps=[]))

    # A detected Situation on the 'web' service.
    situation = Situation(
        id="sit-web-1", status=SituationStatus.DETECTED,
        member_events=[TelemetryEvent(
            source="prom", kind=TelemetryKind.METRIC, name="cpu_usage", value=99.0,
            labels={"service": "web"}, ts=NOW, fingerprint="fp",
        )],
        severity="high", first_seen=NOW, last_seen=NOW, signature="sig-web",
    )
    publish_model(bus, "situations.detected", situation)

    # Run RCA against a provider that knows about the recent 'web' deploy.
    run_consumer(bus, DeployProvider(), store, audit, threading.Event())

    # Exactly one DiagnosedSituation, top hypothesis = recent deploy → rollback.
    diagnosed_msgs = bus.topics.get("situations.diagnosed", [])
    assert len(diagnosed_msgs) == 1
    d = decode_model(diagnosed_msgs[0], DiagnosedSituation)
    assert d.situation.status == SituationStatus.DIAGNOSED
    assert d.hypotheses[0].suggested_runbook_id == "rollback-deploy"
    assert d.suggested_runbook_id == "rollback-deploy"

    # Audit trail recorded, threaded by the situation id.
    records = audit.records()
    assert len(records) == 1
    assert records[0].action == "diagnose"
    assert records[0].correlation_id == "sit-web-1"
