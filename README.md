# IntelliOps CoE

**Agentic AIOps — real detection, diagnosis, and automated remediation with a human in the loop.**

> A production-credible, end-to-end AIOps platform built from scratch: seven Python microservices, a React operator console, a real Kubernetes remediation path, a self-improving ML pipeline, and a Deloitte-style financial platform as its live target. Every number on the dashboard is real; every fix is reversible; every decision is audited.

---

<!-- ═══════════════════════════════════════════════════════════════════
     DEMO VIDEO — drop your launch video embed here
     ════════════════════════════════════════════════════════════════ -->

## Demo

<!-- Replace the block below with your video embed or a linked thumbnail.
     Example (YouTube): [![Watch the demo](docs/assets/thumbnail.png)](https://youtu.be/YOUR_ID)
     Example (mp4 in repo): <video src="docs/assets/demo.mp4" controls width="100%"></video>  -->

> 📹 **Demo video coming soon** — live incident detection → diagnosis → HITL approval → real pod remediation, in under 3 minutes.

---

## What it does

Modern cloud estates emit **500–1,200 alerts per day**. The large majority is noise. SREs burn their on-call hours triaging it instead of fixing things, which keeps MTTR stuck in the hours range and drives burnout.

IntelliOps attacks that from four angles simultaneously:

| Problem | What IntelliOps does |
|---|---|
| Alert storm → unreadable noise | Collapses raw alerts into a handful of meaningful **Situations** using online ML (z-score, robust seasonal, or a trained IsolationForest) |
| "Which service, which cause?" | Enriches each Situation with deploy history, topology, and metric evidence; ranks hypotheses; explains the top one in plain language |
| "Can we automate the fix?" | Executes safe, reversible remediation — scale, restart, rollback, config patch — after a pre-flight rehearsal on an isolated cluster clone |
| "Did it work, and who approved it?" | Re-checks the firing metric after every fix; rolls back if unhealthy; writes every decision to an immutable audit trail |

And the system **learns**: every outcome (success, rollback, escalation) feeds back into the correlation model, so accuracy compounds rather than freezing at deployment-day quality.

---

## What's actually running

This isn't a mock dashboard. The full stack runs in a single `docker compose up`:

- **Seven Python microservices** (FastAPI) talking over Redis Streams (or Kafka)
- **Real Prometheus** scraping a real breakable target — every metric on the console is scraped, not generated
- **A live SSE pipeline** that streams each incident through Detect → Correlate → Diagnose → Approve → Execute → Verify in real time
- **Real Kubernetes remediation** on a local kind cluster — approving a fix restarts/scales a real pod; a real health check verifies it
- **Postgres-backed durable state** — the audit trail, playbook registry, pending approvals, and the detector's learned baseline all survive a restart
- **Meridian** — a four-service Deloitte-style financial/audit platform (gateway, validation, aggregation, reporting + its own portal UI) running alongside IntelliOps as a genuine production-shaped target, emitting 11 USE+RED gauges across 8 typed fault scenarios

---

## Architecture

```
 telemetry                ┌───────────────────────────────────────────────────────┐
 sources                  │              governance-service (CoE)                 │
 (Prometheus,             │        RBAC · audit log · playbook registry           │
  Loki, OTel)             └──────▲ sync approval gate ─────────▲ audit (async)───┘
      │                          │                              │
      ▼                    ┌─────┴──────┐   ┌──────────┐   ┌──┴───────┐   ┌──────────┐
 ┌──────────┐              │correlation │   │   rca    │   │  action  │   │ feedback │
 │ingestion │──raw────────▶│ detect +   │──▶│ enrich + │──▶│ approve, │──▶│ label +  │
 │normalize │              │ cluster →  │   │ rank +   │   │ execute, │   │ retrain  │
 │ + dedup  │              │ Situation  │   │ runbook  │   │ rollback │   │ (metrics)│
 └──────────┘              └─────▲──────┘   └──────────┘   └──────────┘   └────┬─────┘
                                 │                                              │
                                 └──────────────── retrain (closed loop) ───────┘
                                          ▲
                                   read-service (CQRS)
                                   React console (SSE)
```

Six services communicate over an event bus. The only synchronous step is `action → governance` — the human-in-the-loop gate is enforced in the call graph itself, not by convention. Full walkthrough in [flow.md](flow.md); the reasoning behind every decision is in [architectural.md](architectural.md).

### Design principles

1. **Augment, don't replace** — IntelliOps sits alongside your existing observability and ticketing stack; it never becomes the system of record
2. **Human-in-the-loop by construction** — automated action is structurally gated on a governance decision; the gate fails closed
3. **Reversible-only automation** — the system only automates what it can undo; it verifies health after acting and rolls back when the metric doesn't recover
4. **The loop closes** — every remediation outcome is training data; the correlation model improves as it operates
5. **Open-source-first** — every named tool sits behind an interface and is swappable; no vendor lock-in

---

## Tech stack

| Concern | Default | Opt-in / swap path |
|---|---|---|
| Services | Python 3.11 · FastAPI · Pydantic v2 | — |
| Event bus | Redis Streams | Kafka (`BUS_BACKEND=kafka`) |
| Telemetry | Prometheus · Loki · OpenTelemetry | any, via `TelemetrySource` |
| Correlation / ML | River (online z-score) | Robust seasonal z-score · scikit-learn IsolationForest (`CORRELATOR_KIND`) |
| Runbook selection | Keyword rules | Embedding similarity — `sentence-transformers all-MiniLM-L6-v2` (`RUNBOOK_SELECTOR_MODE=embedding`, now on by default) |
| Remediation | Dry-run (safe default) | Kubernetes API real pod ops (`REMEDIATOR_MODE=k8s`) |
| Pre-flight | — | Sandbox rehearsal on isolated namespace clone (`SANDBOX_MODE=k8s`) |
| Persistence | Postgres (SQLAlchemy Core + Alembic) | — |
| Frontend | React 19 · TypeScript · Tailwind | — |
| Auth | Off (dev default) | Bearer token (`AUTH_MODE=token`) |
| Deploy | Docker Compose | Helm chart (`deploy/k8s/`) |

---

## Quickstart

```bash
# 1. Start the full stack (Redis, Postgres, 7 services, Prometheus, demo app, Meridian)
docker compose -f deploy/docker-compose.yml up --build

# 2. Start the console in live mode
cd frontend && cp .env.example .env.local
# edit .env.local → VITE_DATA_MODE=live
npm install && npm run dev
# open http://localhost:5173
```

**Logs, metrics and the audit trail in one place:** Grafana at
<http://localhost:3000> (on kind: <http://localhost:30300>). Every container's
logs (via Loki), the Prometheus metrics, and the Postgres audit trail on one
dashboard, viewable without logging in. See [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md#4-logs-and-metrics-ui-grafana).

**Drive a real incident end to end:**

```bash
# Break the demo app and watch the pipeline animate in the console
./scripts/chaos.sh

# Approve the fix in the UI (Incidents → open Situation → Approve)

# Reset between runs — clears the detector baseline and read model
./scripts/reset.sh
```

Detection takes **15–30 seconds** — that's a real Prometheus scrape cycle plus River needing a few samples to flag the anomaly, not a mock timer.

> **For a step-by-step demo walkthrough** (including real kind-cluster remediation), see [docs/DEMO.md](docs/DEMO.md).

### Set your operator name

```bash
# deploy/.env  (gitignored)
VITE_OPERATOR_NAME=your-name    # shows in approvals + audit trail
INTELLIOPS_LLM_ENDPOINT=https://api.groq.com/openai/v1   # optional: LLM explanations
INTELLIOPS_LLM_API_KEY=gsk_...
INTELLIOPS_LLM_MODEL=llama-3.3-70b-versatile
```

---

## The Meridian sample system

Every incident in a demo needs a real target. **Meridian** (`services/meridian/`) is a four-service Deloitte-style financial/audit reporting platform — gateway, validation, aggregation, reporting — with its own client portal and ops panel. It runs alongside IntelliOps in the same compose file and gives the pipeline something genuinely production-shaped to watch.

Each service emits a **USE+RED metric set** (11 gauges: CPU, memory, disk, queue depth, DB-pool, request rate, error rate, p50/p99 latency) and accepts **8 typed fault scenarios**, each moving a realistic cluster of those metrics rather than a single gauge.

Three scenarios were verified live end-to-end against real Docker, each producing the expected, genuinely different diagnosis:

| Fault | Service | Correct diagnosis |
|---|---|---|
| CPU saturation | meridian-aggregation | `scale-service` |
| Error-rate spike | meridian-validation | `restart-pod` |
| Recent deploy + saturation | meridian-gateway | `rollback-deploy` (outranks saturation) |

Meridian is wired to IntelliOps through **additive-only** changes — a Prometheus scrape job per service and a broadened ingestion selector. No IntelliOps service code changed. See [docs/MERIDIAN.md](docs/MERIDIAN.md).

---

## Safety & compliance

- **HITL gate fails closed** — a timeout, rejection, or unreachable governance service means no action, never a default execute
- **Destructive-action denylist** — a typed vocabulary gate prevents catastrophic actions from being expressed at all; the `Literal` action type is permanently closed
- **Pre-flight sandbox** — fixes are rehearsed on an isolated namespace clone; a failed rehearsal blocks auto-remediation and surfaces the verdict in the UI
- **Immutable audit trail** — every RBAC decision, approval, execution, and outcome is recorded in Postgres, threaded by `correlation_id`
- **AI is bounded** — the AI can *draft* a runbook for a gap, but it cannot join the registry without a human approval; it can *rank* vetted playbooks by embedding similarity, but it never chooses an unvetted fix
- **Compliance-aligned** — NIST AI RMF (Govern/Map/Measure/Manage), EU AI Act risk-tiered documentation, DORA 4-hour notification window; deployable on-prem for sovereign-cloud needs

---

## Test suite & CI

```bash
uv run pytest          # 433+ tests, no Docker needed for the core suite
uv run ruff check .    # linting
npm run build          # frontend type-check + contrast gate (112 checks)
```

CI runs on every PR: lint → test → frontend build → compose smoke test. The correlator improvement is covered by a [reproducible benchmark](docs/BENCHMARKS.md) committed to the repo.

---

## What's been built

| Area | Status |
|---|---|
| Seven-service closed loop (detect → diagnose → approve → execute → verify → retrain) | ✅ |
| Real Kubernetes remediation + rollback on a kind cluster | ✅ |
| Postgres persistence — audit trail, playbook registry, durable approvals, baseline snapshot | ✅ |
| Pluggable detectors — River (online z-score), robust seasonal, trained IsolationForest | ✅ |
| Reliability-weighted + LLM-explained RCA with a committed CI benchmark | ✅ |
| Semantic runbook selection via embedding similarity (now on by default) | ✅ |
| AI-authored runbook drafting — propose-only, human-approval gate, type-safe actions | ✅ |
| Pre-flight sandbox rehearsal on an isolated namespace clone | ✅ |
| Destructive-action denylist (7 typed Deployment-scoped verbs; `Literal` closed) | ✅ |
| Real-time React console — SSE pipeline view, audit explorer, light/dark theme | ✅ |
| Agent Activity trace — live step-by-step view of the AI runbook author as it works | ✅ |
| Meridian sample financial platform — 4 services, 8 fault scenarios, verified live | ✅ |
| Edge auth (bearer token, timing-safe), structured JSON logging, readiness probes | ✅ |
| Kafka bus binding + whole-stack Helm deploy | ✅ |
| 433+ tests · CI pipeline · ruff-clean | ✅ |

---

## Documentation

| Document | What it covers |
|---|---|
| [architectural.md](architectural.md) | Design principles, layer mapping, 30 ADRs with context and trade-offs |
| [flow.md](flow.md) | One-incident journey, bus topics, data contracts, per-function service reference |
| [docs/DEMO.md](docs/DEMO.md) | Guided two-act demo — live loop then real kind-cluster remediation |
| [docs/MERIDIAN.md](docs/MERIDIAN.md) | The sample financial platform — services, fault scenarios, verified results, honest limits |
| [docs/BENCHMARKS.md](docs/BENCHMARKS.md) | Correlator benchmark — methodology, results table, where `robust`/`trained` win and cost |
| [docs/PERSISTENCE.md](docs/PERSISTENCE.md) | Postgres backend — schema, `STORE_BACKEND` switch, migrations, durable runtime state |
| [docs/UI.md](docs/UI.md) | Operator console — five views, mock vs. live mode, SSE architecture, theme system |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Deploy guide, full env-switch table, auth model |
| [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) | Structured JSON logging, liveness vs. readiness probes |
| [deploy/k8s/README.md](deploy/k8s/README.md) | Real-remediation demo on a kind cluster |

---

## Repository layout

```
intelliops/
├── README.md                  ← you are here
├── architectural.md           ← why: 30 ADRs, layer model, compliance mapping
├── flow.md                    ← how: data flow, bus contracts, per-function reference
├── common/                    ← shared library: contracts, interfaces, bus client, config, auth
├── services/
│   ├── ingestion/             ← normalize, dedup, ingest from Prometheus/Loki
│   ├── correlation/           ← detect anomalies, cluster into Situations
│   ├── rca/                   ← enrich, rank hypotheses, select + embed runbooks, explain
│   ├── action/                ← HITL gate, sandbox, execute, verify, rollback
│   ├── governance/            ← RBAC, audit log, playbook registry, AI runbook author
│   ├── feedback/              ← label outcomes, retrain, graduate playbooks, compute KPIs
│   ├── read/                  ← CQRS read model, SSE push, Prometheus proxy
│   └── meridian/              ← 4-service sample financial platform + portal UI
├── frontend/                  ← React 19 operator console (TypeScript, Tailwind)
├── playbooks/                 ← YAML playbook definitions
├── alembic/                   ← Postgres schema migrations
├── deploy/
│   ├── docker-compose.yml     ← full dev stack
│   └── k8s/                   ← Helm chart + kind demo
├── tests/                     ← 433+ tests (pytest)
├── docs/                      ← DEMO, MERIDIAN, BENCHMARKS, PERSISTENCE, UI, OPERATIONS
└── pyproject.toml
```

---

## Why this exists

SRE/DevOps engineers spend most of their on-call hours on manual log correlation and alert triage — not on resolution. Downtime costs enterprises roughly **$15,000/minute** (Splunk & Cisco with Oxford Economics, 2026), yet most AIOps setups treat correlation and remediation as disconnected, static systems that can't improve over time.

IntelliOps is built around two ideas:

1. **The loop closes.** Remediation outcomes feed back into the correlation model so accuracy compounds instead of freezing at deployment-day quality.
2. **A governed Center of Excellence, not a point tool.** RBAC, audit, rollback, and a shared playbook registry are a single control plane — countering the well-documented point-solution anti-pattern in AIOps adoption.

Target outcomes (from the proposal, grounded in cited industry data):

| KPI | Target |
|---|---|
| MTTR reduction | 40–60% |
| Alert volume reduction | 80–95% |
| Low-risk incidents auto-remediated | 30–60% (phased) |
| SRE on-call burden | ~30–40% reduction |
