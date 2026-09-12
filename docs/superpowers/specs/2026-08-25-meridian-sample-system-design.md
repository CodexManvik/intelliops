# Meridian — Sample Production System — Design Spec

**Date:** 2026-08-25
**Owner (this effort):** Manvik (integration lead)
**Status:** design approved in brainstorming; four hard integration unknowns research-verified with an adversarial pass; ready for an implementation plan.

## Goal

Build **Meridian** — a Deloitte-style enterprise **financial/audit reporting platform** (a realistic multi-service app with its own web UI) that runs alongside IntelliOps in the compose stack, serves real traffic, emits real Prometheus metrics, and fails in genuine, injectable ways — so IntelliOps genuinely **detects → diagnoses → gates → remediates** Meridian's incidents. This is the "connect a real production system" demo the PPO panel sees: an enterprise platform Deloitte would *operate under managed services*, with IntelliOps as the AIOps layer keeping it healthy.

## The decisive design fact (verified — shapes everything)

IntelliOps today observes exactly **one metric (`cpu_usage`) scraped from one target (`demo-app`)**. The user chose **"make Meridian's faults genuinely real"**, so this spec includes the small **additive, test-safe** IntelliOps-side wiring to truly observe Meridian. Three verified realities constrain the design:

1. **Detection needs a runtime toggle.** The correlation z-score baseline is keyed on metric *name* only (all `cpu_usage` series share one baseline). A service pinned "broken" from boot never spikes. Each Meridian service must emit a `cpu_usage` gauge that **toggles healthy 18.0 → broken 92.0** at runtime (exactly the demo-app pattern).
2. **Correlation groups by TIME WINDOW, not by service** (`CorrelationEngine` buffers all anomalies in one 15s window → one Situation). **Concurrent faults on 2+ services merge into a single situation.** Therefore faults must be injected **one at a time, spaced > the correlation window (15s), each recovered before the next** — the fault-injection UI and the demo script must enforce this.
3. **Diverse diagnoses require engineering the trigger, not the value.** RCA maps by metric-name token + `service` label, not value: `cpu_usage` → `scale-service` (0.6); a recent-deploy match → `rollback-deploy` (0.8); an `error`-named metric or log event → `restart-pod` (0.5). `rollback-deploy` additionally needs a `deploys.json` mounted into the rca service (it isn't today).

## Non-goals

- No change to IntelliOps' **default** behavior. The ingestion query default stays `cpu_usage`; new scrape jobs and any query broadening are additive and don't affect the existing demo-app path or tests.
- No second Postgres, no second Redis. Meridian reuses the shared infra (a `meridian` schema in the existing DB).
- Meridian does **not** import from IntelliOps' domain logic beyond the shared platform utilities (`services.base.create_app`, `common.auth`, `common.config`, `common.db`).
- Not a real financial system — it's a realistic *sample* with synthetic data.

## Global Constraints

- **Test-safe by default.** IntelliOps' existing suite, `docker compose up` (base), and CI must be unaffected. Meridian runs in the compose stack; its own tests are self-contained.
- **Gates:** `uv run pytest -m "not postgres and not kafka"` green; `uv run ruff check` + `ruff format --check .` clean; `npm run build` clean for the Meridian UI.
- **No new Python deps** (all present: fastapi, uvicorn, prometheus-client, sqlalchemy, alembic, psycopg, httpx, pydantic). The Meridian UI is a new Vite app (its own `package.json`).
- **Sequential fault injection is a hard requirement** (see decisive fact #2) — the UI enforces one-fault-at-a-time with recovery between.
- **Commit trailer:** every commit ends with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Shared-file coordination:** additive edits to `deploy/prometheus.yml`, `deploy/docker-compose.yml`, `common/config.py` (a new default query optionally), `alembic/env.py` (register Meridian models); no change to existing IntelliOps service code beyond an optional additive ingestion multi-query enhancement.

---

## Architecture

```
  [Meridian Portal UI]  ──▶  meridian-gateway  ──▶  meridian-validation   (rule checks)
   (client + ops panel)      (FastAPI + serves UI) ──▶  meridian-aggregation (roll-ups; the heavy compute)
                                              │      ──▶  meridian-reporting   (report generation)
                                              └──▶  postgres (meridian schema — the client ledger)

  Each meridian-* service:  cpu_usage gauge (18↔92 toggle) + /admin/fault + /admin/deploy  +  /metrics
        │  scraped by Prometheus (per-service job + `service` label)
        ▼
  Prometheus ──▶ ingestion (query) ──▶ telemetry.raw ──▶ correlation ──▶ rca ──▶ action (remediation)
                                                                    (the existing IntelliOps loop, unchanged)
```

- **4 backend services** (gateway, validation, aggregation, reporting) + the **portal UI** served by the gateway + the shared **Postgres** (`meridian` schema).
- Each backend service is a `services/meridian/<svc>/app.py` module built via `services.base.create_app` (free `/health`, `/ready`, CORS, auth), auto-baked into the shared image (no Dockerfile edit for backends).
- The **gateway** additionally serves the built UI via FastAPI `StaticFiles` and proxies ops/fault calls, so the portal is same-origin (no CORS). It needs a small multi-stage `Dockerfile.meridian` (node build → copy `dist/` into the python image).

---

## Decision 1 — The Meridian backend services

Each service is a small FastAPI app that (a) serves real domain endpoints so traffic flows, (b) emits a `cpu_usage` gauge (+ an error-rate gauge for the restart-pod scenario), (c) exposes `/admin/fault` + `/admin/deploy` (token-gated), and (d) exposes `/metrics`.

| Service | Domain role | Real endpoints (driven by the portal) | Fault it showcases |
|---|---|---|---|
| `meridian-gateway` | API front door + serves the UI | `POST /api/submissions`, `GET /api/reports`, ops proxy | **rollback-deploy** (bad deploy) |
| `meridian-validation` | validates submitted financial data | `POST /validate` (called by gateway) | **restart-pod** (error spike) |
| `meridian-aggregation` | roll-ups / heavy compute | `POST /aggregate` | **scale-service** (saturation) |
| `meridian-reporting` | generates client reports | `POST /report` | **scale-service** (saturation) |

**Shared service scaffold** (each `services/meridian/<svc>/app.py`), mirroring `demo_app`:
```python
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest
from fastapi import Depends, Response
from common.auth import require_token
from services.base import create_app

app = create_app("meridian-<svc>")
_state = {"cpu": 18.0, "error_rate": 0.0, "broken": False}
_cpu = Gauge("cpu_usage", "Simulated CPU utilization percent")
_err = Gauge("meridian_error_rate", "Simulated request error rate 0..1")   # for restart-pod

@app.get("/metrics")
def metrics() -> Response:
    _cpu.set(_state["cpu"]); _err.set(_state["error_rate"])
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/admin/fault", dependencies=[Depends(require_token)])
def fault(spec: FaultSpec) -> dict:
    # latency → sleep per request; error → set error_rate + 5xx fraction;
    # saturation → _state["cpu"] = 92.0; crash → app.state.unhealthy = True
    ...

@app.post("/admin/clear", dependencies=[Depends(require_token)])
def clear() -> dict:
    _state.update(cpu=18.0, error_rate=0.0, broken=False); ...
```
**Verified constraints baked in:** the gauge starts at 18.0 (healthy) and the fault *toggles* it to 92.0 (a real step change the z-score flags). `crash` fails `/ready` (drives K8s remediation). Each service exposes the **same `cpu_usage` name** (so the single default ingestion query picks them all up) and the scrape job's `service` label distinguishes them.

**Packaging (verified):** each backend is a `<<: *service` compose block overriding `SERVICE_MODULE`/`PORT`/ports (8010–8013)/healthcheck; DB-using services restate the full `depends_on` (redis + postgres + migrate). Meridian models register into `alembic/env.py` `target_metadata` (a real edit) so the existing one-shot `migrate` creates the `meridian` schema tables.

---

## Decision 2 — Making IntelliOps genuinely observe Meridian (the additive wiring)

### 2a. Prometheus scrape (`deploy/prometheus.yml`, additive)
Add one job per Meridian service with a distinct `service:` label (the label RCA keys on):
```yaml
  - job_name: meridian-gateway
    static_configs: [{ targets: ["meridian-gateway:8000"], labels: { service: meridian-gateway } }]
  - job_name: meridian-validation
    static_configs: [{ targets: ["meridian-validation:8000"], labels: { service: meridian-validation } }]
  - job_name: meridian-aggregation
    static_configs: [{ targets: ["meridian-aggregation:8000"], labels: { service: meridian-aggregation } }]
  - job_name: meridian-reporting
    static_configs: [{ targets: ["meridian-reporting:8000"], labels: { service: meridian-reporting } }]
```
The existing `demo-app` job stays. Each job's `service` label is unique (or events merge in RCA).

### 2b. Ingestion query (to see the error metric for restart-pod)
Today ingestion polls a single fixed query `cpu_usage`. `scale-service` faults work with **zero** ingestion change (all Meridian `cpu_usage` series are already matched by the bare selector). To also ingest `meridian_error_rate` (for `restart-pod`), broaden the query to an instant-vector **selector** (not a function):
```
{__name__=~"cpu_usage|meridian_error_rate"}
```
- **The default in `common/config.py` stays `cpu_usage`** (test-safe). **Chosen for v1: the regex-selector-in-compose path** — set `INTELLIOPS_PROMETHEUS_QUERY: '{__name__=~"cpu_usage|meridian_error_rate"}'` in the compose env only. This is **zero IntelliOps code change** and verified to stay an instant vector (so `prometheus_source` gets `__name__` + a scalar value per series). The multi-query `_make_source` enhancement is a documented fallback only if the regex selector proves problematic (e.g. Prometheus URL-encoding). The plan must verify the regex query returns the expected instant vector before relying on it (a quick `curl` against Prometheus in Task 2).
- Note the merge subtlety: if a service emits BOTH a high `cpu_usage` AND `meridian_error_rate` in the same window, `scale-service` (0.6) outranks `restart-pod` (0.5). So the **validation** service's restart-pod fault sets `error_rate` high but keeps `cpu_usage` at baseline (18.0) — its incident carries only the error signal → `restart-pod`.

### 2c. The rollback-deploy path (`deploys.json` mount — verified NOT config-free today)
The rca service has no `data/rca_context` mount, so `recent_deploys()` is always empty and `rollback-deploy` can't fire. Fix (additive):
- Add a **shared named volume** mounted at `data/rca_context` on **both** the rca service (reads `deploys.json`) and the meridian-gateway (writes it via `/admin/deploy`).
- Meridian's **`/admin/deploy`** writes `data/rca_context/deploys.json` = `[{"service":"meridian-gateway","version":"v2.3.1","ts":...}]`. When the gateway's rollback scenario fires, it stamps a deploy then injects the fault → RCA sees the deploy match → `rollback-deploy` (0.8, outranks saturation).

### 2d. Playbooks
The existing `scale-service` / `restart-pod` / `rollback-deploy` playbooks use `${service}` templating, so they already target any service by name — **no new playbook required**. (Optional: a Meridian-flavored playbook name for demo polish, but not needed for the loop.)

---

## Decision 3 — Fault injection + scenarios (sequential, real)

### The fault mechanism (real, per-service)
`/admin/fault` takes a `FaultSpec = {type, magnitude, duration_seconds?}`:
- **`saturation`** → `cpu_usage` gauge → 92.0 (× magnitude). Real step change → detected → `scale-service`.
- **`error`** → `meridian_error_rate` → high + a fraction of real requests return 5xx. → `restart-pod`.
- **`latency`** → real `time.sleep(magnitude_ms)` per request (visible in the portal as slow reports) + drives cpu up. → `scale-service`.
- **`crash`** → `/ready` starts returning 503. → the K8s remediation / restart path.
- `duration_seconds` → auto-clear after N seconds (else clear on `/admin/clear`).

### The four scripted scenarios (each → distinct service + signature + playbook)
| Preset | Service | Injected | IntelliOps diagnoses → |
|---|---|---|---|
| "Aggregation job saturated" | aggregation | saturation (cpu→92) | **scale-service** |
| "Report generation slow" | reporting | latency + saturation | **scale-service** |
| "Validation errors spiking" | validation | error (error_rate high, cpu stays 18) | **restart-pod** |
| "Bad gateway deploy" | gateway | `/admin/deploy` marker + saturation | **rollback-deploy** |

### The custom-failure builder (your addition)
A UI composer: **target service · fault type · magnitude · duration · [deploy marker]** → posts `/admin/fault` (via the gateway proxy). Same real mechanism as the presets. **Honest about coverage:** the UI indicates which fault types map to a known playbook vs. would land in the low-confidence fallback (detection-only) — a truthful "here's where the model's coverage ends" moment.

### Sequential-injection enforcement (verified hard requirement)
Because correlation merges concurrent faults, the ops panel **enforces one active fault at a time**: firing a new fault is disabled until the current one is cleared/recovered (or a "clear all + wait 15s" affordance). The demo script runs faults sequentially with recovery between — this is stated in the UI and the demo doc, not left to chance.

---

## Decision 4 — The Meridian UI (its own product)

A **second Vite + React + TS + Tailwind app** under `services/meridian/ui/`, built and served by the gateway via `StaticFiles` (same-origin → no CORS for the portal). Two faces:

### A. Client portal (the "real product")
- **Dashboard** — submissions, reporting-period status, aggregate financials, recent reports.
- **Submit data** — a form that POSTs a financial submission through the gateway → drives real traffic through validation → aggregation → reporting (so metrics are live even when healthy).
- **Reports** — list + "generate report" (triggers the aggregation→reporting path).
- **Background traffic toggle** — a simulator so the platform always has realistic load (real baselines).

### B. Ops / SRE panel (the demo driver)
- The **4 scenario presets** (one click each).
- The **custom-fault composer** (Decision 3).
- A **live service-status strip** (5 services healthy/degraded/down) — see the fault take hold before switching to the IntelliOps console.
- **Clear all** + the sequential-injection guard.

### Visual language (structurally distinct from the console)
The IntelliOps console is dark instrument-panel + cyan + Geist. Meridian is a **light enterprise-fintech** look, its own `tailwind.config.js` from commit 1: white / `#F7F8FA` surfaces, ink `#0B1220`, a **non-cyan brand accent** (deep navy `#1B2A4A` or emerald `#0E7C5A`), a grotesk/serif pairing (not Geist), a top-app-bar + left-nav layout (not the floating fluid-island nav). So in the demo it's visually obvious: "this is the client's app" vs. "this is IntelliOps watching it."

### Serve mechanism (verified)
- `Dockerfile.meridian` (multi-stage: `node:20` builds `ui/dist` → copy into the `python:3.11-slim` image). The gateway service uses this Dockerfile (not the shared one, since it needs the node build) and sets `INTELLIOPS_REDIS_URL` itself + a **`/health`-based** healthcheck (not `/ready`, to avoid a bus-ping dependency).
- Gateway mounts `StaticFiles(directory="ui/dist", html=True)` at `/` **last** (so `/api/*`, `/health`, `/ready`, `/metrics`, `/admin/*` win). SPA deep links → a catch-all returning `index.html`, or keep the UI hash-routed.
- Portal fetches are relative (`VITE_API_URL ?? ""`) → same-origin. The gateway proxies ops/fault calls to the other services server-side (browser never holds a token; and demo-app-style targets are un-tokenized anyway).

---

## The demo flow (the money shot)

1. Meridian up, background traffic on, all services green — **"the enterprise platform we operate for the client."**
2. Ops panel fires ONE fault (preset or custom) → the status strip degrades; the client portal shows real symptoms (slow/failed reports).
3. Switch to the **IntelliOps console** → the live pipeline view (Stream C) animates: detect → diagnose → HITL gate.
4. Approve → IntelliOps remediates → the Meridian service recovers → portal healthy again.
5. Reset, and (respecting the 15s window) run the next scenario.

---

## Acceptance criteria

1. **Meridian runs in `docker compose up`** alongside IntelliOps (one command), all services healthy, portal reachable.
2. **Real traffic** flows through the services (portal submit / background simulator) → real baseline metrics.
3. **Each of the 4 scenarios**, injected via the ops panel, produces a **real** IntelliOps incident with the **expected diagnosis** (scale-service / restart-pod / rollback-deploy) and reaches the HITL gate → approve → remediate → recover.
4. **The custom-fault builder** composes an arbitrary fault against a chosen service and IntelliOps detects it (honest about detection-only vs full-loop coverage).
5. **Sequential injection is enforced** (no concurrent-fault merge surprises).
6. **IntelliOps default behavior is unchanged** — existing suite green, base compose + CI unaffected; the Meridian wiring is additive.
7. **The Meridian UI is visually distinct** from the console and looks like a credible enterprise product.
8. **`docs/MERIDIAN.md`** documents the system + the demo script; **ADR-020** records the design.

## Suggested task ordering (for the plan)

1. **Backend services scaffold** — 4 `services/meridian/<svc>/app.py` with `create_app`, `cpu_usage`+`error_rate` gauges, `/metrics`, `/admin/fault`+`/admin/clear`+`/admin/deploy`, real domain endpoints; a shared `FaultSpec`; the `meridian` schema models + `alembic/env.py` registration; their tests.
2. **Compose + Prometheus + ingestion wiring** — meridian-* compose blocks (8010–8013), the shared `data/rca_context` volume on rca + gateway, the per-service scrape jobs, the demo ingestion query (regex selector in compose OR the additive multi-query enhancement). Verify a fault is scraped→ingested→detected (compose smoke).
3. **The gateway + `Dockerfile.meridian` + UI serve** — gateway app (domain intake + ops proxy + `/admin/deploy` writing deploys.json + StaticFiles mount), the multi-stage Dockerfile, the compose service (8008), `/health` healthcheck.
4. **The Meridian UI** — the Vite app, its own theme, the client portal (dashboard/submit/reports + traffic toggle) and the ops panel (presets + custom composer + status strip + sequential guard).
5. **End-to-end demo verification** (controller-run, real Docker) — each scenario break→detect→diagnose→gate→approve→remediate→recover, sequentially; capture the evidence.
6. **Docs** — `docs/MERIDIAN.md` (system + demo script + the sequential-injection note + honest coverage), ADR-020, README/flow touch-ups.

Ordering rationale: backends first (nothing to observe without them), then the observability wiring (prove the loop sees a fault), then the gateway/UI serve, then the UI, then the live end-to-end proof, then docs from the real run.
