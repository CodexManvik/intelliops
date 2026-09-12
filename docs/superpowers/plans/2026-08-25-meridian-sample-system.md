# Meridian — Sample Production System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build **Meridian** — a Deloitte-style enterprise financial/audit reporting platform (4 backend services + a client portal UI + an ops panel) that runs in the compose stack, serves real traffic, emits real metrics, and fails in genuine injectable ways so IntelliOps genuinely detects → diagnoses → gates → remediates each incident.

**Architecture:** `services/meridian/<svc>/app.py` FastAPI services (built via `services.base.create_app`, auto-baked into the shared image) emit a toggleable `cpu_usage` gauge + `meridian_error_rate` + `/admin/fault`. Additive test-safe IntelliOps wiring (per-service Prometheus scrape jobs, a demo-only broadened ingestion query, a shared `deploys.json` volume for the rollback path) makes Meridian's faults real. A gateway service serves a distinct Vite+React UI (client portal + ops panel) via StaticFiles.

**Tech Stack:** FastAPI + prometheus-client + SQLAlchemy/Alembic (all present), Vite + React 18 + TS + Tailwind 3 (new UI app), a multi-stage `Dockerfile.meridian`.

**Spec:** [docs/superpowers/specs/2026-08-25-meridian-sample-system-design.md](../specs/2026-08-25-meridian-sample-system-design.md)

## Global Constraints

- **Test-safe by default.** IntelliOps' existing suite, base `docker compose up`, and CI must be unaffected. The ingestion query DEFAULT in `common/config.py` stays `cpu_usage`; all Meridian wiring is additive (new scrape jobs, compose-env query override, new services/volumes).
- **Gates:** `uv run pytest -m "not postgres and not kafka"` green; `uv run ruff check` + `ruff format --check .` clean; `npm run build` clean for the Meridian UI.
- **No new Python deps** (all present). Meridian UI is a new Vite app with its own `package.json`.
- **SEQUENTIAL FAULT INJECTION is a hard requirement** — correlation groups anomalies by a 15s TIME WINDOW, not by service, so concurrent faults MERGE into one situation. The ops UI enforces one active fault at a time (recover before the next); the demo doc states it.
- **Detection needs a runtime TOGGLE** — the z-score baseline is keyed on metric NAME (all `cpu_usage` series share one baseline). Each service starts healthy (`cpu_usage`=18.0) and a fault TOGGLES it to 92.0. A service pinned high from boot never spikes.
- **Meridian tables register on `common/db.py`'s `METADATA`** (alembic `env.py` uses `target_metadata = METADATA`), so the existing one-shot `migrate` creates them — no `env.py` edit needed if tables attach to `METADATA`. Use a `meridian` schema (or `meridian_`-prefixed table names) to avoid collisions.
- **Commit trailer:** every commit ends with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## Task 1: Backend services scaffold (4 services + fault mechanism + models)

**Files:**
- Create: `services/meridian/__init__.py`, `services/meridian/common.py` (shared `FaultSpec`, gauge/fault helpers)
- Create: `services/meridian/gateway/__init__.py` + `app.py`, `.../validation/app.py`, `.../aggregation/app.py`, `.../reporting/app.py`
- Create: `services/meridian/models.py` (SQLAlchemy `Table`s on `common.db.METADATA`, `meridian_` prefixed)
- Test: `services/meridian/tests/test_fault.py`, `test_metrics.py`, `test_services.py`

**Interfaces:**
- Produces: 4 FastAPI apps exporting `app`, each with `/metrics` (cpu_usage + meridian_error_rate gauges), `/admin/fault`, `/admin/clear`, real domain endpoints. `FaultSpec` model. Meridian tables on METADATA.
- Consumes: `services.base.create_app`, `common.auth.require_token`, `prometheus_client`.

- [ ] **Step 1: Shared fault mechanism (`services/meridian/common.py`) — write tests first**

`test_fault.py`: a `MeridianState` starts healthy (cpu=18.0, error_rate=0.0); `apply_fault(saturation)` sets cpu=92.0; `apply_fault(error, magnitude=0.5)` sets error_rate=0.5; `clear()` resets all; `crash` sets `unhealthy=True`. Then implement:

```python
from __future__ import annotations
from pydantic import BaseModel

CPU_HEALTHY = 18.0
CPU_BROKEN = 92.0

class FaultSpec(BaseModel):
    type: str            # "saturation" | "error" | "latency" | "crash"
    magnitude: float = 1.0
    duration_seconds: float | None = None

class MeridianState:
    def __init__(self) -> None:
        self.cpu = CPU_HEALTHY
        self.error_rate = 0.0
        self.latency_ms = 0.0
        self.unhealthy = False

    def apply(self, spec: FaultSpec) -> None:
        if spec.type == "saturation":
            self.cpu = min(100.0, CPU_BROKEN * spec.magnitude)
        elif spec.type == "error":
            self.error_rate = min(1.0, spec.magnitude)
            self.cpu = CPU_HEALTHY          # keep cpu at baseline so RCA maps to restart-pod, NOT scale-service
        elif spec.type == "latency":
            self.latency_ms = 200.0 * spec.magnitude
            self.cpu = CPU_BROKEN           # latency also drives cpu -> scale-service
        elif spec.type == "crash":
            self.unhealthy = True

    def clear(self) -> None:
        self.cpu = CPU_HEALTHY; self.error_rate = 0.0; self.latency_ms = 0.0; self.unhealthy = False
```

- [ ] **Step 2: Run fault tests → fail → implement → pass**

Run: `uv run pytest services/meridian/tests/test_fault.py -v` (fail → implement common.py → pass).

- [ ] **Step 3: A service factory (`services/meridian/common.py`) that builds a Meridian service app**

```python
import time
from fastapi import Depends, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest
from common.auth import require_token
from services.base import create_app

def make_meridian_service(name: str, domain_routes=None):
    app = create_app(name)
    state = MeridianState()
    app.state.meridian = state
    # Bare gauges (no `service` label) — each service is its own process, and the
    # Prometheus scrape job injects the `service` label. Do NOT add a label here.
    cpu = Gauge("cpu_usage", "Simulated CPU utilization percent")
    err = Gauge("meridian_error_rate", "Simulated request error rate 0..1")

    @app.get("/metrics")
    def metrics() -> Response:
        cpu.set(state.cpu); err.set(state.error_rate)
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/admin/fault", dependencies=[Depends(require_token)])
    def fault(spec: FaultSpec) -> dict:
        state.apply(spec)
        return {"applied": spec.type, "cpu": state.cpu, "error_rate": state.error_rate}

    @app.post("/admin/clear", dependencies=[Depends(require_token)])
    def clear() -> dict:
        state.clear(); return {"cleared": True}

    # latency + error injection on real domain traffic:
    @app.middleware("http")
    async def _inject(request: Request, call_next):
        if state.latency_ms and request.url.path.startswith("/api") or (request.url.path not in ("/metrics", "/health", "/ready")):
            if state.latency_ms:
                time.sleep(state.latency_ms / 1000.0)
        return await call_next(request)

    if domain_routes:
        domain_routes(app, state)
    return app
```
> NOTE: the `cpu = Gauge(...)` line above has a leftover ternary — the implementer writes the plain `Gauge("cpu_usage", ...)` form. Prometheus gauges are process-global; since each service is its OWN process/container, a bare `cpu_usage` gauge per service is correct (the scrape job's `service` label distinguishes them). Do NOT add a `svc` label on the gauge (the scrape label owns `service`).

**IMPORTANT — one gauge registry per process:** each Meridian service is a separate container, so defining `Gauge("cpu_usage")` in each is fine. But if tests import multiple services into ONE process, `prometheus_client`'s default registry raises "Duplicated timeseries". Mitigate in tests by using a fresh `CollectorRegistry` per service, OR test services in isolation. The plan's tests import ONE service per test module to avoid this.

- [ ] **Step 4: The 4 service `app.py` modules**

Each is thin — `services/meridian/aggregation/app.py`:
```python
from services.meridian.common import make_meridian_service

def _routes(app, state):
    @app.post("/aggregate")
    def aggregate(payload: dict) -> dict:
        # simulate roll-up work; real endpoint so traffic flows
        return {"aggregated": True, "rows": len(payload.get("rows", []))}

app = make_meridian_service("meridian-aggregation", _routes)
```
Similarly: `validation/app.py` (`POST /validate`), `reporting/app.py` (`POST /report`), `gateway/app.py` (`POST /api/submissions`, `GET /api/reports` — the gateway also gets the ops-proxy + `/admin/deploy` + UI mount in Task 3; here just the domain routes + the shared service scaffold).

- [ ] **Step 5: Meridian models on METADATA (`services/meridian/models.py`)**

```python
from sqlalchemy import Table, Column, BigInteger, String, DateTime, Float
from common.db import METADATA

meridian_submissions = Table(
    "meridian_submissions", METADATA,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("client", String, nullable=False),
    Column("period", String, nullable=False),
    Column("status", String, nullable=False),
    Column("amount", Float, nullable=False),
    Column("ts", DateTime(timezone=True), nullable=False),
)
meridian_reports = Table(
    "meridian_reports", METADATA,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("submission_id", BigInteger, nullable=False),
    Column("summary", String, nullable=False),
    Column("ts", DateTime(timezone=True), nullable=False),
)
```
Add an Alembic migration `alembic/versions/0004_meridian.py` (`down_revision` = the current head — verify with `ls alembic/versions/`; likely `0003_model_artifacts` after Stream B) that `create_table`s these two. Tables attach to `METADATA` so `target_metadata` picks them up; the migration makes it explicit for the `migrate` service.

- [ ] **Step 6: Service + metrics tests**

`test_metrics.py`: each service's `/metrics` returns Prometheus text containing `cpu_usage`; after `POST /admin/fault {"type":"saturation"}` the exposed `cpu_usage` is 92.0; after `/admin/clear` it's 18.0; error fault sets `meridian_error_rate` and keeps `cpu_usage` at 18.0. `test_services.py`: the domain endpoints return 200. Use `TestClient` without `with` (no lifespan) and one service per module (gauge-registry isolation).

- [ ] **Step 7: Run all Meridian tests + full fast suite + lint; commit**

Run: `uv run pytest services/meridian/tests/ -q && uv run pytest -m "not postgres and not kafka" -q && uv run ruff check services/meridian/ && uv run ruff format --check services/meridian/`
Expected: Meridian tests pass, full suite unaffected, lint clean.

```bash
git add services/meridian/ alembic/versions/0004_meridian.py
git commit -m "feat(meridian): 4 backend services + toggleable fault mechanism + models

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Compose + Prometheus + ingestion wiring (make IntelliOps observe Meridian)

**Files:**
- Modify: `deploy/prometheus.yml` (4 additive scrape jobs)
- Modify: `deploy/docker-compose.yml` (4 meridian-* backend services 8010–8013; the shared `rca-context` volume on rca + gateway; the demo ingestion query override; ingestion depends on prometheus already)
- Test: no unit test (config); verified by a compose-up + curl smoke (controller-run in Task 5, plus a config-validate here)

**Interfaces:** Consumes Task 1's services. Produces the observability path (Meridian metrics → detected).

- [ ] **Step 1: Prometheus scrape jobs (additive)**

Append to `deploy/prometheus.yml` `scrape_configs` (keep the demo-app job), one per backend service, each with a unique `service:` label:
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

- [ ] **Step 2: Meridian backend compose services (8010–8013)**

Add 4 blocks using the `<<: *service` anchor (DB-using ones restate the full `depends_on`). The gateway comes in Task 3 (it needs the UI Dockerfile); here add validation/aggregation/reporting + a DB-less gateway placeholder OR defer gateway entirely to Task 3. Example (aggregation, DB-backed via the meridian schema — but these services are mostly stateless; only gateway writes submissions, so validation/aggregation/reporting can be **bus/stateless** blocks like `read`):
```yaml
  meridian-aggregation:
    <<: *service
    environment:
      INTELLIOPS_REDIS_URL: redis://redis:6379
      INTELLIOPS_LOG_FORMAT: json
      SERVICE_MODULE: services.meridian.aggregation.app:app
      PORT: "8000"
    ports: ["8012:8000"]
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)\""]
      interval: 10s
      timeout: 3s
      retries: 5
      start_period: 20s
```
(validation → 8011, reporting → 8013. Use `/health` in the healthcheck — bus-ping-free, so no redis dependency surprise. gateway → 8008 in Task 3.)

- [ ] **Step 3: The rollback path — shared `rca-context` volume + ingestion query override**

Add a named volume `rca-context` and mount it on BOTH the `rca` service and (Task 3) the gateway at `/app/data/rca_context` (the container's `rca_context_path`; confirm the working dir — `data/rca_context` relative to `/app`). On the `rca` service add:
```yaml
    volumes:
      - rca-context:/app/data/rca_context
```
And under top-level `volumes:` add `rca-context:`. Set the demo ingestion query on the `ingestion` service env (override the default, do NOT change `common/config.py`):
```yaml
      INTELLIOPS_PROMETHEUS_QUERY: '{__name__=~"cpu_usage|meridian_error_rate"}'
```

- [ ] **Step 4: Validate compose config + verify the query is an instant vector**

Run: `docker compose -f deploy/docker-compose.yml config >/dev/null && echo "compose valid"` (config parses). Verify the regex query returns an instant vector: the plan's Task 5 does the live check, but note here — if `{__name__=~"..."}` fails to URL-encode or returns a non-vector, fall back to the multi-query `_make_source` enhancement (documented in the spec). The controller confirms this live in Task 5.

- [ ] **Step 5: Commit**

```bash
git add deploy/prometheus.yml deploy/docker-compose.yml
git commit -m "feat(meridian): wire IntelliOps to observe Meridian (scrape jobs, query, rca-context volume)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: The gateway (ops proxy + /admin/deploy + UI serve) + Dockerfile.meridian

**Files:**
- Modify: `services/meridian/gateway/app.py` (ops proxy, `/admin/deploy` writes deploys.json, StaticFiles mount)
- Create: `deploy/Dockerfile.meridian` (multi-stage node build → python image)
- Modify: `deploy/docker-compose.yml` (the `meridian-gateway` service block, port 8008, its own Dockerfile, the rca-context volume mount)
- Test: `services/meridian/tests/test_gateway.py`

**Interfaces:** Produces the gateway that proxies faults to the 4 services, writes deploy markers, and serves the UI (built in Task 4).

- [ ] **Step 1: Gateway ops proxy + /admin/deploy (write tests first)**

`test_gateway.py`: `POST /api/ops/fault {service, spec}` proxies to `http://meridian-<service>:8000/admin/fault` (mock httpx); `POST /api/ops/deploy {service}` writes `data/rca_context/deploys.json` with `[{"service": "...", "version": "...", "ts": "..."}]`. Then implement in `gateway/app.py` (extend the Task 1 gateway):
```python
import json, os
from datetime import UTC, datetime
import httpx
from fastapi import Depends
from common.auth import require_token

_MERIDIAN_SERVICES = {"gateway", "validation", "aggregation", "reporting"}
_RCA_CONTEXT = os.environ.get("INTELLIOPS_RCA_CONTEXT_PATH", "data/rca_context")

@app.post("/api/ops/fault")
def ops_fault(body: dict) -> dict:
    svc = body["service"]; spec = body["spec"]
    url = f"http://meridian-{svc}:8000/admin/fault"
    with httpx.Client(timeout=5.0) as c:
        r = c.post(url, json=spec)          # demo-app-style targets are un-tokenized; server-side call
    return {"status": r.status_code}

@app.post("/api/ops/deploy")
def ops_deploy(body: dict) -> dict:
    svc = f"meridian-{body['service']}"
    os.makedirs(_RCA_CONTEXT, exist_ok=True)
    path = os.path.join(_RCA_CONTEXT, "deploys.json")
    entry = {"service": svc, "version": body.get("version", "v2.3.1"), "ts": datetime.now(UTC).isoformat()}
    json.dump([entry], open(path, "w"))
    return {"deployed": svc}
```
> The gateway proxies ops calls server-side so the browser never holds a token and stays same-origin.

- [ ] **Step 2: StaticFiles mount (mount LAST)**

At the END of `gateway/app.py` (after all `/api`, `/admin`, `/metrics` routes):
```python
from pathlib import Path
from fastapi.staticfiles import StaticFiles
_ui = Path(__file__).parent.parent / "ui" / "dist"
if _ui.exists():                            # exists only after the UI is built (Task 4)
    app.mount("/", StaticFiles(directory=str(_ui), html=True), name="ui")
```
Guard on `.exists()` so the gateway runs in tests/CI before the UI is built.

- [ ] **Step 3: `deploy/Dockerfile.meridian` (multi-stage)**

```dockerfile
FROM node:20-slim AS ui
WORKDIR /ui
COPY services/meridian/ui/package*.json ./
RUN npm ci
COPY services/meridian/ui/ ./
RUN npm run build          # -> /ui/dist

FROM python:3.11-slim
# mirror deploy/Dockerfile's uv setup (copy the same install steps)
WORKDIR /app
# ... uv sync / copy common, services, policies as deploy/Dockerfile does ...
COPY --from=ui /ui/dist ./services/meridian/ui/dist
ENV SERVICE_MODULE=services.meridian.gateway.app:app
CMD ["sh", "-c", "uvicorn \"$SERVICE_MODULE\" --host 0.0.0.0 --port \"$PORT\""]
```
> Read `deploy/Dockerfile` and replicate its exact uv/copy steps for the python stage — do not guess.

- [ ] **Step 4: The gateway compose service (8008, own Dockerfile, rca-context volume)**

```yaml
  meridian-gateway:
    build: { context: .., dockerfile: deploy/Dockerfile.meridian }
    environment:
      INTELLIOPS_REDIS_URL: redis://redis:6379
      INTELLIOPS_LOG_FORMAT: json
      SERVICE_MODULE: services.meridian.gateway.app:app
      PORT: "8000"
      INTELLIOPS_RCA_CONTEXT_PATH: /app/data/rca_context
    ports: ["8008:8000"]
    volumes:
      - rca-context:/app/data/rca_context
    depends_on:
      redis: { condition: service_healthy }
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)\""]
      interval: 10s
      timeout: 3s
      retries: 5
      start_period: 30s
```

- [ ] **Step 5: Run gateway tests + full suite + lint; compose config valid; commit**

Run: `uv run pytest services/meridian/tests/ -q && uv run ruff check services/meridian/ && docker compose -f deploy/docker-compose.yml config >/dev/null`

```bash
git add services/meridian/gateway/app.py deploy/Dockerfile.meridian deploy/docker-compose.yml services/meridian/tests/test_gateway.py
git commit -m "feat(meridian): gateway ops-proxy + /admin/deploy + StaticFiles UI serve + Dockerfile.meridian

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: The Meridian UI (client portal + ops panel, distinct theme)

**Files:**
- Create: `services/meridian/ui/` — a Vite+React+TS+Tailwind app (`package.json`, `vite.config.ts`, `tsconfig.json`, `tailwind.config.js`, `index.html`, `src/`)
- Test: `npm run build` clean (strict TS)

**Interfaces:** Consumes the gateway's `/api/*` (same-origin). Produces the built `dist/` the gateway serves.

- [ ] **Step 1: Scaffold the Vite app (its OWN theme from commit 1)**

Mirror `frontend/`'s toolchain versions (react ^18.3, vite ^8, tailwind ^3.4, ts ^5.6; scripts `dev`/`build`/`preview`). `tailwind.config.js` uses Meridian's OWN palette — NOT the console's: light enterprise-fintech (white / `#F7F8FA` surfaces, ink `#0B1220`, brand accent emerald `#0E7C5A` or navy `#1B2A4A` — **not cyan**), a grotesk/serif pairing (not Geist). Vite `base: "/"`.

- [ ] **Step 2: `src/data/api.ts` — same-origin gateway client**

```ts
const API = import.meta.env.VITE_API_URL ?? "";   // empty → relative → same-origin
export const submitData = (body) => fetch(`${API}/api/submissions`, {method:"POST", headers:{"content-type":"application/json"}, body: JSON.stringify(body)}).then(r=>r.json());
export const loadReports = () => fetch(`${API}/api/reports`).then(r=>r.json());
export const injectFault = (service, spec) => fetch(`${API}/api/ops/fault`, {method:"POST", headers:{"content-type":"application/json"}, body: JSON.stringify({service, spec})}).then(r=>r.json());
export const deploy = (service) => fetch(`${API}/api/ops/deploy`, {method:"POST", headers:{"content-type":"application/json"}, body: JSON.stringify({service})}).then(r=>r.json());
export const clearFault = (service) => fetch(`${API}/api/ops/clear`, {method:"POST", headers:{"content-type":"application/json"}, body: JSON.stringify({service})}).then(r=>r.json());
```

- [ ] **Step 3: Client portal views**

A top-app-bar + left-nav shell (NOT the console's floating nav). Views: **Dashboard** (submissions, period status, aggregate figures, recent reports), **Submit** (a form → `submitData` → real traffic), **Reports** (list + "generate"). A **background-traffic toggle** that fires periodic `submitData`/`loadReports` so metrics stay live. Enterprise-clean styling, its own theme.

- [ ] **Step 4: Ops / SRE panel (with the sequential-injection guard)**

A distinct "Operations" view: **4 scenario preset buttons** (each = a fixed `{service, spec}` + optional deploy), the **custom-fault composer** (target select · type select · magnitude slider · duration · deploy checkbox → `injectFault`/`deploy`), a **live service-status strip** (poll each service's health via the gateway, show healthy/degraded), **Clear all**. **ENFORCE sequential injection:** track an `activeFault` state; disable "inject" while one is active until "clear" is pressed (and show a "wait ~15s for the window to close" hint). This is the load-bearing UX constraint.

- [ ] **Step 5: `npm run build` clean**

Run: `cd services/meridian/ui && npm install && npm run build`
Expected: clean (strict TS), produces `dist/`.

- [ ] **Step 6: Commit**

```bash
git add services/meridian/ui/
git commit -m "feat(meridian): client portal + ops panel UI (distinct enterprise theme, sequential-fault guard)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: End-to-end demo verification (controller-run, real Docker)

**Files:** none (verification) — may add `scripts/meridian-demo.sh` if a scripted driver helps.

- [ ] **Step 1: Bring the full stack up**

`docker compose -f deploy/docker-compose.yml up -d --build` (+ the auth overlay optionally). Confirm all services healthy incl. the 4 meridian-* + gateway; the portal loads at http://localhost:8008.

- [ ] **Step 2: Verify the ingestion regex query returns an instant vector (the Task 2 risk)**

`curl 'http://localhost:9090/api/v1/query?query={__name__=~"cpu_usage|meridian_error_rate"}'` → confirm a `resultType: vector` with `cpu_usage` + `meridian_error_rate` series carrying `service: meridian-*` labels. If it fails, apply the multi-query `_make_source` fallback (spec) — this is the one place the design could need a small ingestion code change.

- [ ] **Step 3: Run each scenario SEQUENTIALLY, verify the full loop**

For each of the 4 (one at a time, recover + wait >15s between): fire the fault → watch the Meridian status strip degrade → switch to the IntelliOps console (or curl read `/situations`) → confirm a situation detected with the EXPECTED diagnosis (aggregation/reporting→scale-service, validation→restart-pod, gateway→rollback-deploy) → HITL gate → approve → outcome → Meridian recovers. Capture evidence (the situations JSON + outcomes).

- [ ] **Step 4: Verify the custom-fault builder + the sequential guard**

Compose a custom fault (e.g. validation error 0.4) → confirm detection. Confirm the UI blocks a second concurrent inject until clear.

- [ ] **Step 5: Tear down; record the verified results for Task 6**

Capture the real per-scenario evidence (which diagnosis, MTTR, recovered) to the ledger/scratchpad for `docs/MERIDIAN.md`.

---

## Task 6: Docs + ADR-020

**Files:**
- Create: `docs/MERIDIAN.md`
- Modify: `architectural.md` (ADR-020), `README.md`, `flow.md`, `WORKPLAN.md`

- [ ] **Step 1: `docs/MERIDIAN.md`**

The system (services, the domain, the UI), how it's wired to IntelliOps (scrape jobs, query, deploys.json volume), the **4 scenarios + expected diagnoses** (with the real evidence from Task 5), the **custom-fault builder**, the **SEQUENTIAL-injection requirement** (why — the 15s window merge — stated honestly), the demo script (the money-shot flow), and honest limits (synthetic data; toggle-based faults; which fault types map to a playbook vs detection-only).

- [ ] **Step 2: ADR-020 + README/flow/WORKPLAN**

`architectural.md` — **ADR-020 — Meridian sample production system** (verify next number is 020; match ADR structure): the design, the additive-observability-wiring decision, the time-window-not-service constraint, and honest limits. `README.md` — Meridian section + link to MERIDIAN.md + ADR count → 20. `flow.md` — a note that a sample system feeds the pipeline. `WORKPLAN.md` — the sample-system effort shipped.

- [ ] **Step 3: Commit**

```bash
git add docs/MERIDIAN.md architectural.md README.md flow.md WORKPLAN.md
git commit -m "docs(meridian): MERIDIAN.md (demo script + real results), ADR-020, README/flow/WORKPLAN

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-review notes (author)

- **Spec coverage:** all 8 acceptance criteria map — one-command up (T2/T3 compose), real traffic (T4 portal+simulator), 4 scenarios with expected diagnoses (T5 verified), custom builder (T4/T5), sequential enforcement (T4 guard + T5 verify), IntelliOps default unchanged (T1/T2 additive + full-suite gate each task), distinct UI (T4 theme), docs+ADR (T6).
- **Verified constraints baked in:** toggle-based `cpu_usage` (name-keyed baseline); error-fault keeps cpu at baseline (so restart-pod, not scale-service); sequential injection (window-merge); the rca-context volume (rollback path isn't config-free today); the regex query stays an instant vector (T5 Step 2 verifies live, with the multi-query fallback named); models on METADATA (no env.py edit); gateway own-Dockerfile + /health healthcheck (bus-ping avoidance).
- **Risk control:** T5 Step 2 is the single riskiest external unknown (the regex query) — verified live before relying on it, with a named fallback. The gauge-registry duplication hazard is handled (one service per test module). The gateway StaticFiles mount is `.exists()`-guarded so CI (no UI build) doesn't break.
- **YAGNI:** validation/aggregation/reporting are stateless (bus-less even) — only the gateway touches the DB (submissions/reports). No second Postgres/Redis. No new playbook (existing `${service}`-templated ones suffice).
