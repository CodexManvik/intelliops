/**
 * Domain types — mirror the shipped IntelliOps contracts (common/contracts.py).
 * A real API client would return exactly these shapes.
 */

export type SituationStatus =
  | "detected"
  | "diagnosed"
  | "acting"
  | "resolved"
  | "failed"
  | "suppressed";

export type HitlMode = "auto" | "hitl" | "disabled";

export type RemediationResult = "success" | "failure" | "rolled_back";

/** The exact health_after vocabulary the action service emits. */
export type OutcomeReason =
  | "healthy"
  | "unhealthy:rolled-back"
  | "execute-failed"
  | "denied:rbac"
  | "refused:not-reversible"
  | "aborted:rejected"
  | "aborted:timeout"
  | "skipped:disabled"
  | "skipped:no-playbook";

export type Severity = "critical" | "high" | "medium" | "low";

export interface Hypothesis {
  description: string;
  confidence: number; // 0..1
  suggested_runbook_id: string | null;
  evidence?: string[];
  explanation?: string | null;
  explanation_source?: string | null; // "llm" | "template"
}

export interface SituationOutcome {
  result: RemediationResult;
  health_after: OutcomeReason;
  mode: "dry_run" | "k8s";
  steps: string[];
  preflight?: {
    passed: boolean;
    detail: string;
    mode: "off" | "k8s";
    sandbox_namespace?: string | null;
  } | null;
}

export interface Situation {
  id: string; // "sit-" + signature
  signature: string;
  service: string;
  title: string;
  status: SituationStatus;
  severity: Severity;
  memberCount: number; // alerts collapsed into this Situation
  first_seen: number; // epoch ms
  hypotheses: Hypothesis[];
  suggested_runbook_id: string | null;
  hitl_mode: HitlMode;
  reversible: boolean;
  reliability: number; // per-signature reliability (0..1)
  suppressed: boolean;
  outcome?: SituationOutcome; // present once remediation has produced a result
  peak_score?: number | null;
  baseline?: Record<string, { mean: number; std: number }> | null;
  member_events?: MemberEvent[];
  stages?: Partial<Record<"detected" | "diagnosed" | "acting" | "resolved" | "failed", number>>;
}

export interface OutcomeRow {
  situation_id: string;
  playbook_id: string;
  result: RemediationResult;
  reason: OutcomeReason;
  ts: number;
  service: string;
}

export interface AuditRow {
  actor: string;
  action: string;
  resource: string;
  decision: "allow" | "deny" | "pending";
  ts: number;
  correlation_id: string;
}

export interface Playbook {
  id: string;
  name: string;
  hitl_mode: HitlMode;
  reversible: boolean;
  successes: number;
  rollbacks: number;
  failures: number;
  graduated: boolean;
}

export interface ServiceHealth {
  name: string;
  port: number;
  role: string;
  status: "ok" | "degraded" | "down";
  throughput: number; // events/min
}

export interface Metrics {
  alertsIngested: number;
  situationsOpen: number;
  noiseReductionPct: number;
  mttrMinutes: number;
  autoRemediatedPct: number;
  suppressedToday: number;
  approvalsPending: number;
  successRate: number; // 0..1
}

export interface MemberEvent {
  name: string;
  value: number | null;
  labels: Record<string, string>;
  kind: string;
  ts: number;
}

export interface SystemInfo {
  correlator_kind: string;
  bus_backend: string;
  store_backend: string;
  remediator_mode: string;
  auth_mode: string;
  llm: {
    provider: "template" | "openai-compatible";
    endpoint_configured: boolean;
    endpoint: string;
    model: string;
    last_probe?: { ok: boolean; latency_ms?: number; error?: string } | null;
  };
}

export interface BaselineInfo {
  correlator_kind: string;
  baselines: { metric_name: string; mean: number; std: number; count: number }[];
}

export interface LlmProbe {
  ok: boolean;
  model?: string;
  latency_ms?: number;
  error?: string;
}

/** One step of a playbook's remediation plan (common/contracts.py RemediationStep). */
export interface RemediationStep {
  action:
    | "restart"
    | "scale"
    | "rollback_deploy"
    | "wait"
    | "patch_resource_limits"
    | "rollback_to_revision"
    | "patch_probe";
  replicas?: number | null;
  note?: string | null;
  cpu_limit?: string | null;
  mem_limit?: string | null;
  container?: string | null;
  revision?: number | null;
  probe?: "liveness" | "readiness" | null;
  initial_delay_seconds?: number | null;
}

/** The full playbook shape a proposal drafts (common/contracts.py Playbook). */
export interface DraftedPlaybook {
  id: string;
  name: string;
  match_rule: string;
  steps: RemediationStep[];
  hitl_mode: HitlMode;
  reversible: boolean;
  rollback_steps: RemediationStep[];
}

export type ProposedPlaybookStatus = "proposed" | "approved" | "rejected";

export interface ProposedPlaybook {
  id: string;
  playbook: DraftedPlaybook;
  status: ProposedPlaybookStatus;
  proposed_by: string;
  rationale?: string | null;
  source_situation_id?: string | null;
  decided_by?: string | null;
  ts: number | string;
}
