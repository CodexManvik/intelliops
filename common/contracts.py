"""Canonical data contracts passed between IntelliOps services.

These models are load-bearing: they are the shared vocabulary every service
uses over the bus. Defined once here so services cannot drift (see ADR-006).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class TelemetryKind(str, Enum):
    METRIC = "metric"
    LOG = "log"
    TRACE = "trace"


class SituationStatus(str, Enum):
    DETECTED = "detected"
    DIAGNOSED = "diagnosed"
    ACTING = "acting"
    RESOLVED = "resolved"
    FAILED = "failed"


class HitlMode(str, Enum):
    AUTO = "auto"
    HITL = "hitl"
    DISABLED = "disabled"


class RemediationResult(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    ROLLED_BACK = "rolled_back"


class TelemetryEvent(BaseModel):
    """A single normalized signal from any telemetry source."""

    source: str
    kind: TelemetryKind
    name: str
    value: float | None = None
    payload: dict | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    ts: datetime
    fingerprint: str


class Situation(BaseModel):
    """An alert storm collapsed into one incident — the universal currency."""

    id: str
    status: SituationStatus
    member_events: list[TelemetryEvent] = Field(default_factory=list)
    severity: str
    first_seen: datetime
    last_seen: datetime
    signature: str


class RootCauseHypothesis(BaseModel):
    situation_id: str
    description: str
    confidence: float
    evidence: list[str] = Field(default_factory=list)
    suggested_runbook_id: str | None = None


class RemediationStep(BaseModel):
    action: Literal["restart", "scale", "rollback_deploy", "wait"]
    replicas: int | None = None  # for scale: a delta, e.g. +2 / -2
    note: str | None = None  # human-readable / wait annotation


class RemediationTarget(BaseModel):
    namespace: str
    deployment: str


class RemediationPlan(BaseModel):
    target: RemediationTarget
    steps: list[RemediationStep] = Field(default_factory=list)
    rollback_steps: list[RemediationStep] = Field(default_factory=list)


class Playbook(BaseModel):
    id: str
    name: str
    match_rule: str
    steps: list[RemediationStep] = Field(default_factory=list)
    hitl_mode: HitlMode
    reversible: bool = False
    rollback_steps: list[RemediationStep] = Field(default_factory=list)


class ApprovalRequest(BaseModel):
    id: str
    situation_id: str
    playbook_id: str
    requested_by: str
    status: str = "pending"
    decided_by: str | None = None


class RemediationOutcome(BaseModel):
    situation_id: str
    playbook_id: str
    result: RemediationResult
    health_after: str
    ts: datetime
    hitl_mode: HitlMode = HitlMode.HITL


class AuditRecord(BaseModel):
    actor: str
    action: str
    resource: str
    decision: str
    ts: datetime
    correlation_id: str


class EnrichmentContext(BaseModel):
    """Change/deploy/topology context gathered for a Situation during RCA."""

    recent_deploys: list[dict] = Field(default_factory=list)
    topology: dict = Field(default_factory=dict)
    config_changes: list[dict] = Field(default_factory=list)


class DiagnosedSituation(BaseModel):
    """The currency of situations.diagnosed: a diagnosed Situation plus ranked
    root-cause hypotheses and the top suggested runbook. Additive — does not
    mutate the frozen Situation contract."""

    situation: Situation
    hypotheses: list[RootCauseHypothesis] = Field(default_factory=list)
    suggested_runbook_id: str | None = None


class TrainingRecord(BaseModel):
    """A labeled remediation outcome — training data that closes the loop.

    `worked` is True when the remediation succeeded; feedback derives `signature`
    from the situation id (the "sit-" prefix convention). Correlation reads these
    at retrain time to learn which signatures reliably self-heal."""

    situation_id: str
    signature: str
    playbook_id: str
    result: RemediationResult
    worked: bool
    ts: datetime
