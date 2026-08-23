# IntelliOps CoE

**Agentic AIOps for automated incident detection, diagnosis & remediation.**

IntelliOps CoE ingests the telemetry your systems already produce, uses machine learning to
collapse alert storms into a handful of meaningful **Situations**, suggests likely root causes,
and — under strict human-in-the-loop governance — executes safe, reversible remediation. Every
outcome feeds back into the model, so the system gets more accurate the longer it runs.

It is built to **augment** your existing observability, CI/CD, and ticketing stack, not replace
it, and it is **open-source-first** to avoid vendor lock-in.

> **Project status: the closed loop runs live, end-to-end, with real remediation and durable
> state.**
> All six services plus a **read-model service** and a **React operator console** run on
> docker-compose — real Prometheus, a breakable demo target, the HITL approval gate working
> across containers, live KPIs, and a one-command scenario reset for repeatable demos.
> **Remediation is real** on an opt-in path: against a local **kind** cluster, approving a fix
> restarts/scales a real pod and a real health check verifies it (`REMEDIATOR_MODE=k8s`;
> `dry_run` stays the safe default). The compliance audit trail, the learning loop's training
> records, the playbook registry, and live runtime state (pending approvals + the detector's
> baseline) are **persisted to Postgres** and survive restarts (`STORE_BACKEND=postgres`).
> Edge auth (`AUTH_MODE=token`), a CI pipeline, and structured logging + `/ready` readiness
> probes are in place. **The console is now real-time**: the read-service pushes over SSE, a
> live **Pipeline** tab animates every incident through the closed loop as it happens, a new
> **Audit** tab makes the audit trail filterable, and the console was repainted to Apple's light
> website palette (see [docs/UI.md](docs/UI.md), [ADR-018](architectural.md#adr-018--real-time-console-read-path-sse)).
> Next up: the Kafka bus binding and a whole-stack Helm deploy (in review).
> See [WORKPLAN.md](WORKPLAN.md).

## 📖 Understanding this project — start here

New to this repo? Read these two documents first — they are the fastest way to understand what
IntelliOps is and how it works:

| Read this | To understand |
|-----------|---------------|
| **[flow.md](flow.md)** | **How a signal flows through the system** — the one-incident journey, every bus topic and data contract, a function-by-function reference for each of the seven services, and the current status (what's real vs. simulated). |
| **[architectural.md](architectural.md)** | **Why the system is shaped this way** — the layer model and eighteen ADRs (Architecture Decision Records), each with the context, the decision, the trade-offs, and the alternatives rejected. |

Then, for the team: **[WORKPLAN.md](WORKPLAN.md)** divides the remaining work into four
owned streams with acceptance criteria. The full original design spec is at
[docs/superpowers/specs/2026-08-13-intelliops-coe-design.md](docs/superpowers/specs/2026-08-13-intelliops-coe-design.md);
later design decisions have their own specs under `docs/superpowers/specs/`.

---

---

## Why this exists

Modern cloud-native estates emit more telemetry than any human team can triage.

- Enterprises receive **500–1,200 alerts/day**, the large majority of it noise, not signal
  (Covasant, 2026).
- Downtime costs an average of **~$15,000/minute** — an aggregate **~$600B/year** across the
  Global 2000 (Splunk & Cisco with Oxford Economics, 2026; verified against the primary press
  release).
- SRE/DevOps engineers burn their time on manual log correlation and alert triage instead of
  resolution, which drives on-call burnout and keeps MTTR stuck in the hours-not-minutes range.

IntelliOps attacks that directly: **less noise, faster diagnosis, safe automated fixes, and a
loop that learns.**

### Target outcomes (from the proposal, grounded in cited industry data)

| KPI | Target | Basis |
|-----|--------|-------|
| MTTR reduction | **40–60%** | Most consistently documented range across independent sources (incl. a Forrester-commissioned study). |
| Alert volume reduction | **80–95%** | Correlation/clustering collapses the ~85–95% false-positive load (Covasant, 2026). |
| Low-risk incidents auto-remediated | **30–60%** (phased) | Set conservatively for a first-year rollout. |
| SRE on-call burden | **~30–40%** reduction | Directionally supported by AIOps case studies; treated as a target range. |

## How it works (at a glance)

```
 telemetry            ┌───────────────────────────────────────────────────────┐
 sources              │              governance-service (CoE)                 │
 (Prometheus,         │        RBAC · audit log · playbook registry           │
  Loki, OTel)         └──────▲ sync approval gate ─────────▲ audit (async)────┘
      │                      │                             │
      ▼                ┌─────┴──────┐   ┌──────────┐   ┌───┴──────┐   ┌──────────┐
 ┌──────────┐          │correlation │   │   rca    │   │  action  │   │ feedback │
 │ingestion │──raw────▶│ detect +   │──▶│ enrich + │──▶│ approve, │──▶│ label +  │
 │normalize │          │ cluster →  │   │ rank +   │   │ execute, │   │ retrain  │
 │ + dedup  │          │ Situation  │   │ runbook  │   │ rollback │   │ (metrics)│
 └──────────┘          └─────▲──────┘   └──────────┘   └──────────┘   └────┬─────┘
                             │                                             │
                             └───────────── retrain (closed loop) ─────────┘
```

Six services communicate over an **event bus**; the only synchronous step is the
`action → governance` approval gate, which enforces the human-in-the-loop guarantee in the
call graph itself. Full walkthrough in [flow.md](flow.md); the reasoning behind each choice is
in [architectural.md](architectural.md).

### The two innovations

1. **The loop closes.** Most AIOps setups treat correlation and remediation as two static,
   disconnected systems. Here, every remediation outcome (did it work? did it roll back?) becomes
   training data for the correlation model, so accuracy compounds instead of freezing at
   deployment-day quality.
2. **A governed Center of Excellence, not a point tool.** RBAC, audit, rollback, and a shared
   playbook registry are a single control plane — countering the well-documented point-solution
   anti-pattern in AIOps adoption.

## Repository layout

```
intelliops/
├── README.md              ← you are here
├── architectural.md       ← why the system is shaped this way (ADRs, compliance mapping)
├── flow.md                ← data flow + per-function reference
├── docs/superpowers/specs/
│   └── 2026-08-13-intelliops-coe-design.md   ← full design spec
├── common/                ← shared library: contracts, interfaces, bus client, config
├── services/              ← the six services (ingestion, correlation, rca, action,
│                            governance, feedback) — added slice by slice
├── playbooks/             ← YAML playbook definitions (the CoE registry seed)
├── alembic/               ← Postgres schema migrations (versioned, run as a one-shot step)
├── deploy/                ← docker-compose (dev), deploy/k8s (kind remediation demo)
└── pyproject.toml
```

## Tech stack

| Concern | Default (open-source) | Optional / commercial path |
|---------|-----------------------|----------------------------|
| Services | Python 3.11+ · FastAPI · Pydantic | — |
| Event bus | Redis Streams (dev) → Kafka (prod) | — |
| Telemetry sources | Prometheus · Loki · OpenTelemetry | any, via `TelemetrySource` |
| ML / correlation | River (online) · scikit-learn | Moogsoft · BigPanda · Dynatrace (via `Correlator`) |
| Remediation | Kubernetes API · Ansible | any, via `Remediator` |
| Audit + training store | Postgres | — |
| Persistence | Postgres (SQLAlchemy Core + Alembic) | any, via `STORE_BACKEND` |
| On-call / ticketing | (REST approval endpoint) | PagerDuty / Slack (post-Phase-3) |
| Local dev | Docker Compose | Kubernetes (kind demo; whole-stack Helm in review) |

Every named tool sits behind an interface, so it is swappable — see
[ADR-005](architectural.md#adr-005--pluggable-adapters-behind-interfaces).

## Quickstart

> **For a full guided demo, see [DEMO.md](docs/DEMO.md)** — a two-act walkthrough of the live loop
> and real kind-cluster remediation.

> Slice 0 is built: the command below brings up Redis and six health-checked service stubs.
> Slice 1 adds `POST /ingest` on ingestion (8001) and a correlation consumer that emits `Situation`s onto the bus.
> Slice 2 adds rca-service (diagnoses Situations → `situations.diagnosed`) and governance-service (audit log, playbook registry, RBAC at 8005).
> Slice 3 adds action-service (8004): HITL-gated, reversible remediation of `situations.diagnosed` → `remediation.outcomes`, with RBAC-enforced approvals.
> Slice 4 adds feedback-service (8006): labels `remediation.outcomes` into a training store that closes the loop (proven self-healing signatures get suppressed), computes metrics at `GET /metrics`, and graduates playbooks hitl→auto on evidence.

```bash
# 1. Bring up the dev stack (Redis bus + the six service stubs)
docker compose -f deploy/docker-compose.yml up

# 2. Check every service is alive (/health) and ready (/ready — checks bus + DB)
curl localhost:8001/health   # ingestion — liveness
curl localhost:8001/ready    # ingestion — readiness (200 when deps reachable, else 503)
# ... correlation 8002, rca 8003, action 8004, governance 8005, feedback 8006
```

## Run it live (real data, local, free)

Beyond the mock-data quickstart above, the full stack can run against **real** telemetry —
Prometheus actually scraping a demo app, a real anomaly detector, and remediation (dry-run here;
real pod remediation via the kind demo below) — entirely on your machine, at no cost.

1. **Start the stack** (adds `demo-app`, `prometheus`, and `read` to the six core services):

   ```bash
   docker compose -f deploy/docker-compose.yml up --build
   ```

2. **Start the frontend in live mode:**

   ```bash
   cd frontend
   cp .env.example .env.local
   # edit .env.local: set VITE_DATA_MODE=live
   npm run dev
   ```

   Open [http://localhost:5173](http://localhost:5173).

3. **Drive an incident end to end:**

   ```bash
   ./scripts/chaos.sh
   ```

   This breaks the demo app (`POST /break` on the demo app, [http://localhost:8080](http://localhost:8080)),
   generates error traffic, and waits for the stack to detect and diagnose it. Detection takes
   **~15–30 seconds** — that's expected: it's a real Prometheus scrape (every 5s) + ingestion
   poll (every 5s) + River needing a few samples to flag an anomaly, not instant. The script then
   prints the open Situation from the read service ([http://localhost:8007/situations](http://localhost:8007/situations))
   and tells you when to switch to the console.

4. **Approve the fix in the UI.** Open the console, find the open Situation, and click **Approve**.
   Once you're done, recover the demo app with `curl -X POST http://localhost:8080/fix`.

**Resetting between runs:** `./scripts/reset.sh` (or `./scripts/chaos.sh reset`) gives a clean
slate without `docker compose down` — recovers the demo app, clears the detector's learned
baseline, and empties the read model.

> **Remediation modes:** By default remediation runs in **dry-run** mode (ADR-007): the action
> service logs the steps and a simulated health check reports healthy — no real infrastructure is
> touched, which is what the quickstart above uses. To see **real** remediation, run the kind-cluster
> demo (`REMEDIATOR_MODE=k8s`) — approving a fix restarts/scales a real pod and a real health check
> verifies recovery. See [deploy/k8s/README.md](deploy/k8s/README.md).

> **Simulation controls note:** The `/reset`, `/reset-baseline`, `/break`, and `/fix` endpoints
> are simulation controls, not production endpoints. When this stack is pointed at a real system,
> they must be gated or removed.

## Roadmap

Delivered in vertical slices mapped to the proposal's phased rollout. Each slice is a working
increment and is approved before it is built.

The five vertical slices (the closed loop) are done; a second wave of **production-credibility**
work builds on top of them.

| Slice | Phase | Outcome | Status |
|-------|-------|---------|--------|
| 0 | — | Skeleton: contracts, bus, `docker compose up`, health endpoints | ✅ done |
| 1 | Phase 1 | Noise reduction: telemetry in → one `Situation` out | ✅ done |
| 2 | Phase 2 | RCA suggestions + governance audit/RBAC | ✅ done |
| 3 | Phase 3 | HITL-gated reversible remediation, end to end | ✅ done |
| 4 | Phase 4 | Closed feedback loop + metrics + first `auto` playbook | ✅ done |

**Production-credibility work (since the slices):**

| Area | Outcome | Status |
|------|---------|--------|
| Real remediation | `KubernetesRemediator` + real health checks on a kind cluster (`REMEDIATOR_MODE=k8s`) | ✅ done |
| Persistence | Audit / training / playbook stores + durable approvals & baseline on Postgres (`STORE_BACKEND=postgres`), Alembic migrations | ✅ done |
| Security & CI | Edge auth (`AUTH_MODE=token`, internal calls authenticate) + a CI pipeline on every PR | ✅ done |
| Observability | Structured JSON logging + a `/ready` readiness probe per service | ✅ done |
| Platform | Kafka bus binding, whole-stack Helm deploy, load/chaos testing | 🚧 in review |
| Intelligence | Smarter detection + a measured benchmark vs. the baseline | ⬜ open |
| Frontend | Real-time console over SSE, a live incident-pipeline view, an audit explorer, Apple-light repaint | ✅ done |

## Security, compliance & safety

- **Human-in-the-loop by construction** — automated action is structurally gated on a
  governance decision; the gate fails **closed**.
- **Reversible-only automation** — every playbook carries its own rollback steps, and health is
  verified after every action.
- **Full audit trail** — every decision is recorded immutably, threaded by `correlation_id`.
- **Compliance-aligned** — NIST AI RMF (Govern/Map/Measure/Manage), EU AI Act risk-tiered
  documentation, and DORA's 4-hour major-incident **notification** window. Deployable
  in-region/on-prem for sovereign-cloud needs. Details in
  [architectural.md §5](architectural.md#5-compliance-mapping).

## Documentation map

- **[architectural.md](architectural.md)** — design principles, the 5→6 layer mapping, eighteen
  ADRs, cross-cutting concerns, compliance mapping.
- **[docs/DEMO.md](docs/DEMO.md)** — the guided two-act demo walkthrough: the live dry-run loop,
  then real remediation on a kind cluster.
- **[flow.md](flow.md)** — the one-incident journey, bus topics, data contracts, and a
  per-function reference for all seven services.
- **[docs/PERSISTENCE.md](docs/PERSISTENCE.md)** — the Postgres backend: the schema, the
  `STORE_BACKEND` switch, migrations, and the durable runtime state (approvals + baseline).
- **[docs/OBSERVABILITY.md](docs/OBSERVABILITY.md)** — structured JSON logging and the
  `/health` (liveness) vs `/ready` (readiness) probes.
- **[docs/OPERATIONS.md](docs/OPERATIONS.md)** — deploy, the env-switch table, and the auth model.
- **[docs/UI.md](docs/UI.md)** — the operator console: the five views, mock vs. live mode, the SSE
  real-time architecture, and the Apple-light repaint.
- **[deploy/k8s/README.md](deploy/k8s/README.md)** — the real-remediation demo on a kind cluster.
- **[docs/superpowers/specs/2026-08-13-intelliops-coe-design.md](docs/superpowers/specs/2026-08-13-intelliops-coe-design.md)**
  — the original design spec; later decisions have their own specs under `docs/superpowers/specs/`.
