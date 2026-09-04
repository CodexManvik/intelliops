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
  baselines: [
    { metric_name: "cpu_pct", mean: 42.3, std: 9.1, count: 1840 },
    { metric_name: "error_rate", mean: 0.012, std: 0.006, count: 1840 },
    { metric_name: "latency_ms", mean: 118.4, std: 24.7, count: 1840 },
    { metric_name: "mem_pct", mean: 61.8, std: 7.4, count: 1840 },
  ],
};

export const situations: Situation[] = [
  {
    id: "sit-9abe6de2",
    signature: "9abe6de2",
    service: "web",
    title: "CPU saturation after deploy · web",
    status: "diagnosed",
    severity: "high",
    memberCount: 214,
    first_seen: mins(4),
    hypotheses: [
      { description: "Recent deployment of web (v2.14.0) preceded the incident", confidence: 0.8, suggested_runbook_id: "rollback-deploy" },
      { description: "Resource saturation across the affected service", confidence: 0.6, suggested_runbook_id: "scale-service" },
    ],
    suggested_runbook_id: "rollback-deploy",
    hitl_mode: "hitl",
    reversible: true,
    reliability: 0.67,
    suppressed: false,
  },
  {
    id: "sit-3f81ac04",
    signature: "3f81ac04",
    service: "checkout-api",
    title: "Error-rate spike · checkout-api",
    status: "acting",
    severity: "critical",
    memberCount: 96,
    first_seen: mins(11),
    hypotheses: [
      { description: "Error spike in service logs", confidence: 0.5, suggested_runbook_id: "restart-pod" },
    ],
    suggested_runbook_id: "restart-pod",
    hitl_mode: "auto",
    reversible: true,
    reliability: 0.83,
    suppressed: false,
  },
  {
    id: "sit-c72d10b9",
    signature: "c72d10b9",
    service: "payments",
    title: "Memory pressure · payments-worker",
    status: "detected",
    severity: "medium",
    memberCount: 41,
    first_seen: mins(2),
    hypotheses: [
      { description: "Resource saturation across the affected service, no rule matched confidently", confidence: 0.4, suggested_runbook_id: null },
    ],
    suggested_runbook_id: null, // the gap: RCA has no matching playbook — a human can draft one with AI
    hitl_mode: "hitl",
    reversible: true,
    reliability: 0.5,
    suppressed: false,
  },
];

export const outcomes: OutcomeRow[] = [
  { situation_id: "sit-9abe6de2", playbook_id: "rollback-deploy", result: "success", reason: "healthy", ts: mins(18), service: "web" },
  { situation_id: "sit-9abe6de2", playbook_id: "rollback-deploy", result: "success", reason: "healthy", ts: mins(52), service: "web" },
  { situation_id: "sit-3f81ac04", playbook_id: "restart-pod", result: "success", reason: "healthy", ts: mins(9), service: "checkout-api" },
  { situation_id: "sit-77a0f2e1", playbook_id: "scale-service", result: "rolled_back", reason: "unhealthy:rolled-back", ts: mins(74), service: "search" },
  { situation_id: "sit-2b44c9d0", playbook_id: "restart-pod", result: "failure", reason: "denied:rbac", ts: mins(96), service: "auth" },
  { situation_id: "sit-91e7bb3c", playbook_id: "scale-service", result: "success", reason: "healthy", ts: mins(120), service: "checkout-api" },
  { situation_id: "sit-6cd8a4f2", playbook_id: "rollback-deploy", result: "failure", reason: "aborted:timeout", ts: mins(140), service: "web" },
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
