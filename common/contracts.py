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
    peak_score: float | None = None  # correlator max z-score for the window
    baseline: dict | None = None  # per-metric {name: {mean, std}} at emit time


class RootCauseHypothesis(BaseModel):
    situation_id: str
    description: str
    confidence: float
    evidence: list[str] = Field(default_factory=list)
    suggested_runbook_id: str | None = None
    explanation: str | None = None
    explanation_source: str | None = None  # "llm" | "template" — provenance of `explanation`
    confidence_source: str | None = None  # "embedding" | "rule" — provenance of `confidence`


class RemediationStep(BaseModel):
    action: Literal[
        "restart",
        "scale",
        "rollback_deploy",
        "wait",
        "patch_resource_limits",
        "rollback_to_revision",
        "patch_probe",
    ]
    replicas: int | None = None  # for scale: a delta, e.g. +2 / -2
    note: str | None = None  # human-readable / wait annotation
    # patch_resource_limits: new container resource ceilings (targeted change).
    cpu_limit: str | None = None  # e.g. "500m"
    mem_limit: str | None = None  # e.g. "512Mi"
    container: str | None = None  # which container; None -> first/only
    # rollback_to_revision: the Deployment revision to roll back to.
    revision: int | None = None
    # patch_probe: adjust a liveness/readiness probe's timing.
    probe: Literal["liveness", "readiness"] | None = None
    initial_delay_seconds: int | None = None
    period_seconds: int | None = None
    timeout_seconds: int | None = None  # probe timeout; NOT the remediation timeout
    failure_threshold: int | None = None


class RemediationTarget(BaseModel):
    namespace: str
    deployment: str


class RemediationPlan(BaseModel):
    target: RemediationTarget
    steps: list[RemediationStep] = Field(default_factory=list)
    rollback_steps: list[RemediationStep] = Field(default_factory=list)


class PreflightResult(BaseModel):
    passed: bool
    detail: str  # e.g. "sandbox: pod healthy in 8s" / "not rehearsed (sandbox off)"
    mode: str  # "off" | "k8s"
    sandbox_namespace: str | None = None  # the throwaway ns, for audit


class Playbook(BaseModel):
    id: str
    name: str
    match_rule: str
    steps: list[RemediationStep] = Field(default_factory=list)
    hitl_mode: HitlMode
    reversible: bool = False
    rollback_steps: list[RemediationStep] = Field(default_factory=list)
    symptoms: str | None = None  # human-written "when this applies" — the semantic match target


class ProposedPlaybookStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"


class ProposedPlaybook(BaseModel):
    id: str  # server-assigned
    playbook: Playbook  # the typed draft — steps validate via the closed Literal
    status: ProposedPlaybookStatus = ProposedPlaybookStatus.PROPOSED
    proposed_by: str
    rationale: str | None = None
    source_situation_id: str | None = None
    decided_by: str | None = None
    ts: datetime


class AuthorDecisionDisposition(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class AuthorDecisionOutcome(str, Enum):
    UNKNOWN = "unknown"
    WORKED = "worked"
    FAILED = "failed"


class AuthorDecision(BaseModel):
    """A record of one AI drafting decision — the author's own memory.

    Recorded when a runbook is drafted (disposition="pending", outcome="unknown");
    disposition is updated on human approve/reject; outcome is updated when the
    approved runbook runs. `get_past_decisions` reads these back so the agent
    learns from its own prior judgments. `note` is model free-text — treated as
    untrusted when replayed (surfaced as prior/unverified reasoning, never as
    instructions)."""

    signature: str
    proposal_id: str
    playbook_id: str  # the ai-<sig>-<uuid> id; links to RemediationOutcome.playbook_id
    actions: list[str] = Field(default_factory=list)
    cited_facts: list[str] = Field(default_factory=list)
    note: str | None = None
    disposition: AuthorDecisionDisposition = AuthorDecisionDisposition.PENDING
    outcome: AuthorDecisionOutcome = AuthorDecisionOutcome.UNKNOWN
    decided_by: str | None = None
    ts: datetime


class ApprovalRequest(BaseModel):
    id: str
    situation_id: str
    playbook_id: str
    requested_by: str
    status: str = "pending"
    decided_by: str | None = None
    preflight: PreflightResult | None = None


class RemediationOutcome(BaseModel):
    situation_id: str
    playbook_id: str
    result: RemediationResult
    health_after: str
    ts: datetime
    hitl_mode: HitlMode = HitlMode.HITL
    steps: list[str] = Field(default_factory=list)
    mode: str = "dry_run"  # "dry_run" | "k8s"
    preflight: PreflightResult | None = None


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


class TraceStepKind(str, Enum):
    """The kind of step in a trace of the AI runbook author's reasoning."""

    MODEL_TURN = "model_turn"
    TOOL_CALL = "tool_call"
    SUBMIT = "submit"
    OUTCOME = "outcome"


class TraceStep(BaseModel):
    """A single step in the trace of the AI runbook author's activity.

    Records each model reasoning turn, tool call, submission, and outcome
    in monotonic sequence. text (model reasoning) is truncated to ~4000 chars."""

    run_id: str
    seq: int  # monotonically increasing from 0
    kind: TraceStepKind
    ts: datetime
    text: str | None = None  # for model_turn: the reasoning
    tool: str | None = None  # for tool_call: the tool name
    arguments: dict | None = None  # for tool_call: the arguments
    result_summary: str | None = None  # for tool_call: the result
    detail: dict | None = (
        None  # for submit: the draft detail; for outcome: {"status": str, "proposal_id": str|None}
    )


class RunSummary(BaseModel):
    """Summary of a completed run of the AI runbook author agent."""

    run_id: str
    started_at: datetime
    status: str  # "succeeded" | "failed" | "gave_up"
    signature: str
    step_count: int
    proposal_id: str | None = None


_TRACE_TEXT_CAP = 4000
