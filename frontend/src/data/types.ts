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
