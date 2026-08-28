# Live UI Additions — Design Spec

**Date:** 2026-08-28
**Owner:** Manvik
**Status:** design (architectural — new gateway endpoint + read-side derivation + two views). Two independent additions, one PR.

## The problem (from live use / presentation feedback)

1. **No view of Meridian's scraped metrics.** Each Meridian service exposes `cpu_usage` + `meridian_error_rate` at `/metrics`, scraped by Prometheus every 5s. But the only way to see them is raw (`/metrics`, or Prometheus at `:9090`). A presenter can't point at the `cpu_usage` spike inside the product.
2. **Governance gate cards are static text.** The three gate cards (`frontend/src/views/Governance.tsx`) describe the gates but show no live activity. The gates ARE enforced (real code in `services/action/remediate.py`) and the audit trail proves they fire — but the cards read as documentation, not active enforcement.

## Goal

- Add a **Metrics** page to the **Meridian UI** showing live per-service `cpu_usage`/`error_rate` from Prometheus — so you watch the spike inside Meridian's own console.
- Make the **IntelliOps Governance** gate cards show **live passed/blocked counts + last-fired**, derived from real audit data — so they read as active enforcement.

## Non-goals / constraints

- **No engine/logic change.** Detection/RCA/governance/remediation unchanged.
- **No fabricated data.** Every number is real (Prometheus / audit records) or labeled "—"/"no data".
- Meridian's Metrics page reads Prometheus **server-side via the gateway** (same-origin, no CORS) — mirroring the existing ops-proxy pattern.
- One PR for both additions (both are "render real data live").

## Global Constraints

- **Gates:** `npm --prefix frontend run build` clean; `npm --prefix services/meridian/ui run build` clean; `uv run pytest -m "not postgres and not kafka"` green (~428); `ruff check .` + `ruff format --check .` clean.
- **Mock-safe:** IntelliOps Governance still builds/renders in `VITE_DATA_MODE=mock` (mock audit data drives the counts).
- **Commit trailer** on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Shared files (coordinate):** `services/meridian/gateway/app.py` (new proxy route), `services/meridian/ui/*` (new view + nav), `frontend/src/views/Governance.tsx` (gate counts).

---

## Part A — Meridian Metrics page

### A1. Backend: `GET /api/ops/metrics` on the Meridian gateway

The gateway already has an ops-proxy (`/api/ops/fault`, `/api/ops/clear`, `/api/ops/deploy` in `services/meridian/gateway/app.py`) that makes server-side calls. Add a sibling that queries Prometheus:

- Route `GET /api/ops/metrics` → for each Meridian service (gateway/validation/aggregation/reporting), query Prometheus for `cpu_usage` and `meridian_error_rate` filtered by the `service` label. Prometheus URL from settings (`get_settings().prometheus_url`, default `http://localhost:9090` — in compose it's `http://prometheus:9090`).
- Use an httpx GET to `{prometheus_url}/api/v1/query` with `query=cpu_usage` and `query=meridian_error_rate` (two queries, or one `{__name__=~"cpu_usage|meridian_error_rate"}`), reusing the pattern in `services/ingestion/adapters/prometheus_source.py:31` (instant-vector query, parse `data.result[].metric.service` + `.value[1]`).
- Return: `{"services": [{"service": "meridian-aggregation", "cpu_usage": 92.0, "error_rate": 0.0, "healthy": false}, ...], "scraped": true}`. `healthy` = `cpu_usage < 50 and error_rate < 0.1` (thresholds; healthy cpu baseline is 18, broken is 92 per `services/meridian/common.py`).
- **Robustness:** if Prometheus is unreachable or returns no data, return `{"services": [], "scraped": false}` (never raise, never fake). Follow the ingestion source's fail-soft pattern.
- **Auth:** the ops-proxy routes are same-origin from the gateway UI; match whatever auth the existing `/api/ops/*` routes use (they're not token-gated for the UI — confirm and match).

### A2. Frontend: new `Metrics` view in the Meridian UI

- Add `Metrics.tsx` under `services/meridian/ui/src/views/`. It fetches `GET /api/ops/metrics` (via the same-origin api client, add a `loadMetrics()` to `services/meridian/ui/src/data/api.ts`), polls every ~3s (a `setInterval` in a `useEffect`, matching the app's existing patterns — check `Dashboard.tsx`/`useBackgroundTraffic` for the idiom).
- Render a row/card per service: service name, `cpu_usage` (green when < 50, red/amber when elevated — a bar or big number), `error_rate`, a healthy/broken dot. Match the Meridian UI's existing visual style (Tailwind, the `brand`/`ink`/`surface` tokens used in `Dashboard.tsx`).
- Empty/unreachable state: "No metrics — is Prometheus running?" (honest, no fake).
- Add the nav entry: `services/meridian/ui/src/App.tsx` (`View` union gains `"metrics"`, render `{view === "metrics" && <Metrics />}`) and `services/meridian/ui/src/components/AppShell.tsx` (`NAV_ITEMS` gains `{ id: "metrics", label: "Metrics", hint: "Live telemetry" }`). Meridian nav becomes Dashboard / Operations / Metrics (3 pages).

### A3. Meridian UI files touched (Part A)
- `services/meridian/gateway/app.py` — new `GET /api/ops/metrics`.
- `services/meridian/ui/src/data/api.ts` — `loadMetrics()` + types.
- `services/meridian/ui/src/views/Metrics.tsx` — new.
- `services/meridian/ui/src/App.tsx` + `components/AppShell.tsx` — nav + route.
- A gateway test (`services/meridian/tests/`) for `/api/ops/metrics` shape (mock the Prometheus httpx call).

---

## Part B — Live Governance gate activity

### B1. Frontend-only: derive gate counts from audit + outcomes

The Governance page already loads `GET /audit` (`loadAudit`) and can load `GET /outcomes` (`loadOutcomes`). No backend change. Derive per-gate activity client-side:

- **RBAC gate (fail-closed):** blocked = audit records with `decision === "deny"` OR outcomes with `reason === "denied:rbac"`; passed = successful executions that got past RBAC (approx: outcomes not denied). Simplest reliable signal: count `decision: "deny"` audit rows as "blocked", `decision: "allow"` execute-action rows as "passed".
- **Reversible-only gate:** blocked = outcomes with `reason === "refused:not-reversible"`.
- **HITL gate:** blocked/held = outcomes with `reason === "aborted:rejected"` or `"aborted:timeout"`; passed = approvals that led to execution (outcomes with a real result after a hitl playbook).

The exact mapping: audit `decision` vocabulary is `allow`/`deny`/`pending` (from `common/contracts.py` AuditRecord + the action service's `_audit` calls: `skipped`/`refused`/`deny`/`abort`/`allow`/`execute-failed`/`rolled-back`). Outcomes carry `reason` (health_after): `denied:rbac`/`refused:not-reversible`/`aborted:rejected`/`aborted:timeout`/`healthy`/etc. **Use the outcomes `reason` vocabulary as the primary source** (it's the precise per-gate reason) and fall back to audit `decision` for the "passed/allowed" tally.

### B2. Render passed/blocked + last-fired on each gate card

- Each of the 3 gate cards (`frontend/src/views/Governance.tsx`, the `gates` array + render) gains a small live footer: e.g. **"✓ 47 passed · ✗ 2 blocked · last 2m ago"**, computed from the loaded audit/outcomes. Keep the descriptive text; add the live line.
- Compute the counts in a `useMemo` over the loaded `audit`/`outcomes`. Use `timeAgo` (ISO-safe) for last-fired.
- Empty state: "no activity yet" (not a fake number).

### B3. IntelliOps files touched (Part B)
- `frontend/src/views/Governance.tsx` — add the per-gate count derivation + render.
- Possibly `frontend/src/data/source.ts`/mock — ensure `loadOutcomes` is available to Governance (it's already exported); ensure mock audit/outcomes drive non-zero counts in mock mode.

---

## Acceptance criteria

1. **Meridian Metrics page:** a 3rd Meridian nav item "Metrics" shows live per-service `cpu_usage`/`error_rate` from Prometheus, polling ~3s; breaking a service (Operations) makes its `cpu_usage` visibly jump on this page within a few seconds. Prometheus-unreachable shows an honest empty state, never a fake number.
2. **Gateway endpoint:** `GET /api/ops/metrics` returns real Prometheus data server-side (no browser CORS), fail-soft when Prometheus is down.
3. **Governance gates live:** each of the 3 gate cards shows real passed/blocked counts + last-fired, derived from audit/outcomes; the counts change as incidents are gated. No fabricated numbers.
4. **Gates:** both frontends build; backend suite ~428 green; ruff clean; IntelliOps mock mode still renders Governance with counts from mock data.

## Suggested task ordering (for the plan)

1. Gateway `GET /api/ops/metrics` (server-side Prometheus query, fail-soft) + a test.
2. Meridian UI Metrics view + nav + api client + build.
3. Governance gate counts (frontend-only) + build + mock check.
4. Verify end-to-end (live Prometheus during a break; gate counts during a gated incident) + docs note.

Rationale: backend endpoint first (Part A's data source), then its UI, then the independent frontend-only Part B.
