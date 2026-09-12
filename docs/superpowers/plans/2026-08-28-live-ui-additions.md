# Live UI Additions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two live-data UI additions in one PR — (A) a Meridian **Metrics** page showing live per-service `cpu_usage`/`error_rate` from Prometheus (via a new gateway proxy endpoint), and (B) live **passed/blocked counts + last-fired** on the IntelliOps Governance gate cards (derived from real audit/outcomes data).

**Architecture:** Part A adds `GET /api/ops/metrics` on the Meridian gateway that queries Prometheus server-side (same-origin, no CORS — mirrors the existing ops-proxy), plus a new `Metrics.tsx` view polling it. Part B is frontend-only: derive per-gate counts client-side from the audit + outcomes already loadable on the Governance page. No engine/logic change.

**Tech Stack:** Python 3.11 + FastAPI + httpx (gateway); React + TypeScript + Vite + Tailwind (both frontends). Docker is running — verify live. Gates: `uv run pytest -m "not postgres and not kafka"`, `ruff check .`, `ruff format --check .`, `npm --prefix frontend run build`, `npm --prefix services/meridian/ui run build`.

**Spec:** `docs/superpowers/specs/2026-08-28-live-ui-additions-design.md`

## Global Constraints

- **No engine/logic change.** Only a new read-only gateway endpoint + frontend rendering.
- **No fabricated data.** Every number is real (Prometheus / audit) or an honest empty state ("—" / "no data" / "no activity yet"). Prometheus-unreachable and empty-audit must render cleanly, never a fake number.
- **Fail-soft backend.** The gateway metrics endpoint must never raise on a Prometheus outage — return an empty payload (mirror `services/ingestion/adapters/prometheus_source.py`'s defensive pattern).
- **Gates:** both frontends build; `uv run pytest -m "not postgres and not kafka"` green (~428); `ruff check .` + `ruff format --check .` clean. IntelliOps mock mode still renders Governance (mock audit/outcomes drive the counts).
- **Commit trailer** on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **git:** branch `feat/live-ui-additions` (off master). Do NOT merge/push to master — open a PR.
- **Meridian dist/ is gitignored + Docker-built** (`Dockerfile.meridian` multi-stage) — do NOT commit `services/meridian/ui/dist/`. Commit source only; the image rebuild serves the new page.
- **Shared files:** `services/meridian/gateway/app.py`, `services/meridian/ui/src/*`, `frontend/src/views/Governance.tsx`.

---

## Task 1: Gateway `GET /api/ops/metrics` — server-side Prometheus query

**Files:**
- Modify: `services/meridian/gateway/app.py` (add the route inside `_routes`)
- Test: `services/meridian/tests/test_gateway_metrics.py` (new)

**Interfaces:**
- Produces: `GET /api/ops/metrics` → `{"scraped": bool, "services": [{"service": str, "cpu_usage": float | None, "error_rate": float | None, "healthy": bool}]}`. Fail-soft: Prometheus unreachable/empty → `{"scraped": false, "services": []}`.

- [ ] **Step 1: Write the failing test**

Create `services/meridian/tests/test_gateway_metrics.py`. Mock the Prometheus httpx call and assert the endpoint shape. Follow the existing gateway test patterns in `services/meridian/tests/` for how the app is constructed (use `TestClient`). The endpoint queries Prometheus; inject/patch the httpx client so the test doesn't need a real Prometheus:

```python
"""GET /api/ops/metrics proxies a Prometheus instant query, server-side."""

from __future__ import annotations

from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient


def _prom_response():
    # Prometheus instant-query success shape for cpu_usage + meridian_error_rate
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {"metric": {"__name__": "cpu_usage", "service": "meridian-aggregation"}, "value": [0, "92"]},
                {"metric": {"__name__": "cpu_usage", "service": "meridian-gateway"}, "value": [0, "18"]},
                {"metric": {"__name__": "meridian_error_rate", "service": "meridian-aggregation"}, "value": [0, "0"]},
            ],
        },
    }


def test_ops_metrics_returns_per_service_values():
    from services.meridian.gateway.app import app

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_prom_response())

    with patch("services.meridian.gateway.app.httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.get.return_value = httpx.Response(
            200, json=_prom_response(), request=httpx.Request("GET", "http://prom/api/v1/query")
        )
        with TestClient(app) as client:
            r = client.get("/api/ops/metrics")
    assert r.status_code == 200
    body = r.json()
    assert body["scraped"] is True
    agg = next(s for s in body["services"] if s["service"] == "meridian-aggregation")
    assert agg["cpu_usage"] == 92.0
    assert agg["healthy"] is False  # cpu 92 > threshold


def test_ops_metrics_fail_soft_when_prometheus_down():
    from services.meridian.gateway.app import app

    with patch("services.meridian.gateway.app.httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.get.side_effect = httpx.ConnectError("no prom")
        with TestClient(app) as client:
            r = client.get("/api/ops/metrics")
    assert r.status_code == 200
    assert r.json() == {"scraped": False, "services": []}
```

(If the existing gateway tests use a different app-construction idiom — e.g. a fixture — match it. The two behaviors to assert are: correct per-service shape on success, and `{"scraped": false, "services": []}` on a Prometheus error.)

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest services/meridian/tests/test_gateway_metrics.py -v`
Expected: FAIL (route doesn't exist → 404).

- [ ] **Step 3: Add the route**

In `services/meridian/gateway/app.py`, add imports at the top: `from common.config import get_settings`. Inside `_routes(app, state)`, after the `/api/ops/deploy` route, add:

```python
    @app.get("/api/ops/metrics")
    def ops_metrics() -> dict:
        """Live per-service telemetry, proxied from Prometheus server-side so the
        UI stays same-origin (no CORS) and never holds the Prometheus URL.
        Fail-soft: any Prometheus error yields an empty payload, never a 5xx."""
        prom = get_settings().prometheus_url.rstrip("/")
        query = '{__name__=~"cpu_usage|meridian_error_rate"}'
        try:
            with httpx.Client(timeout=5.0) as c:
                resp = c.get(f"{prom}/api/v1/query", params={"query": query})
        except httpx.HTTPError:
            return {"scraped": False, "services": []}
        if resp.status_code != 200:
            return {"scraped": False, "services": []}
        try:
            body = resp.json()
        except ValueError:
            return {"scraped": False, "services": []}
        if not isinstance(body, dict) or body.get("status") != "success":
            return {"scraped": False, "services": []}
        # Fold the flat result vector into per-service {cpu_usage, error_rate}.
        by_service: dict[str, dict] = {}
        for entry in body.get("data", {}).get("result", []):
            metric = entry.get("metric", {})
            svc = metric.get("service")
            name = metric.get("__name__")
            value_pair = entry.get("value", [0, "0"])
            if not svc or not isinstance(value_pair, list) or len(value_pair) < 2:
                continue
            try:
                val = float(value_pair[1])
            except (TypeError, ValueError):
                continue
            row = by_service.setdefault(svc, {"service": svc, "cpu_usage": None, "error_rate": None})
            if name == "cpu_usage":
                row["cpu_usage"] = val
            elif name == "meridian_error_rate":
                row["error_rate"] = val
        services = []
        for row in by_service.values():
            cpu = row["cpu_usage"]
            err = row["error_rate"]
            row["healthy"] = (cpu is None or cpu < 50) and (err is None or err < 0.1)
            services.append(row)
        services.sort(key=lambda r: r["service"])
        return {"scraped": bool(services), "services": services}
```

(`httpx` is already imported at the top of the file. The healthy threshold: cpu baseline is 18, broken is 92 — 50 is a safe midpoint; error_rate healthy is ~0, broken 0.5 — 0.1 is a safe threshold.)

- [ ] **Step 4: Run the test — verify it passes**

Run: `uv run pytest services/meridian/tests/test_gateway_metrics.py -v`
Expected: PASS.

- [ ] **Step 5: Full suite + ruff**

Run: `uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check .`
Expected: ~430 pass (428 + 2 new), ruff clean (format the new test if flagged).

- [ ] **Step 6: Commit**

```bash
git add services/meridian/gateway/app.py services/meridian/tests/test_gateway_metrics.py
git commit -m "feat(meridian): GET /api/ops/metrics — server-side Prometheus proxy for live telemetry"
```

---

## Task 2: Meridian UI Metrics page

**Files:**
- Modify: `services/meridian/ui/src/data/api.ts` (add `loadMetrics` + types)
- Create: `services/meridian/ui/src/views/Metrics.tsx`
- Modify: `services/meridian/ui/src/App.tsx` (route), `services/meridian/ui/src/components/AppShell.tsx` (nav)
- Test: none new (verified by build + live)

**Interfaces:**
- Consumes: `GET /api/ops/metrics` (Task 1).
- Produces: a 3rd Meridian nav item "Metrics" polling live telemetry.

- [ ] **Step 1: Add the api client + types**

In `services/meridian/ui/src/data/api.ts`, add after `clearFault`:

```ts
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
```

- [ ] **Step 2: Create the Metrics view**

Create `services/meridian/ui/src/views/Metrics.tsx`. Poll `loadMetrics()` every 3s (a `useEffect` with `setInterval`, matching the app's idiom — the app already uses interval polling in `useBackgroundTraffic`). Render a card/row per service: service name, `cpu_usage` (green when healthy, red/amber when elevated), `error_rate`, a healthy/broken dot. Honest empty state when `!scraped` or `services` is empty. Match the Meridian UI's Tailwind style (look at `Dashboard.tsx` for the `surface`/`ink`/`brand`/`line` token classes + card idiom). Example skeleton:

```tsx
import { useEffect, useState } from "react";
import { loadMetrics, type MetricsResult } from "../data/api";
import StatusPill from "../components/StatusPill";

export default function Metrics() {
  const [data, setData] = useState<MetricsResult>({ scraped: false, services: [] });

  useEffect(() => {
    let alive = true;
    const tick = () => loadMetrics().then((d) => alive && setData(d)).catch(() => {});
    tick();
    const id = window.setInterval(tick, 3000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-serif text-2xl font-semibold text-ink">Live telemetry</h1>
        <p className="mt-1 text-sm text-ink-2">
          Real Prometheus metrics for each Meridian service, scraped every 5s. Break a service in
          Operations and watch its CPU climb here.
        </p>
      </div>

      {!data.scraped || data.services.length === 0 ? (
        <div className="rounded-lg border border-line bg-surface p-8 text-center text-sm text-ink-3">
          No metrics — is Prometheus running? (compose brings it up on :9090)
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {data.services.map((s) => {
            const cpu = s.cpu_usage;
            const hot = cpu != null && cpu >= 50;
            return (
              <div key={s.service} className="rounded-lg border border-line bg-surface p-5">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold text-ink">{s.service}</span>
                  <StatusPill tone={s.healthy ? "ok" : "bad"} pulse={!s.healthy}>
                    {s.healthy ? "healthy" : "degraded"}
                  </StatusPill>
                </div>
                <div className="mt-4 flex items-end justify-between">
                  <div>
                    <div className="text-2xs uppercase tracking-wide text-ink-3">CPU usage</div>
                    <div className={`text-3xl font-semibold tabular-nums ${hot ? "text-brand-dim" : "text-ink"}`}>
                      {cpu != null ? cpu.toFixed(0) : "—"}
                      {cpu != null && <span className="text-base text-ink-3">%</span>}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-2xs uppercase tracking-wide text-ink-3">Error rate</div>
                    <div className="text-lg font-medium tabular-nums text-ink-2">
                      {s.error_rate != null ? `${(s.error_rate * 100).toFixed(0)}%` : "—"}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
```

**IMPORTANT:** verify the actual token classes + `StatusPill` prop names against the real `Dashboard.tsx`/`StatusPill.tsx` before finalizing — `StatusPill`'s `tone` values may be `"ok"|"neutral"|...` not `"bad"`. Read `services/meridian/ui/src/components/StatusPill.tsx` and match its real API. If a token class (`text-ink-3`, `text-brand-dim`, `border-line`) doesn't exist, use the nearest one the other views use. The skeleton is a guide; the real classes must come from the existing components.

- [ ] **Step 3: Wire the nav + route**

In `services/meridian/ui/src/App.tsx`: change the `View` union to `"dashboard" | "operations" | "metrics"`; add `"metrics"` to `VALID_VIEWS`; import `Metrics`; add `{view === "metrics" && <Metrics />}`.

In `services/meridian/ui/src/components/AppShell.tsx`: add to `NAV_ITEMS`: `{ id: "metrics", label: "Metrics", hint: "Live telemetry" }`.

- [ ] **Step 4: Build the Meridian UI**

Run: `npm --prefix services/meridian/ui run build`
Expected: PASS. (dist/ is gitignored — do NOT commit it.)

- [ ] **Step 5: Verify live (Docker is running)**

Rebuild + restart the gateway so it serves the new page, then check it:
```bash
docker compose -f deploy/docker-compose.yml up -d --build meridian-gateway
```
Open `http://localhost:8008` → the **Metrics** tab. It should show all 4 services with healthy cpu (~18). Then break one: on the Operations tab click "Aggregation saturated" (or `curl` the gateway ops proxy), wait ~5s, and confirm meridian-aggregation's CPU climbs toward 92 on the Metrics page. Capture the result in the report. (If the gateway serves a stale dist, confirm the image rebuilt.)

- [ ] **Step 6: Commit (source only — NOT dist)**

```bash
git add services/meridian/ui/src/data/api.ts services/meridian/ui/src/views/Metrics.tsx services/meridian/ui/src/App.tsx services/meridian/ui/src/components/AppShell.tsx
git commit -m "feat(meridian): live Metrics page — per-service cpu/error_rate from Prometheus"
```

---

## Task 3: Live Governance gate activity (passed/blocked + last-fired)

**Files:**
- Modify: `frontend/src/views/Governance.tsx` (derive + render per-gate counts)
- Test: none new (verified by build + mock render)

**Interfaces:**
- Consumes: `loadAudit` (already used), `loadOutcomes` (add — outcomes carry the precise `reason` vocabulary). `OutcomeRow.reason` ∈ `healthy|denied:rbac|refused:not-reversible|aborted:rejected|aborted:timeout|...`; `AuditRow.decision` ∈ `allow|deny|pending`.

- [ ] **Step 1: Load outcomes + derive per-gate counts**

In `frontend/src/views/Governance.tsx`, add `loadOutcomes` to the import from `../data/source` and `OutcomeRow` to the type import. Add:

```tsx
  const { data: outcomes } = useLiveData(loadOutcomes, [] as OutcomeRow[]);
```

Then compute per-gate stats keyed on each gate's `reason` (the `gates` array already has a `reason` field: `denied:rbac`, `refused:not-reversible`, `aborted:timeout`). Blocked = outcomes whose `reason` matches that gate; passed = the count that got past it; last-fired = newest matching outcome ts. A `useMemo`:

```tsx
  const gateStats = useMemo(() => {
    // Map each gate's `reason` to its blocking outcomes. HITL covers both
    // aborted:rejected and aborted:timeout; RBAC = denied:rbac; reversible =
    // refused:not-reversible. "passed" = allow-decisions in the audit for the
    // action being gated (approx: successful/attempted executions).
    const blockedReasons: Record<string, (r: string) => boolean> = {
      "denied:rbac": (r) => r === "denied:rbac",
      "refused:not-reversible": (r) => r === "refused:not-reversible",
      "aborted:timeout": (r) => r === "aborted:rejected" || r === "aborted:timeout",
    };
    const stats: Record<string, { passed: number; blocked: number; lastTs: number | string | null }> = {};
    for (const g of gates) {
      const match = blockedReasons[g.reason];
      const blockedOutcomes = outcomes.filter((o) => match(o.reason));
      // "passed" heuristic: total outcomes that reached a real result minus the ones this gate blocked.
      const passed = outcomes.filter((o) => o.reason === "healthy").length;
      const lastTs = blockedOutcomes.length
        ? blockedOutcomes.map((o) => o.ts).sort((a, b) => new Date(b).getTime() - new Date(a).getTime())[0]
        : null;
      stats[g.reason] = { passed, blocked: blockedOutcomes.length, lastTs };
    }
    return stats;
  }, [outcomes]);
```

(Adjust the "passed" heuristic to whatever is most honest given the real data — `healthy` outcomes are remediations that passed all gates. If a cleaner signal exists in the audit `decision` field, use it. The blocked count is the precise per-gate signal and is the important one.)

- [ ] **Step 2: Render the live line on each gate card**

In the gate-card render (the `gates.map(...)`), add a small footer under the existing body/reason showing the live stats:

```tsx
              <div className="mt-4 flex items-center gap-3 font-mono text-2xs text-ink-3">
                <span className="text-sev-ok">✓ {gateStats[g.reason]?.passed ?? 0} passed</span>
                <span className={g.tone}>✗ {gateStats[g.reason]?.blocked ?? 0} blocked</span>
                {gateStats[g.reason]?.lastTs && <span className="ml-auto">last {timeAgo(gateStats[g.reason].lastTs)}</span>}
              </div>
```

(Find the actual gate-card JSX in the file — it renders `g.title`, `g.body`, `g.reason` — and insert this after the `g.reason` line. `timeAgo` is already imported.)

- [ ] **Step 3: Build + mock check**

Run: `npm --prefix frontend run build`
Expected: PASS. Then confirm mock mode renders: mock `outcomes` should produce non-zero counts (check `frontend/src/data/mock.ts` has outcomes with a `denied:rbac`/`refused:*`/`aborted:*` reason among them; if all mock outcomes are `healthy`, the blocked counts show 0 in mock — that's honest, but for a livelier mock demo the mock could include one blocked outcome. Optional — do not fabricate in live).

- [ ] **Step 4: Verify live (Docker running)**

With the stack up + console in live mode, the Governance page's gate cards should show real counts from the accumulated audit/outcomes (there are already hundreds of records). Confirm the numbers are non-zero and last-fired shows a real time. Capture in the report.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/Governance.tsx
git commit -m "feat(ui): live gate activity — passed/blocked counts + last-fired from real audit/outcomes"
```

---

## Task 4: End-to-end verification + docs

**Files:**
- Modify: `TODO.md` (mark the two live-UI items done / remove them)
- Test: run all gates + the live checks

- [ ] **Step 1: All gates green**

Run: `uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check . && npm --prefix frontend run build && npm --prefix services/meridian/ui run build`
Expected: all PASS.

- [ ] **Step 2: Live end-to-end (Docker)**

- Meridian Metrics: break a service in Operations → its CPU visibly climbs on the Metrics page within ~5s; Clear → returns to baseline. Prometheus-down (stop prometheus container) → honest empty state.
- Governance gates: the 3 cards show real passed/blocked + last-fired; drive a rejected incident (approve→reject in the console) and confirm the HITL gate's blocked count increments after the outcome lands.
Record both in the report.

- [ ] **Step 3: Update TODO.md**

Remove (or mark DONE) the two entries "Live Meridian metrics view in the console" and "Live Governance gate activity" — note they shipped in this PR. (Correct the metrics-view entry's location note: it landed in the MERIDIAN UI, not the IntelliOps console.)

- [ ] **Step 4: Commit**

```bash
git add TODO.md
git commit -m "docs: mark live Metrics page + gate activity shipped in TODO"
```

---

## Self-Review checklist (before the PR)

1. **Meridian Metrics page:** 3rd nav item; live per-service cpu/error from Prometheus via the gateway proxy; break→spike visible; honest empty state — Tasks 1, 2.
2. **Gateway endpoint fail-soft:** Prometheus down → `{scraped:false, services:[]}`, never a 5xx — Task 1.
3. **Governance gates live:** real passed/blocked + last-fired per gate from audit/outcomes; no fabricated numbers — Task 3.
4. **Gates green:** both builds; suite ~430; ruff clean; mock mode renders Governance — Task 4.
5. **No dist committed** (Meridian dist gitignored + Docker-built) — Task 2.
