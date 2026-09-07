export type Severity = "critical" | "high" | "warning";
export type IncidentStatus = "investigating" | "identified" | "resolved";
export type AuditActionType = "alert" | "config" | "login" | "integration";
export type AuditStatus = "success" | "failed" | "pending";

export interface TimelineEvent {
  time: string;
  label: string;
  description?: string;
  type: "action" | "detected" | "notified" | "identified" | "resolved";
}

export interface MetricSeries {
  label: string;
  data: number[];
  color: string;
  unit: string;
  peak: string;
}

export interface Incident {
  id: string;
  title: string;
  service: string;
  severity: Severity;
  status: IncidentStatus;
  startTime: string;
  duration?: string;
  aiSummary: string;
  aiFindings: string[];
  timeline: TimelineEvent[];
  metrics: MetricSeries[];
}

export const INCIDENTS: Incident[] = [
  {
    id: "INC-2847",
    title: "P99 latency spike — api-gateway",
    service: "api-gateway",
    severity: "critical",
    status: "investigating",
    startTime: "2024-01-15T14:23:00Z",
    aiSummary:
      "IntelliOps detected a 4.2σ deviation in api-gateway P99 latency at 14:23 UTC, correlating with a connection pool exhaustion event on postgres-primary that began 90 seconds earlier. The root cause chain: a long-running analytics query (ID: q_8f3a91) held 22 connections for >30s, triggering pool starvation. Downstream API requests queued, causing latency to spike from an 88ms baseline to a 2,340ms peak. The deploy at 14:11 UTC (api-v2.3.1) introduced a missing index on the analytics_events table, making the query 40× slower under sustained load.",
    aiFindings: [
      "Missing index on analytics_events(user_id, created_at) introduced in api-v2.3.1",
      "Query q_8f3a91 held 22/25 connections open for 34s — pool starvation cascaded upstream",
      "Blast radius: api-gateway, postgres-primary, reporting-service, 3 dependent microservices",
      "Recommended fix: rollback api-v2.3.1 or hotfix with CREATE INDEX CONCURRENTLY",
    ],
    timeline: [
      { time: "14:11 UTC", label: "Deploy api-v2.3.1", description: "Missing index introduced on analytics_events", type: "action" },
      { time: "14:21 UTC", label: "Anomaly detected", description: "P99 latency began rising — 4.2σ from 7-day baseline", type: "detected" },
      { time: "14:23 UTC", label: "Incident opened — INC-2847", description: "IntelliOps auto-created incident", type: "notified" },
      { time: "14:24 UTC", label: "On-call notified", description: "PagerDuty alert → @sre-oncall (Maya Chen)", type: "notified" },
      { time: "14:29 UTC", label: "Root cause identified", description: "Missing index correlated to 14:11 deploy", type: "identified" },
    ],
    metrics: [
      { label: "P99 Latency", data: [88,90,91,89,92,140,310,780,1520,2340,2100,1820,1450,1200,980], color: "#EF4444", unit: "ms", peak: "2,340ms" },
      { label: "DB Connections", data: [8,9,8,10,13,17,21,23,24,24,24,23,22,20,18], color: "#F59E0B", unit: " active", peak: "24/25" },
    ],
  },
  {
    id: "INC-2841",
    title: "Connection pool exhaustion — postgres-primary",
    service: "postgres-primary",
    severity: "high",
    status: "identified",
    startTime: "2024-01-15T12:08:00Z",
    duration: "2h 14m",
    aiSummary:
      "Connection pool on postgres-primary reached 89% utilization at 12:08 UTC. IntelliOps correlated the saturation with reporting-service v1.8.2 deployed at 11:52 UTC, which inadvertently set idle_in_transaction_session_timeout=0, allowing report-generation sessions to hold connections indefinitely during large export jobs. As concurrent report jobs increased throughout the morning, the pool slowly starved — with 22/25 connections occupied by stale idle-in-transaction sessions by 12:42 UTC.",
    aiFindings: [
      "idle_in_transaction_session_timeout=0 set in reporting-service v1.8.2 config (deployed 11:52 UTC)",
      "Report jobs holding connections for up to 8 minutes each — 22/25 occupied by 12:42",
      "No query-level timeouts configured on the reporting-service pool connection string",
      "Recommended fix: set idle_in_transaction_session_timeout=30000ms — config change, no rollback required",
    ],
    timeline: [
      { time: "11:52 UTC", label: "reporting-service v1.8.2 deployed", description: "Session timeout misconfigured to 0", type: "action" },
      { time: "12:08 UTC", label: "Pool utilization exceeded 80%", description: "IntelliOps anomaly detector fired", type: "detected" },
      { time: "12:09 UTC", label: "Incident opened — INC-2841", type: "notified" },
      { time: "12:15 UTC", label: "On-call acknowledged", description: "James Park — @infra-oncall", type: "action" },
      { time: "12:42 UTC", label: "Root cause identified", description: "Session timeout config correlated to deploy", type: "identified" },
      { time: "14:22 UTC", label: "Config patch rolling out", description: "idle_in_transaction_session_timeout=30000", type: "action" },
    ],
    metrics: [
      { label: "Connection Pool %", data: [42,44,48,53,59,66,72,78,82,85,87,89,89,88,87], color: "#F59E0B", unit: "%", peak: "89%" },
      { label: "Idle-in-Txn Sessions", data: [0,1,2,4,6,9,12,15,17,19,20,21,22,22,21], color: "#EF4444", unit: "", peak: "22" },
    ],
  },
  {
    id: "INC-2835",
    title: "Cache hit rate degradation — cdn-edge",
    service: "cdn-edge",
    severity: "warning",
    status: "resolved",
    startTime: "2024-01-15T09:44:00Z",
    duration: "47m",
    aiSummary:
      "CDN edge cache hit rate dropped from 94% to 61% at 09:44 UTC following a configuration change in cdn-config v3.1.0 deployed six minutes earlier. The change modified Vary header handling for authenticated endpoints, effectively invalidating all cached responses for logged-in users and causing a 2.3× surge in origin requests. IntelliOps correlated the cache miss spike to the configuration deploy within 8 seconds of anomaly detection. No user-visible errors occurred — latency increased by ~22ms during the 47-minute window as the cache naturally repopulated.",
    aiFindings: [
      "Vary header change in cdn-config v3.1.0 invalidated ~340k cached entries for authenticated users",
      "Origin request rate: 1,200/min → 2,760/min — 2.3× increase, no origin errors",
      "Cache warmup completed within 38 minutes as traffic naturally repopulated CDN nodes",
      "No user-visible errors — peak additional latency was +22ms for authenticated requests",
    ],
    timeline: [
      { time: "09:38 UTC", label: "cdn-config v3.1.0 deployed", description: "Vary header handling changed for auth endpoints", type: "action" },
      { time: "09:44 UTC", label: "Cache hit rate drop detected", description: "94% → 61% within 3 minutes of deploy", type: "detected" },
      { time: "09:45 UTC", label: "Incident opened — INC-2835", type: "notified" },
      { time: "09:46 UTC", label: "Root cause identified", description: "8s detection-to-correlation — config deploy matched", type: "identified" },
      { time: "10:31 UTC", label: "Cache repopulated — incident resolved", description: "Hit rate back to 93%", type: "resolved" },
    ],
    metrics: [
      { label: "Cache Hit Rate", data: [94,93,94,92,78,65,61,62,65,70,75,80,85,90,93], color: "#22C55E", unit: "%", peak: "61% (trough)" },
      { label: "Origin Req / min", data: [1200,1210,1195,1220,1680,2280,2760,2640,2420,2180,1940,1680,1440,1280,1210], color: "#6366F1", unit: "", peak: "2,760/min" },
    ],
  },
];

export interface AuditEntry {
  id: string;
  timestamp: string;
  actor: string;
  actorType: "user" | "system" | "api";
  action: string;
  actionType: AuditActionType;
  target: string;
  status: AuditStatus;
  detail?: string;
}

export const AUDIT_ENTRIES: AuditEntry[] = [
  { id: "a001", timestamp: "2024-01-15 14:29:03", actor: "intelliops-ai", actorType: "system", action: "Root cause identified", actionType: "alert", target: "INC-2847 / api-gateway", status: "success", detail: "Correlated missing index on analytics_events to api-v2.3.1 deploy" },
  { id: "a002", timestamp: "2024-01-15 14:24:01", actor: "pagerduty-webhook", actorType: "api", action: "On-call notified", actionType: "alert", target: "INC-2847 / @sre-oncall", status: "success", detail: "PagerDuty alert #PA-91823 sent to Maya Chen" },
  { id: "a003", timestamp: "2024-01-15 14:23:18", actor: "intelliops-ai", actorType: "system", action: "Incident auto-created", actionType: "alert", target: "INC-2847", status: "success", detail: "P99 latency 4.2σ above baseline triggered auto-incident" },
  { id: "a004", timestamp: "2024-01-15 14:11:42", actor: "maya.chen@acme.com", actorType: "user", action: "Deploy triggered", actionType: "config", target: "api-gateway / api-v2.3.1", status: "success", detail: "Production deploy via GitHub Actions run #4821" },
  { id: "a005", timestamp: "2024-01-15 13:50:00", actor: "maya.chen@acme.com", actorType: "user", action: "Alert rule modified", actionType: "config", target: "rule: latency-p99-critical", status: "success", detail: "Threshold changed from 500ms to 750ms" },
  { id: "a006", timestamp: "2024-01-15 13:22:17", actor: "james.park@acme.com", actorType: "user", action: "User login", actionType: "login", target: "dashboard", status: "success", detail: "Login from 203.0.113.44 (Seoul, KR)" },
  { id: "a007", timestamp: "2024-01-15 12:42:05", actor: "intelliops-ai", actorType: "system", action: "Root cause identified", actionType: "alert", target: "INC-2841 / postgres-primary", status: "success", detail: "idle_in_transaction_session_timeout=0 correlated to v1.8.2 deploy" },
  { id: "a008", timestamp: "2024-01-15 12:15:33", actor: "james.park@acme.com", actorType: "user", action: "Incident acknowledged", actionType: "alert", target: "INC-2841", status: "success", detail: "On-call acknowledged via PagerDuty mobile" },
  { id: "a009", timestamp: "2024-01-15 12:09:01", actor: "intelliops-ai", actorType: "system", action: "Incident auto-created", actionType: "alert", target: "INC-2841", status: "success", detail: "Connection pool 80%+ anomaly triggered auto-incident" },
  { id: "a010", timestamp: "2024-01-15 11:52:14", actor: "ci-pipeline", actorType: "api", action: "Deploy triggered", actionType: "config", target: "reporting-service / v1.8.2", status: "success", detail: "Automated deploy via ArgoCD — commit 9f3c1b2" },
  { id: "a011", timestamp: "2024-01-15 11:30:00", actor: "sarah.kim@acme.com", actorType: "user", action: "Integration connected", actionType: "integration", target: "PagerDuty / acme-prod", status: "success", detail: "PagerDuty integration re-authorized — service key rotated" },
  { id: "a012", timestamp: "2024-01-15 10:45:22", actor: "sarah.kim@acme.com", actorType: "user", action: "Alert rule created", actionType: "config", target: "rule: cache-hit-rate-warn", status: "success", detail: "New rule: cache hit rate < 70% for 5m → warning" },
  { id: "a013", timestamp: "2024-01-15 10:31:55", actor: "intelliops-ai", actorType: "system", action: "Incident resolved", actionType: "alert", target: "INC-2835", status: "success", detail: "Cache hit rate recovered to 93% — auto-resolved" },
  { id: "a014", timestamp: "2024-01-15 10:02:11", actor: "api-key: io_prod_7f2a", actorType: "api", action: "Metrics push", actionType: "integration", target: "prometheus-remote-write", status: "failed", detail: "Auth error: API key expired. Rotated at 10:03." },
  { id: "a015", timestamp: "2024-01-15 09:46:08", actor: "intelliops-ai", actorType: "system", action: "Root cause identified", actionType: "alert", target: "INC-2835 / cdn-edge", status: "success", detail: "Vary header change correlated to cdn-config v3.1.0 deploy" },
  { id: "a016", timestamp: "2024-01-15 09:45:01", actor: "intelliops-ai", actorType: "system", action: "Incident auto-created", actionType: "alert", target: "INC-2835", status: "success", detail: "Cache hit rate drop 4.8σ from baseline triggered auto-incident" },
  { id: "a017", timestamp: "2024-01-15 09:38:29", actor: "ci-pipeline", actorType: "api", action: "Deploy triggered", actionType: "config", target: "cdn-edge / cdn-config v3.1.0", status: "success", detail: "Blue/green deploy via CDN pipeline — commit 2a8f3c1" },
  { id: "a018", timestamp: "2024-01-15 09:10:44", actor: "tom.russo@acme.com", actorType: "user", action: "User login", actionType: "login", target: "dashboard", status: "failed", detail: "Invalid MFA token — login blocked. Third attempt." },
  { id: "a019", timestamp: "2024-01-15 08:55:30", actor: "tom.russo@acme.com", actorType: "user", action: "User login", actionType: "login", target: "dashboard", status: "success", detail: "Login from 198.51.100.12 (New York, US)" },
  { id: "a020", timestamp: "2024-01-15 08:30:00", actor: "sarah.kim@acme.com", actorType: "user", action: "Integration updated", actionType: "integration", target: "Slack / #sre-alerts", status: "success", detail: "Slack webhook channel changed from #alerts to #sre-alerts" },
];
