//! The wire models. Unknown fields are ignored and additive fields default.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::Timestamp;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TelemetryKind {
    Metric,
    Log,
    Trace,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SituationStatus {
    Detected,
    Diagnosed,
    Acting,
    Resolved,
    Failed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HitlMode {
    Auto,
    #[default]
    Hitl,
    Disabled,
}

/// `Escalated` means no remediation was attempted because the system had no
/// candidate fix. It is never evidence about a runbook.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RemediationResult {
    Success,
    Failure,
    RolledBack,
    Escalated,
}

/// Whether a situation pages a human or is handled quietly. A request, not a
/// permission: action re-checks the evidence before skipping approval.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Handling {
    #[default]
    Normal,
    Quiet,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TelemetryEvent {
    pub source: String,
    pub kind: TelemetryKind,
    pub name: String,
    #[serde(default)]
    pub value: Option<f64>,
    #[serde(default)]
    pub payload: Option<Map<String, Value>>,
    #[serde(default)]
    pub labels: BTreeMap<String, String>,
    pub ts: Timestamp,
    pub fingerprint: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Situation {
    pub id: String,
    pub status: SituationStatus,
    #[serde(default)]
    pub member_events: Vec<TelemetryEvent>,
    pub severity: String,
    pub first_seen: Timestamp,
    pub last_seen: Timestamp,
    pub signature: String,
    #[serde(default)]
    pub peak_score: Option<f64>,
    #[serde(default)]
    pub baseline: Option<Map<String, Value>>,
    #[serde(default)]
    pub handling: Handling,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RootCauseHypothesis {
    pub situation_id: String,
    pub description: String,
    pub confidence: f64,
    #[serde(default)]
    pub evidence: Vec<String>,
    #[serde(default)]
    pub suggested_runbook_id: Option<String>,
    #[serde(default)]
    pub explanation: Option<String>,
    #[serde(default)]
    pub explanation_source: Option<String>,
    #[serde(default)]
    pub confidence_source: Option<String>,
}

/// The closed remediation vocabulary. This is the core safety property: neither
/// a person nor a model can express an action that is not listed here.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Action {
    Restart,
    Scale,
    RollbackDeploy,
    Wait,
    PatchResourceLimits,
    RollbackToRevision,
    PatchProbe,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProbeKind {
    Liveness,
    Readiness,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RemediationStep {
    pub action: Action,
    /// For `scale`: a delta such as +2 or -2.
    #[serde(default)]
    pub replicas: Option<i64>,
    #[serde(default)]
    pub note: Option<String>,
    #[serde(default)]
    pub cpu_limit: Option<String>,
    #[serde(default)]
    pub mem_limit: Option<String>,
    #[serde(default)]
    pub container: Option<String>,
    #[serde(default)]
    pub revision: Option<i64>,
    #[serde(default)]
    pub probe: Option<ProbeKind>,
    #[serde(default)]
    pub initial_delay_seconds: Option<i64>,
    #[serde(default)]
    pub period_seconds: Option<i64>,
    #[serde(default)]
    pub timeout_seconds: Option<i64>,
    #[serde(default)]
    pub failure_threshold: Option<i64>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RemediationTarget {
    pub namespace: String,
    pub deployment: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RemediationPlan {
    pub target: RemediationTarget,
    #[serde(default)]
    pub steps: Vec<RemediationStep>,
    #[serde(default)]
    pub rollback_steps: Vec<RemediationStep>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PreflightResult {
    pub passed: bool,
    pub detail: String,
    pub mode: String,
    #[serde(default)]
    pub sandbox_namespace: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Playbook {
    pub id: String,
    pub name: String,
    pub match_rule: String,
    #[serde(default)]
    pub steps: Vec<RemediationStep>,
    pub hitl_mode: HitlMode,
    #[serde(default)]
    pub reversible: bool,
    #[serde(default)]
    pub rollback_steps: Vec<RemediationStep>,
    #[serde(default)]
    pub symptoms: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProposalStatus {
    #[default]
    Proposed,
    Approved,
    Rejected,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ProposedPlaybook {
    pub id: String,
    pub playbook: Playbook,
    #[serde(default)]
    pub status: ProposalStatus,
    pub proposed_by: String,
    #[serde(default)]
    pub rationale: Option<String>,
    #[serde(default)]
    pub source_situation_id: Option<String>,
    #[serde(default)]
    pub decided_by: Option<String>,
    pub ts: Timestamp,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Disposition {
    #[default]
    Pending,
    Accepted,
    Rejected,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DecisionOutcome {
    #[default]
    Unknown,
    Worked,
    Failed,
}

/// The runbook author's memory of one drafting decision. `note` is model free
/// text and is untrusted when replayed.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AuthorDecision {
    pub signature: String,
    pub proposal_id: String,
    pub playbook_id: String,
    #[serde(default)]
    pub actions: Vec<String>,
    #[serde(default)]
    pub cited_facts: Vec<String>,
    #[serde(default)]
    pub note: Option<String>,
    #[serde(default)]
    pub disposition: Disposition,
    #[serde(default)]
    pub outcome: DecisionOutcome,
    #[serde(default)]
    pub decided_by: Option<String>,
    pub ts: Timestamp,
}

fn pending() -> String {
    "pending".to_string()
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ApprovalRequest {
    pub id: String,
    pub situation_id: String,
    pub playbook_id: String,
    pub requested_by: String,
    /// "pending" | "approved" | "rejected" | "expired"; free text in stored rows.
    #[serde(default = "pending")]
    pub status: String,
    #[serde(default)]
    pub decided_by: Option<String>,
    #[serde(default)]
    pub preflight: Option<PreflightResult>,
}

fn dry_run() -> String {
    "dry_run".to_string()
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RemediationOutcome {
    pub situation_id: String,
    pub playbook_id: String,
    pub result: RemediationResult,
    pub health_after: String,
    pub ts: Timestamp,
    #[serde(default)]
    pub hitl_mode: HitlMode,
    #[serde(default)]
    pub steps: Vec<String>,
    /// "dry_run" | "k8s" | "none" (escalated: no executor ran).
    #[serde(default = "dry_run")]
    pub mode: String,
    #[serde(default)]
    pub preflight: Option<PreflightResult>,
    #[serde(default)]
    pub handling: Handling,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AuditRecord {
    pub actor: String,
    pub action: String,
    pub resource: String,
    pub decision: String,
    pub ts: Timestamp,
    pub correlation_id: String,
}

#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize)]
pub struct EnrichmentContext {
    #[serde(default)]
    pub recent_deploys: Vec<Map<String, Value>>,
    #[serde(default)]
    pub topology: Map<String, Value>,
    #[serde(default)]
    pub config_changes: Vec<Map<String, Value>>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DiagnosedSituation {
    pub situation: Situation,
    #[serde(default)]
    pub hypotheses: Vec<RootCauseHypothesis>,
    #[serde(default)]
    pub suggested_runbook_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TrainingRecord {
    pub situation_id: String,
    pub signature: String,
    pub playbook_id: String,
    pub result: RemediationResult,
    pub worked: bool,
    pub ts: Timestamp,
    /// How the remediation ran; records written before this existed have none.
    #[serde(default)]
    pub mode: Option<String>,
}

impl TrainingRecord {
    /// A dry run "always works", so it is no evidence about a real cluster.
    #[must_use]
    pub fn is_real(&self) -> bool {
        !matches!(self.mode.as_deref(), None | Some("dry_run" | "none"))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TraceStepKind {
    ModelTurn,
    ToolCall,
    Submit,
    Outcome,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TraceStep {
    pub run_id: String,
    pub seq: i64,
    pub kind: TraceStepKind,
    pub ts: Timestamp,
    #[serde(default)]
    pub text: Option<String>,
    #[serde(default)]
    pub tool: Option<String>,
    #[serde(default)]
    pub arguments: Option<Map<String, Value>>,
    #[serde(default)]
    pub result_summary: Option<String>,
    #[serde(default)]
    pub detail: Option<Map<String, Value>>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RunSummary {
    pub run_id: String,
    pub started_at: Timestamp,
    /// "succeeded" | "failed" | "gave_up"
    pub status: String,
    pub signature: String,
    pub step_count: i64,
    #[serde(default)]
    pub proposal_id: Option<String>,
}
