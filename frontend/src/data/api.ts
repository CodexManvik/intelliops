import type { AuditRow, Metrics, OutcomeRow, Playbook, Situation } from "./types";

const READ = import.meta.env.VITE_READ_URL ?? "http://localhost:8007";
const GOV = import.meta.env.VITE_GOV_URL ?? "http://localhost:8005";

const AUTH_TOKEN = import.meta.env.VITE_AUTH_TOKEN ?? "";

function authHeaders(base: Record<string, string> = {}): Record<string, string> {
  return AUTH_TOKEN ? { ...base, Authorization: `Bearer ${AUTH_TOKEN}` } : base;
}

async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url, { headers: authHeaders() });
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return (await r.json()) as T;
}

export const loadSituations = () => getJSON<Situation[]>(`${READ}/situations`);
export const loadOutcomes = () => getJSON<OutcomeRow[]>(`${READ}/outcomes`);
export const loadAudit = () => getJSON<AuditRow[]>(`${GOV}/audit`);
export const loadPlaybooks = () => getJSON<Playbook[]>(`${GOV}/playbooks`);
export const loadMetrics = () => getJSON<Metrics>(`${READ}/metrics`);

export async function decideApproval(
  approvalId: string,
  decision: "approved" | "rejected",
  decidedBy = "oncall-alice",
): Promise<void> {
  const r = await fetch(`${GOV}/approvals/${approvalId}/decide`, {
    method: "POST",
    headers: authHeaders({ "content-type": "application/json" }),
    body: JSON.stringify({ decision, decided_by: decidedBy }),
  });
  if (!r.ok) throw new Error(`decide → ${r.status}`);
}
