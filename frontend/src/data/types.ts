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
  // terminal, but NOT a failure: nothing was executed because there was no
  // candidate fix — a human has to look.
  | "needs_attention"
  | "suppressed";

export type HitlMode = "auto" | "hitl" | "disabled";

export type RemediationResult = "success" | "failure" | "rolled_back" | "escalated";

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
  | "skipped:no-playbook"
  | "escalated:no-diagnosis"
  | "escalated:unknown-runbook";

export type Severity = "critical" | "high" | "medium" | "low";

export interface Hypothesis {
  description: string;
  confidence: number; // 0..1
  suggested_runbook_id: string | null;
  evidence?: string[];
  explanation?: string | null;
  explanation_source?: string | null; // "llm" | "template"
  confidence_source?: "embedding" | "rule" | null; // how `confidence` was computed (Phase 3)
}

export interface SituationOutcome {
  result: RemediationResult;
  health_after: OutcomeReason;
  // "none" is the escalation case — no executor ran at all.
  mode: "dry_run" | "k8s" | "none";
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
  stages?: Partial<
    Record<"detected" | "diagnosed" | "acting" | "resolved" | "failed" | "needs_attention", number>
  >;
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
  decision: "allow" | "deny" | "pending" | "escalated";
  ts: number;
  correlation_id: string;
}

export interface Playbook {
  id: string;
  name: string;
  hitl_mode: HitlMode;
  reversible: boolean;
  symptoms?: string | null;
  // GET /playbooks does not serve a track record. These were declared required,
  // so every consumer read `undefined` and rendered 0 forever - the "graduated
  // playbooks" tile contradicted the copy directly above it. Optional now, and
  // graduation is derived from hitl_mode instead (see isGraduated).
  successes?: number;
  rollbacks?: number;
  failures?: number;
}

/** Graduation IS hitl -> auto, and hitl_mode is served, so this needs no new API. */
export const isGraduated = (p: Playbook): boolean => p.hitl_mode === "auto";

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
  needsAttention: number; // escalations awaiting a human — excluded from successRate
}

export interface MemberEvent {
  name: string;
  value: number | null;
  labels: Record<string, string>;
  kind: string;
  ts: number;
  // Detection-policy classification (Phase 2) — which anomaly rule judged this
  // metric: absolute ratio / scale-aware saturation / latency ceiling-or-z /
  // plain z-score. A display hint the read model may carry; safe when absent.
  kind_detected?: "ratio" | "saturation" | "latency" | "default";
}

export interface SystemInfo {
  correlator_kind: string;
  bus_backend: string;
  store_backend: string;
  remediator_mode: string;
  auth_mode: string;
  // Reported by read-service. The console used to assert "fixes rehearsed on a
  // throwaway clone first" as a fixed line of copy, which is a claim about a
  // setting that is off in the live posture. Read it instead of stating it.
  sandbox_mode?: string;
  health_check_mode?: string;
  detection_policy?: string;
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
  // Which statistic the running correlator actually computes. `river` keeps a
  // running mean/stddev; `robust` a median and a MAD-derived sigma. Labelling
  // both "mean" was wrong on the page that promises nothing is staged.
  statistic?: "median/MAD" | "mean/stddev";
  // mean/count are null for the robust correlator (median/MAD per hour-bucket,
  // no running mean); std may be 0 before enough samples. Guard before formatting.
  baselines: { metric_name: string; mean: number | null; std: number | null; count: number | null }[];
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

/**
 * Agent Activity — trace of the AI runbook author's reasoning (common/contracts.py
 * TraceStep/RunSummary). One TraceStep per model turn / tool call / submit / outcome,
 * in monotonic `seq` order for a given `run_id`.
 */
export type TraceStepKind = "model_turn" | "tool_call" | "submit" | "outcome";

export interface TraceStep {
  run_id: string;
  seq: number; // monotonically increasing from 0
  kind: TraceStepKind;
  ts: number | string;
  text?: string | null; // model_turn: the reasoning
  tool?: string | null; // tool_call: the tool name
  arguments?: Record<string, unknown> | null; // tool_call: the arguments
  result_summary?: string | null; // tool_call: the result
  detail?: Record<string, unknown> | null; // submit: draft detail; outcome: {status, proposal_id}
}

export type RunStatus = "running" | "succeeded" | "failed" | "gave_up";

export interface RunSummary {
  run_id: string;
  started_at: number | string;
  status: string; // "running" | "succeeded" | "failed" | "gave_up"
  signature: string;
  step_count: number;
  proposal_id?: string | null;
}

/** A real time-series from GET /metrics/history (Prometheus, proxied by read). */
export interface MetricSeries {
  service: string;
  /** [unix_seconds, value] pairs, oldest first. */
  points: [number, number][];
}

export interface MetricHistory {
  metric: string;
  /** false when Prometheus could not be reached - render "no data", never a fake shape. */
  available: boolean;
  reason?: string;
  start: number;
  end: number;
  step_seconds: number;
  series: MetricSeries[];
}
