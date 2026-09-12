export type AuditActionType = "alert" | "config" | "login" | "integration";
export type AuditStatus = "success" | "failed" | "pending";

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
  { id: "a001", timestamp: "2024-01-15 14:29:03", actor: "intelliops-ai", actorType: "system", action: "Root cause identified", actionType: "alert", target: "ALT-0091 / api-gateway", status: "success", detail: "Correlated missing index on analytics_events to api-v2.3.1 deploy" },
  { id: "a002", timestamp: "2024-01-15 14:24:01", actor: "pagerduty-webhook", actorType: "api", action: "On-call notified", actionType: "alert", target: "ALT-0091 / @sre-oncall", status: "success", detail: "PagerDuty alert #PA-91823 sent to Maya Chen" },
  { id: "a003", timestamp: "2024-01-15 14:23:18", actor: "intelliops-ai", actorType: "system", action: "Alert auto-escalated", actionType: "alert", target: "ALT-0091", status: "success", detail: "P99 latency 4.2σ above baseline triggered escalation" },
  { id: "a004", timestamp: "2024-01-15 14:11:42", actor: "maya.chen@acme.com", actorType: "user", action: "Deploy triggered", actionType: "config", target: "api-gateway / api-v2.3.1", status: "success", detail: "Production deploy via GitHub Actions run #4821" },
  { id: "a005", timestamp: "2024-01-15 13:50:00", actor: "maya.chen@acme.com", actorType: "user", action: "Alert rule modified", actionType: "config", target: "rule: latency-p99-critical", status: "success", detail: "Threshold changed from 500ms to 750ms" },
  { id: "a006", timestamp: "2024-01-15 13:22:17", actor: "james.park@acme.com", actorType: "user", action: "User login", actionType: "login", target: "dashboard", status: "success", detail: "Login from 203.0.113.44 (Seoul, KR)" },
  { id: "a007", timestamp: "2024-01-15 12:42:05", actor: "intelliops-ai", actorType: "system", action: "Root cause identified", actionType: "alert", target: "ALT-0090 / postgres-primary", status: "success", detail: "idle_in_transaction_session_timeout=0 correlated to v1.8.2 deploy" },
  { id: "a008", timestamp: "2024-01-15 12:15:33", actor: "james.park@acme.com", actorType: "user", action: "Alert acknowledged", actionType: "alert", target: "ALT-0090", status: "success", detail: "On-call acknowledged via PagerDuty mobile" },
  { id: "a009", timestamp: "2024-01-15 12:09:01", actor: "intelliops-ai", actorType: "system", action: "Warning alert triggered", actionType: "alert", target: "ALT-0090", status: "success", detail: "Connection pool 80%+ anomaly triggered warning" },
  { id: "a010", timestamp: "2024-01-15 11:52:14", actor: "ci-pipeline", actorType: "api", action: "Deploy triggered", actionType: "config", target: "reporting-service / v1.8.2", status: "success", detail: "Automated deploy via ArgoCD — commit 9f3c1b2" },
  { id: "a011", timestamp: "2024-01-15 11:30:00", actor: "sarah.kim@acme.com", actorType: "user", action: "Integration connected", actionType: "integration", target: "PagerDuty / acme-prod", status: "success", detail: "PagerDuty integration re-authorized — service key rotated" },
  { id: "a012", timestamp: "2024-01-15 10:45:22", actor: "sarah.kim@acme.com", actorType: "user", action: "Alert rule created", actionType: "config", target: "rule: cache-hit-rate-warn", status: "success", detail: "New rule: cache hit rate < 70% for 5m → warning" },
  { id: "a013", timestamp: "2024-01-15 10:31:55", actor: "intelliops-ai", actorType: "system", action: "Alert auto-resolved", actionType: "alert", target: "ALT-0085", status: "success", detail: "Cache hit rate recovered to 93% — auto-resolved" },
  { id: "a014", timestamp: "2024-01-15 10:02:11", actor: "api-key: io_prod_7f2a", actorType: "api", action: "Metrics push", actionType: "integration", target: "prometheus-remote-write", status: "failed", detail: "Auth error: API key expired. Rotated at 10:03." },
  { id: "a015", timestamp: "2024-01-15 09:46:08", actor: "intelliops-ai", actorType: "system", action: "Root cause identified", actionType: "alert", target: "ALT-0085 / cdn-edge", status: "success", detail: "Vary header change correlated to cdn-config v3.1.0 deploy" },
  { id: "a016", timestamp: "2024-01-15 09:45:01", actor: "intelliops-ai", actorType: "system", action: "Warning alert triggered", actionType: "alert", target: "ALT-0085", status: "success", detail: "Cache hit rate drop 4.8σ from baseline triggered warning" },
  { id: "a017", timestamp: "2024-01-15 09:38:29", actor: "ci-pipeline", actorType: "api", action: "Deploy triggered", actionType: "config", target: "cdn-edge / cdn-config v3.1.0", status: "success", detail: "Blue/green deploy via CDN pipeline — commit 2a8f3c1" },
  { id: "a018", timestamp: "2024-01-15 09:10:44", actor: "tom.russo@acme.com", actorType: "user", action: "User login", actionType: "login", target: "dashboard", status: "failed", detail: "Invalid MFA token — login blocked. Third attempt." },
  { id: "a019", timestamp: "2024-01-15 08:55:30", actor: "tom.russo@acme.com", actorType: "user", action: "User login", actionType: "login", target: "dashboard", status: "success", detail: "Login from 198.51.100.12 (New York, US)" },
  { id: "a020", timestamp: "2024-01-15 08:30:00", actor: "sarah.kim@acme.com", actorType: "user", action: "Integration updated", actionType: "integration", target: "Slack / #sre-alerts", status: "success", detail: "Slack webhook channel changed from #alerts to #sre-alerts" },
];
