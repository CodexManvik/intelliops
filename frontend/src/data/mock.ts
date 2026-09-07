import type {
  AuditRow,
  BaselineInfo,
  Metrics,
  OutcomeRow,
  Playbook,
  ProposedPlaybook,
  ServiceHealth,
  Situation,
  SystemInfo,
} from "./types";

/**
 * Self-contained mock data source. Every value is accurate to the shipped
 * system: real service ports, the real playbook ids and ranking confidences
 * (0.8 / 0.6 / 0.5), the 0.8 suppression threshold, the 3-success graduation
 * rule, and the real health_after vocabulary. Swap this module for a real
 * `fetch`-based client and the UI is unchanged.
 */

const now = Date.now();
const mins = (m: number) => now - m * 60_000;

export const services: ServiceHealth[] = [
  { name: "ingestion", port: 8001, role: "normalize · dedup", status: "ok", throughput: 1180 },
  { name: "correlation", port: 8002, role: "detect · cluster → Situation", status: "ok", throughput: 940 },
  { name: "rca", port: 8003, role: "enrich · rank · runbook", status: "ok", throughput: 62 },
  { name: "action", port: 8004, role: "approve · execute · rollback", status: "ok", throughput: 18 },
  { name: "governance", port: 8005, role: "RBAC · audit · playbooks", status: "ok", throughput: 210 },
  { name: "feedback", port: 8006, role: "label · retrain · metrics", status: "ok", throughput: 40 },
];

export const metrics: Metrics = {
  alertsIngested: 8420,
  situationsOpen: 3,
  noiseReductionPct: 91,
  mttrMinutes: 6.4,
  autoRemediatedPct: 38,
  suppressedToday: 27,
  approvalsPending: 1,
  successRate: 0.94,
};

export const system: SystemInfo = {
  correlator_kind: "river",
  bus_backend: "redis",
  store_backend: "file",
  remediator_mode: "dry_run",
  auth_mode: "off",
  llm: { provider: "template", endpoint_configured: false, endpoint: "", model: "gpt-4o-mini", last_probe: null },
};

export const baseline: BaselineInfo = {
  correlator_kind: "river",
  // The real USE+RED metric surface (Phase 1) with the per-metric z-score
  // baselines the correlator learns. Names match what the services emit.
  baselines: [
    { metric_name: "cpu_usage", mean: 42.3, std: 9.1, count: 1840 },
    { metric_name: "memory_usage_mb", mean: 512.0, std: 48.0, count: 1840 },
    { metric_name: "meridian_error_rate", mean: 0.012, std: 0.006, count: 1840 },
    { metric_name: "latency_p99_ms", mean: 118.4, std: 24.7, count: 1840 },
    { metric_name: "latency_p50_ms", mean: 41.2, std: 8.9, count: 1840 },
    { metric_name: "queue_depth", mean: 6.5, std: 3.1, count: 1840 },
    { metric_name: "db_pool_in_use", mean: 8.4, std: 2.7, count: 1840 },
    { metric_name: "request_rate", mean: 1240.0, std: 210.0, count: 1840 },
  ],
};

// Each situation is a typed fault profile (Phase 1) carrying its real firing
// metrics (member_events, with the Phase-2 detection kind that judged each) and
// the correlator's baseline snapshot, so the drill-down shows what actually
// broke and how. Hypotheses carry the real Phase-3 confidences and, where an
// embedding selector would be enabled, `confidence_source: "embedding"` — the
// AI-computed (symptom-fit) confidence. In live k8s mode with
// RUNBOOK_SELECTOR_MODE=embedding this is genuine; here it is illustrative of
// that feature (mock has no model), the same way the LLM is shown as template.
export const situations: Situation[] = [
  {
    // dependency_outage: error_rate ↑ + latency_p99 ↑, cpu flat. The load-bearing
    // multi-metric case — restart wins over scale because a failing dependency is
    // fixed by recycling, not capacity (Phase 3 collision fix: 0.58 > 0.55).
    id: "sit-3f81ac04",
    signature: "3f81ac04",
    service: "checkout-api",
    title: "Dependency outage · checkout-api",
    status: "diagnosed",
    severity: "critical",
    memberCount: 132,
    first_seen: mins(6),
    member_events: [
      { name: "meridian_error_rate", value: 0.087, labels: { service: "checkout-api" }, kind: "metric", ts: mins(6), kind_detected: "ratio" },
      { name: "latency_p99_ms", value: 612.0, labels: { service: "checkout-api" }, kind: "metric", ts: mins(6), kind_detected: "latency" },
      { name: "cpu_usage", value: 44.0, labels: { service: "checkout-api" }, kind: "metric", ts: mins(6), kind_detected: "saturation" },
    ],
    baseline: {
      meridian_error_rate: { mean: 0.012, std: 0.006 },
      latency_p99_ms: { mean: 118.4, std: 24.7 },
      cpu_usage: { mean: 42.3, std: 9.1 },
    },
    peak_score: 5.1,
    hypotheses: [
      { description: "Failing upstream dependency — error rate and latency both breached, CPU flat", confidence: 0.58, suggested_runbook_id: "restart-pod", confidence_source: "embedding", evidence: ["metrics: meridian_error_rate latency_p99_ms cpu_usage"] },
      { description: "Latency/queueing under load — capacity contention", confidence: 0.55, suggested_runbook_id: "scale-service", confidence_source: "rule", evidence: ["metrics: latency_p99_ms"] },
    ],
    suggested_runbook_id: "restart-pod",
    hitl_mode: "hitl",
    reversible: true,
    reliability: 0.71,
    suppressed: false,
  },
  {
    // db_exhaustion: db_pool_in_use → max + latency ↑. → restart-pod (0.62)
    // to recycle wedged connections. Resolved + verified per-metric (Phase 4).
    id: "sit-b7e4a190",
    signature: "b7e4a190",
    service: "payments",
    title: "DB connection-pool exhaustion · payments",
    status: "resolved",
    severity: "high",
    memberCount: 68,
    first_seen: mins(23),
    member_events: [
      { name: "db_pool_in_use", value: 20.0, labels: { service: "payments" }, kind: "metric", ts: mins(23), kind_detected: "default" },
      { name: "latency_p99_ms", value: 540.0, labels: { service: "payments" }, kind: "metric", ts: mins(23), kind_detected: "latency" },
    ],
    baseline: {
      db_pool_in_use: { mean: 8.4, std: 2.7 },
      latency_p99_ms: { mean: 118.4, std: 24.7 },
    },
    peak_score: 4.3,
    hypotheses: [
      { description: "Database connection-pool exhaustion — recycle connections", confidence: 0.62, suggested_runbook_id: "restart-pod", confidence_source: "embedding", evidence: ["metrics: db_pool_in_use latency_p99_ms"] },
      { description: "Latency/queueing under load — capacity contention", confidence: 0.55, suggested_runbook_id: "scale-service", confidence_source: "rule" },
    ],
    suggested_runbook_id: "restart-pod",
    hitl_mode: "auto",
    reversible: true,
    reliability: 0.86,
    suppressed: false,
    outcome: {
      result: "success",
      health_after: "healthy",
      mode: "dry_run",
      steps: ["restart"],
      preflight: { passed: true, detail: "sandbox: clone healthy in 7s", mode: "k8s" },
    },
  },
  {
    // memory_leak: memory_usage_mb ramping, cpu flat. → restart-pod (0.65) — a
    // leak is fixed by recycling; scaling spins up pods that also leak. The
    // headline Phase-3 correction (memory no longer routes to scale).
    id: "sit-9abe6de2",
    signature: "9abe6de2",
    service: "web",
    title: "Memory leak trending to OOM · web",
    status: "acting",
    severity: "high",
    memberCount: 74,
    first_seen: mins(3),
    member_events: [
      { name: "memory_usage_mb", value: 928.0, labels: { service: "web" }, kind: "metric", ts: mins(3), kind_detected: "default" },
      { name: "cpu_usage", value: 39.0, labels: { service: "web" }, kind: "metric", ts: mins(3), kind_detected: "saturation" },
    ],
    baseline: {
      memory_usage_mb: { mean: 512.0, std: 48.0 },
      cpu_usage: { mean: 42.3, std: 9.1 },
    },
    peak_score: 8.7,
    hypotheses: [
      { description: "Memory pressure / leak — recycle the process", confidence: 0.65, suggested_runbook_id: "restart-pod", confidence_source: "embedding", evidence: ["metrics: memory_usage_mb cpu_usage"] },
    ],
    suggested_runbook_id: "restart-pod",
    hitl_mode: "hitl",
    reversible: true,
    reliability: 0.67,
    suppressed: false,
  },
  {
    // traffic_surge: request_rate + cpu + queue_depth all ↑. → scale-service
    // (0.60) — genuine capacity shortfall. Recent-deploy context would outrank
    // this (0.80→rollback); here there's no deploy, so scale wins.
    id: "sit-51c8de77",
    signature: "51c8de77",
    service: "search",
    title: "Traffic surge · search",
    status: "detected",
    severity: "medium",
    memberCount: 203,
    first_seen: mins(1),
    member_events: [
      { name: "request_rate", value: 4820.0, labels: { service: "search" }, kind: "metric", ts: mins(1), kind_detected: "default" },
      { name: "cpu_usage", value: 93.0, labels: { service: "search" }, kind: "metric", ts: mins(1), kind_detected: "saturation" },
      { name: "queue_depth", value: 41.0, labels: { service: "search" }, kind: "metric", ts: mins(1), kind_detected: "default" },
    ],
    baseline: {
      request_rate: { mean: 1240.0, std: 210.0 },
      cpu_usage: { mean: 42.3, std: 9.1 },
      queue_depth: { mean: 6.5, std: 3.1 },
    },
    peak_score: 6.4,
    hypotheses: [
      { description: "Resource saturation across the affected service", confidence: 0.6, suggested_runbook_id: "scale-service", confidence_source: "rule", evidence: ["metrics: request_rate cpu_usage queue_depth"] },
      { description: "Latency/queueing under load — capacity contention", confidence: 0.55, suggested_runbook_id: "scale-service", confidence_source: "rule" },
    ],
    suggested_runbook_id: "scale-service",
    hitl_mode: "hitl",
    reversible: true,
    reliability: 0.58,
    suppressed: false,
  },
];

// Outcomes reflect Phase-4 per-metric verification: `reason` (health_after) is
// judged on the metric that fired, not cpu. A `rolled_back` means the firing
// metric was still anomalous after the fix → rolled back (fail-safe).
export const outcomes: OutcomeRow[] = [
  { situation_id: "sit-b7e4a190", playbook_id: "restart-pod", result: "success", reason: "healthy", ts: mins(21), service: "payments" },
  { situation_id: "sit-a1f0c3d2", playbook_id: "restart-pod", result: "success", reason: "healthy", ts: mins(38), service: "checkout-api" },
  { situation_id: "sit-77a0f2e1", playbook_id: "scale-service", result: "rolled_back", reason: "unhealthy:rolled-back", ts: mins(74), service: "search" },
  { situation_id: "sit-2b44c9d0", playbook_id: "restart-pod", result: "failure", reason: "denied:rbac", ts: mins(96), service: "auth" },
  { situation_id: "sit-91e7bb3c", playbook_id: "scale-service", result: "success", reason: "healthy", ts: mins(120), service: "search" },
  { situation_id: "sit-6cd8a4f2", playbook_id: "restart-pod", result: "success", reason: "healthy", ts: mins(140), service: "web" },
  { situation_id: "sit-2b44c9d0", playbook_id: "rollback-deploy", result: "failure", reason: "aborted:timeout", ts: mins(165), service: "web" },
];

export const audit: AuditRow[] = [
  { actor: "action-service", action: "execute", resource: "playbook:restart-pod", decision: "allow", ts: mins(9), correlation_id: "sit-3f81ac04" },
  { actor: "oncall-alice", action: "approve", resource: "playbook:rollback-deploy", decision: "allow", ts: mins(17), correlation_id: "sit-9abe6de2" },
  { actor: "action-service", action: "execute", resource: "playbook:rollback-deploy", decision: "allow", ts: mins(18), correlation_id: "sit-9abe6de2" },
  { actor: "feedback-service", action: "graduate", resource: "playbook:restart-pod", decision: "allow", ts: mins(30), correlation_id: "playbook:restart-pod" },
  { actor: "rca-service", action: "diagnose", resource: "situation:sit-c72d10b9", decision: "allow", ts: mins(2), correlation_id: "sit-c72d10b9" },
  { actor: "action-service", action: "execute", resource: "playbook:restart-pod", decision: "deny", ts: mins(96), correlation_id: "sit-2b44c9d0" },
];

export const playbooks: Playbook[] = [
  { id: "restart-pod", name: "Restart Pod", hitl_mode: "auto", reversible: true, successes: 12, rollbacks: 0, failures: 0, graduated: true },
  { id: "rollback-deploy", name: "Rollback Deployment", hitl_mode: "hitl", reversible: true, successes: 2, rollbacks: 0, failures: 1, graduated: false },
  { id: "scale-service", name: "Scale Service Horizontally", hitl_mode: "hitl", reversible: true, successes: 4, rollbacks: 1, failures: 0, graduated: false },
];

/**
 * A demo value — this is what an AI-drafted proposal looks like before a
 * human reviews it, not a claim that an LLM actually ran. In `live` mode
 * this list is real (whatever `runbook-author` drafted); this seed exists
 * so the queue renders without a configured LLM endpoint.
 */
export const proposals: ProposedPlaybook[] = [
  {
    id: "prop-demo0001",
    playbook: {
      id: "ai-c72d10b9-a1b2c3",
      name: "Raise memory ceiling · payments-worker",
      match_rule: "sit-c72d10b9",
      steps: [
        { action: "patch_resource_limits", cpu_limit: "500m", mem_limit: "768Mi", container: "payments-worker" },
        { action: "wait", note: "settle after limits patch" },
      ],
      hitl_mode: "hitl",
      reversible: true,
      rollback_steps: [{ action: "patch_resource_limits", cpu_limit: "250m", mem_limit: "512Mi", container: "payments-worker" }],
    },
    status: "proposed",
    proposed_by: "runbook-author",
    rationale:
      "Memory pressure on payments-worker matches an OOM-adjacent pattern seen before; raising the memory ceiling is reversible and sandbox-rehearsable, so it drafts as hitl rather than auto.",
    source_situation_id: "sit-c72d10b9",
    decided_by: null,
    ts: mins(1),
  },
];

/** A sparkline series for the noise-reduction / MTTR cards. */
export function series(n: number, base: number, drift: number, seed = 7): number[] {
  let s = seed;
  const rnd = () => {
    s = (s * 1103515245 + 12345) & 0x7fffffff;
    return s / 0x7fffffff;
  };
  return Array.from({ length: n }, (_, i) => Math.max(0, base + drift * i + (rnd() - 0.5) * base * 0.28));
}
