# IntelliOps CoE

**Agentic AIOps for automated incident detection, diagnosis & remediation.**

IntelliOps CoE ingests the telemetry your systems already produce, uses machine learning to
collapse alert storms into a handful of meaningful **Situations**, suggests likely root causes,
and — under strict human-in-the-loop governance — executes safe, reversible remediation. Every
outcome feeds back into the model, so the system gets more accurate the longer it runs.

It is built to **augment** your existing observability, CI/CD, and ticketing stack, not replace
it, and it is **open-source-first** to avoid vendor lock-in.

> **Project status: all four implementation slices complete — the loop is closed.**
> This repository now contains the full implementation — all six services across four delivery
> slices — alongside the design docs (`README.md`, [architectural.md](architectural.md),
> [flow.md](flow.md), and the [spec](docs/superpowers/specs/2026-08-13-intelliops-coe-design.md)).
> Code was built in slices — see [Roadmap](#roadmap). Origin: a 2026 capstone proposal; the
> engineering decisions that turn that proposal into a buildable system are recorded as ADRs in
> [architectural.md](architectural.md).

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
├── deploy/                ← docker-compose (dev) and k8s manifests (later)
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
| On-call / ticketing | (REST approval endpoint) | PagerDuty / Slack (post-Phase-3) |
| Local dev | Docker Compose | Kubernetes (later) |

Every named tool sits behind an interface, so it is swappable — see
[ADR-005](architectural.md#adr-005--pluggable-adapters-behind-interfaces).

## Quickstart

> Slice 0 is built: the command below brings up Redis and six health-checked service stubs.
> Slice 1 adds `POST /ingest` on ingestion (8001) and a correlation consumer that emits `Situation`s onto the bus.
> Slice 2 adds rca-service (diagnoses Situations → `situations.diagnosed`) and governance-service (audit log, playbook registry, RBAC at 8005).
> Slice 3 adds action-service (8004): HITL-gated, reversible remediation of `situations.diagnosed` → `remediation.outcomes`, with RBAC-enforced approvals.
> Slice 4 adds feedback-service (8006): labels `remediation.outcomes` into a training store that closes the loop (proven self-healing signatures get suppressed), computes metrics at `GET /metrics`, and graduates playbooks hitl→auto on evidence.

```bash
# 1. Bring up the dev stack (Redis bus + the six service stubs)
docker compose -f deploy/docker-compose.yml up

# 2. Check every service is healthy
curl localhost:8001/health   # ingestion
curl localhost:8002/health   # correlation
# ... rca 8003, action 8004, governance 8005, feedback 8006
```

## Run it live (real data, local, free)

Beyond the mock-data quickstart above, the full stack can run against **real** telemetry —
Prometheus actually scraping a demo app, a real anomaly detector, and a real (dry-run)
remediation — entirely on your machine, at no cost.

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

> **Dry-run safety note:** Remediation runs in dry-run mode (ADR-007): the action service logs
> the remediation steps and a simulated health check reports healthy. "Resolved" means the fix
> was logged and simulated — no real infrastructure is ever touched.

> **Simulation controls note:** The `/reset`, `/reset-baseline`, `/break`, and `/fix` endpoints
> are simulation controls, not production endpoints. When this stack is pointed at a real system,
> they must be gated or removed.

## Roadmap

Delivered in vertical slices mapped to the proposal's phased rollout. Each slice is a working
increment and is approved before it is built.

| Slice | Phase | Outcome | Status |
|-------|-------|---------|--------|
| 0 | — | Skeleton: contracts, bus, `docker compose up`, health endpoints | ✅ done |
| 1 | Phase 1 | Noise reduction: telemetry in → one `Situation` out | ✅ done |
| 2 | Phase 2 | RCA suggestions + governance audit/RBAC | ✅ done |
| 3 | Phase 3 | HITL-gated reversible remediation, end to end | ✅ done |
| 4 | Phase 4 | Closed feedback loop + metrics + first `auto` playbook | ✅ done |

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

- **[architectural.md](architectural.md)** — design principles, the 5→6 layer mapping, eight
  ADRs, cross-cutting concerns, compliance mapping.
- **[flow.md](flow.md)** — the one-incident journey, bus topics, data contracts, and a
  per-function reference for all six services.
- **[docs/superpowers/specs/2026-08-13-intelliops-coe-design.md](docs/superpowers/specs/2026-08-13-intelliops-coe-design.md)**
  — the complete design spec the two documents above draw from.
