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
}

export interface SituationOutcome {
  result: RemediationResult;
  health_after: OutcomeReason;
  mode: "dry_run" | "k8s";
  steps: string[];
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
