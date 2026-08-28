// Same-origin gateway client. The gateway serves this UI's built dist/ at
// "/" and exposes /api/* on the same origin, so VITE_API_URL is normally
// unset in production — an empty prefix means every fetch below resolves
// relative to wherever the UI is served from, and there is no CORS to
// configure. Set VITE_API_URL only for local dev against a gateway running
// on a different port.
const API = import.meta.env.VITE_API_URL ?? "";

export interface SubmissionInput {
  client: string;
  period: string;
  amount: number;
}

export interface SubmissionResult {
  accepted: boolean;
  client: string;
  period: string;
  amount: number;
}

export interface ReportsResult {
  reports: unknown[];
}

export type FaultType = "saturation" | "error" | "latency" | "crash";

export interface FaultSpec {
  type: FaultType;
  magnitude?: number;
  duration_seconds?: number;
}

export type MeridianService = "gateway" | "validation" | "aggregation" | "reporting";

async function asJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

export const submitData = (body: SubmissionInput): Promise<SubmissionResult> =>
  fetch(`${API}/api/submissions`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  }).then((r) => asJson<SubmissionResult>(r));

export const loadReports = (): Promise<ReportsResult> =>
  fetch(`${API}/api/reports`).then((r) => asJson<ReportsResult>(r));

export const injectFault = (
  service: MeridianService,
  spec: FaultSpec,
): Promise<{ status: number }> =>
  fetch(`${API}/api/ops/fault`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ service, spec }),
  }).then((r) => asJson<{ status: number }>(r));

export const deploy = (service: MeridianService): Promise<{ deployed: string }> =>
  fetch(`${API}/api/ops/deploy`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ service }),
  }).then((r) => asJson<{ deployed: string }>(r));

export const clearFault = (service: MeridianService): Promise<{ status: number }> =>
  fetch(`${API}/api/ops/clear`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ service }),
  }).then((r) => asJson<{ status: number }>(r));

export interface ServiceMetric {
  service: string;
  cpu_usage: number | null;
  error_rate: number | null;
  healthy: boolean;
}

export interface MetricsResult {
  scraped: boolean;
  services: ServiceMetric[];
}

export const loadMetrics = (): Promise<MetricsResult> =>
  fetch(`${API}/api/ops/metrics`).then((r) => asJson<MetricsResult>(r));
