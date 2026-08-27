import type { AuditRow, BaselineInfo, LlmProbe, Metrics, OutcomeRow, Playbook, Situation, SystemInfo } from "./types";

const READ = import.meta.env.VITE_READ_URL ?? "http://localhost:8007";
const GOV = import.meta.env.VITE_GOV_URL ?? "http://localhost:8005";
const CORR = import.meta.env.VITE_CORR_URL ?? "http://localhost:8002";
const RCA = import.meta.env.VITE_RCA_URL ?? "http://localhost:8003";

const AUTH_TOKEN = import.meta.env.VITE_AUTH_TOKEN ?? "";

function authHeaders(base: Record<string, string> = {}): Record<string, string> {
  return AUTH_TOKEN ? { ...base, Authorization: `Bearer ${AUTH_TOKEN}` } : base;
}

async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url, { headers: authHeaders() });
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return (await r.json()) as T;
}

async function postJSON<T>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, {
    method: "POST",
    headers: authHeaders({ "content-type": "application/json" }),
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return (await r.json()) as T;
}

export const loadSituations = () => getJSON<Situation[]>(`${READ}/situations`);
export const loadSituationDetail = (id: string) => getJSON<Situation>(`${READ}/situations/${id}`);
export const loadSystem = () => getJSON<SystemInfo>(`${READ}/system`);
export const loadOutcomes = () => getJSON<OutcomeRow[]>(`${READ}/outcomes`);
export const loadAudit = () => getJSON<AuditRow[]>(`${GOV}/audit`);
export const loadPlaybooks = () => getJSON<Playbook[]>(`${GOV}/playbooks`);
export const loadMetrics = () => getJSON<Metrics>(`${READ}/metrics`);
export const loadBaseline = () => getJSON<BaselineInfo>(`${CORR}/baseline`);
export const loadLlmConfig = () => getJSON<SystemInfo["llm"]>(`${RCA}/config/llm`);
export const setLlmConfig = (cfg: { endpoint: string; api_key: string; model: string }) =>
  postJSON<SystemInfo["llm"]>(`${RCA}/config/llm`, cfg);
export const testLlmConfig = (cfg: { endpoint: string; api_key: string; model: string }) =>
  postJSON<LlmProbe>(`${RCA}/config/llm/test`, cfg);

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

export function openStream(): EventSource {
  const url = new URL(`${READ}/stream`);
  if (AUTH_TOKEN) url.searchParams.set("token", AUTH_TOKEN);
  return new EventSource(url.toString()); // no withCredentials — conflicts with wildcard CORS
}
