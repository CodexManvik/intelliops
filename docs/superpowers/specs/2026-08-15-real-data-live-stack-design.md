# Real-Data Live Stack — Design

**Date:** 2026-08-15
**Status:** Approved (pending spec review)
**Depends on:** Slices 0–4 (all six services + closed loop, complete)

## Goal

Replace the frontend's mock data with a **genuinely live closed loop** that runs
entirely on the developer's machine via `docker compose`, for free. The system
observes real metrics from a real running application, detects real anomalies the
operator triggers, walks them through the full pipeline (detect → diagnose →
HITL-approve → resolve), and feeds outcomes back — all visible in the React
console reading live APIs.

Nothing in the existing six services is rewritten. Every external dependency
already sits behind a `Protocol` (ADR-005); "going live" means writing **real
implementations behind those seams** plus the **read side** the dashboard needs.

### Explicit non-goals (YAGNI)

- No Kubernetes/Ansible remediator. The remediator stays `DryRunRemediator`
  (logs steps, touches nothing) per ADR-007. "Resolved" means *dry-run executed +
  simulated-healthy*, and the README says so plainly.
- No real health checker (stays `AlwaysHealthyChecker`).
- No auth on the new endpoints, no cloud hosting, no continuous auto-chaos.
- No read-model snapshotting/trimming (rebuildable from the stream; noted, not built).

## Chosen approach

- **Real data = simulated live stack.** A tiny FastAPI demo app emits Prometheus
  metrics; the operator perturbs it; IntelliOps observes and dry-run-remediates.
- **Hosting = local docker-compose.** Zero cost, self-contained.
- **Metrics path = real Prometheus in the loop.** A `prom/prometheus` container
  scrapes the demo app; a new `PrometheusSource` queries it via PromQL. The same
  adapter works against any real Prometheus later.
- **Read APIs = read-model + new GET endpoints (CQRS-lite).** A projection tails
  the event stream and serves `GET /situations` / `GET /outcomes`.
- **Demo driver = manual toggles + one-command scenario script.**

## Architecture

Five new pieces plus wiring. No existing service is rewritten.

1. **`demo-app`** (`services/demo_app/`) — ~60-line FastAPI service, the breakable
   target. Emits Prometheus metrics via `prometheus-client`:
   - `http_requests_total` (counter), `http_request_errors_total` (counter),
     `cpu_usage` (gauge).
   - Endpoints: `GET /work` (fake work, increments counters), `POST /break` (flip
     unhealthy: error rate + CPU spike), `POST /fix` (recover), `GET /metrics`
     (Prometheus exposition), `GET /health`.

2. **Prometheus container** — official `prom/prometheus`, scrapes `demo-app` every
   5s per `deploy/prometheus.yml`. Free, standard.

3. **`PrometheusSource`** (`services/ingestion/adapters/prometheus_source.py`) — a
   `TelemetrySource` (existing Protocol) that runs a PromQL query against
   Prometheus' HTTP API (`/api/v1/query`) and maps results to `TelemetryEvent`s.
   Plus a **background poll loop** added to ingestion's lifespan (ingestion is
   currently push-only via `POST /ingest`; we add a daemon thread that polls the
   source and publishes to `telemetry.raw`). A `TELEMETRY_MODE=file|prometheus`
   switch keeps the file source (and all existing tests) working unchanged.

4. **`read` service** (`services/read/`) — a lightweight read-model. Reuses the
   exact lifespan + daemon-thread consumer pattern `feedback` already uses. Tails
   `situations.detected`, `situations.diagnosed`, `remediation.outcomes`, and the
   approval-request event; keeps latest state in an in-memory projection. Exposes
   `GET /situations` and `GET /outcomes`. Host port 8007.

5. **Frontend API client** (`frontend/src/data/api.ts`) — mirrors the `mock.ts`
   shape, hits the live endpoints. A `VITE_API_BASE` env var and a one-line toggle
   in `src/data/` select `mock` vs `live` — a switch, not a rewrite.

## Data flow — the live incident, end to end

```
[healthy]  demo-app emits nominal metrics
           Prometheus scrapes demo-app every 5s
           ingestion: PrometheusSource polls Prometheus every ~5s (PromQL),
                      emits TelemetryEvent → publishes telemetry.raw

  ── operator: POST /break  (or ./scripts/chaos.sh) ──

demo-app    error rate jumps, cpu_usage spikes
Prometheus  next scrape captures the spike
ingestion   next poll sees anomalous values → telemetry.raw
correlation River z-score exceeds threshold → Situation → situations.detected
            (status: detected)
rca         enriches + ranks hypotheses → situations.diagnosed
            (status: diagnosed, top hypothesis + confidence)
action      selects matching HITL playbook → publishes ApprovalRequest,
            waits (status: acting/pending)

  ── dashboard shows HITL gate; operator clicks Approve ──
     (frontend POSTs governance /approvals/{id}/decide)

action      approved → DryRunRemediator.execute() logs steps →
            AlwaysHealthyChecker → remediation.outcomes
            (result: success, health_after: healthy) → status: resolved
feedback    labels outcome; after ≥3 clean successes graduates playbook hitl→auto

  ── operator: POST /fix → metrics return to nominal ──
```

**Bus topology (existing, unchanged):**
`telemetry.raw` → `situations.detected` → `situations.diagnosed` →
`remediation.outcomes`. The read-model subscribes to the three situation/outcome
topics plus the approval event.

**Timing is honest.** Scrape (5s) + poll (5s) + River needing a few samples means
detection lands ~15–30s after `/break`. `chaos.sh` narrates this; we show real
latency, not fake instant magic.

**The approve action is real.** Governance already has
`POST /approvals/{id}/decide`. The frontend's approve button (currently a
`setTimeout` fake) POSTs to it for real. Approval ids are deterministic
(`appr-{situation_id}`), so the read-model does **not** need to tap an approval
event — the frontend derives the id from the situation.

## Cross-container HITL gap (must fix — discovered in spec review)

**Problem.** The HITL approval loop does not work across containers as currently
wired. `services/action/app.py` constructs `InProcessGovernanceGate(..., {}, ...)`
with a **fresh empty dict that lives inside the action container**. Governance has
its **own** `app.state.approvals = {}` inside the governance container. They are
two different dicts in two different processes. When an operator approves via
governance's `POST /approvals/{id}/decide`, that writes governance's dict — but
action's `await_decision` polls its *own* dict, which nobody updates. In compose,
**every HITL remediation times out** (`aborted:timeout`) after 30s. The
end-to-end tests pass only because they wire a single shared gate in one process;
the deployed topology has this latent gap. The live HITL demo — the frontend's
Approve button, the centerpiece interaction — cannot work until this is closed.

**Fix (already anticipated: the gate docstring says "An HTTP gate is a deferred
alternative").** Build that HTTP gate now:

- `services/action/adapters/governance_gate.py` — add `HttpGovernanceGate`
  implementing the same interface `remediate.py` already uses (no change to
  `remediate.py`): `request_approval` POSTs to governance `/approvals`;
  `await_decision` polls governance `GET /approvals/{id}` until non-pending or
  timeout; `check_rbac` / `write_audit` call governance's existing REST endpoints
  (`POST /rbac/check`, `POST /audit`). Select it via an env switch
  (`INTELLIOPS_GOVERNANCE_MODE=in_process|http`, default `in_process` so tests are
  untouched; compose sets `http`).
- `services/governance/app.py` — add `GET /approvals/{approval_id}` (single) and
  `GET /approvals` (list pending), so the HTTP gate can poll and the dashboard can
  show the queue. TDD'd.

**Accepted behavior (not re-architected):** `await_decision` blocks action's
consumer thread polling for up to the HITL timeout while waiting for a human. Fine
at demo scale (one incident at a time); noted, not changed.

## Read-model store

An in-memory projection, rebuilt from the stream on startup:

- Keyed by `situation_id`. Updated on each event:
  - `situations.detected` → create/update (status=detected, members, severity).
  - `situations.diagnosed` → merge ranked hypotheses + status=diagnosed.
  - approval request → attach `approval_id` + pending flag.
  - `remediation.outcomes` → terminal status (resolved/failed) + append to outcomes.
- On startup the consumer reads the stream from `id="0"` and replays to reconstruct
  state, then tails live. A restart loses nothing — it re-derives.

**Why in-memory, not persisted:** Redis Streams already durably hold every event
(consumer groups). The read-model is a *projection* — a rebuildable cache, not a
system of record. Persisting it would create a second source of truth that can
drift. The existing file-backed stores (audit, training) are systems of record and
stay exactly as they are; this line matches how the system already separates
records from views.

**Bounded memory:** the outcomes list is capped (~200 most recent). Situations are
keyed by id, so they bound to the number of distinct incidents. Replaying from
`id="0"` is fine at demo scale; a one-line comment notes snapshot+trim as the
scale-out path (not built — YAGNI).

## Error handling & operations

- **Startup ordering.** `PrometheusSource.poll()` catches connection errors and
  empty results, returns `[]`; the loop retries next tick with a single
  "waiting for Prometheus" log (not one per failed poll). The read-model replays
  from `id="0"`; empty stream → just tails. GET endpoints return empty arrays,
  never 500, until data arrives — the frontend renders empty states gracefully.
  Compose keeps the Redis healthcheck and adds healthchecks for Prometheus and
  demo-app so `depends_on` is meaningful.
- **CORS.** The frontend (Vite on :5173) calls services on :8001–8007. Add
  FastAPI `CORSMiddleware` in the shared `create_app` factory — one change covers
  all services. Scoped to localhost origins, not `*`.
- **Env switches** (new `Settings` fields, existing defaults untouched):
  - `INTELLIOPS_TELEMETRY_MODE=file|prometheus` (default `file`; compose sets
    `prometheus`).
  - `INTELLIOPS_PROMETHEUS_URL=http://prometheus:9090` (compose).
  - `read` store settings as needed.
- **Dry-run safety stays enforced.** Remediator = `DryRunRemediator`,
  health = `AlwaysHealthyChecker`; the three gates (ADR-003/007/008) stay in the
  call path. Even pointed at something real by accident, it cannot act.

**Ports (all free, all local):**

| Service | Host port |
|---|---|
| demo-app | 8080 |
| Prometheus | 9090 |
| ingestion / correlation / rca / action / governance / feedback | 8001–8006 |
| read (new) | 8007 |
| frontend (Vite) | 5173 |

Every image (`redis:7-alpine`, `prom/prometheus`, the Python services) is free and
local. `docker compose up` is the entire cost.

## Testing

TDD, matching the existing "every adapter has a test double / tests bind fakes"
style. Existing 60+ tests must stay green (guaranteed by the `file` default).

- **`PrometheusSource`** — fake HTTP client returning canned Prometheus API JSON;
  assert correct `TelemetryEvent`s, and that connection errors / empty results
  return `[]` (never raise). No real Prometheus in the test.
- **Read-model projection** — feed a scripted event sequence (detected → diagnosed
  → outcome) through the projection; assert store state and that
  `GET /situations` / `GET /outcomes` return the right shapes. Pure function over
  events, no infra.
- **`HttpGovernanceGate`** — fake HTTP client; assert `request_approval` POSTs,
  `await_decision` polls `GET /approvals/{id}` and returns on non-pending / times
  out on still-pending. Assert `GOVERNANCE_MODE=in_process` still uses the shared
  in-process gate.
- **Governance approval reads** — assert `GET /approvals/{id}` returns a created
  approval and 404s on unknown; `GET /approvals` lists pending.
- **Ingestion poll loop** — with a fake source, assert the lifespan thread
  publishes to `telemetry.raw`; assert `TELEMETRY_MODE=file` still uses the old
  path.
- **CORS** — assert the middleware is present on a service app.
- **Full live loop** — verified by hand via `chaos.sh` (8-container integration is
  not a unit test); each new *unit* is TDD'd.

## Concrete change list

**New files:**
- `services/demo_app/app.py`, `__init__.py`, `tests/`
- `services/ingestion/adapters/prometheus_source.py` (+ test)
- `services/read/app.py`, `projection.py`, `consumer.py`, `adapters/store.py`,
  `__init__.py`, `tests/`
- `deploy/prometheus.yml`
- `frontend/src/data/api.ts`
- `frontend/.env.example`
- `scripts/chaos.sh`
- `docs/superpowers/specs/2026-08-15-real-data-live-stack-design.md` (this file)

**Modified files:**
- `services/base.py` — add `CORSMiddleware`
- `services/ingestion/app.py` — add lifespan poll loop (mode-switched)
- `services/action/adapters/governance_gate.py` — add `HttpGovernanceGate`
- `services/action/app.py` — select gate via `GOVERNANCE_MODE` env switch
- `services/governance/app.py` — add `GET /approvals/{id}` + `GET /approvals`
- `common/config.py` — add `telemetry_mode`, `prometheus_url`, `governance_mode`,
  `governance_url`, read-store settings
- `deploy/docker-compose.yml` — add `demo-app`, `prometheus`, `read` + healthchecks
- `frontend/src/data/*` — toggle mock ↔ api
- `pyproject.toml` — add `prometheus-client`
- `README.md` — "Run it live" section + dry-run safety note

**Rough size:** ~11 new files, ~10 edits. Sequenced so existing tests never break
(the `file` / `in_process` defaults are the safety net).
