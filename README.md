# IntelliOps CoE

**Agentic AIOps — real detection, diagnosis, and automated remediation with a human in the loop.**

> A production-credible, end-to-end AIOps platform written in Rust: seven platform services, a React operator console, a real Kubernetes remediation path, anomaly detection and AI-assisted diagnosis, and a financial-reporting sample system as its live target. Every number on the dashboard is real; remediation is gated; every decision is audited.
>
> It began as a Python application and was [rebuilt in Rust](#python-vs-rust) against recordings of the original's behaviour. The Python version is preserved on the [`python-legacy`](https://github.com/CodexManvik/intelliops/tree/python-legacy) branch.

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

This isn't a mock dashboard. The full stack runs with one `docker compose up`:

- **Seven platform services** (Rust: tokio and axum) talking over Redis Streams
- **Real Prometheus** scraping real, breakable targets
- **A live SSE pipeline** that streams each incident through Detect → Correlate → Diagnose → Approve → Execute → Verify
- **Real Kubernetes remediation** on a kind or k3s cluster: approving a fix restarts or scales a real pod, and a real health check verifies it
- **Postgres-backed durable state**: the audit trail, playbook registry, pending approvals and the detector's learned baseline survive a restart
- **Meridian**, a four-service financial-reporting sample system with eight fault types, running alongside as the thing being watched

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

| Concern | Default | Opt-in |
|---|---|---|
| Services | Rust 2024 · tokio · axum · serde | — |
| Event bus | Redis Streams (consumer groups, dead-letter, de-duplication) | at-least-once (`BUS_DELIVERY`) |
| Telemetry | Prometheus polling and `POST /ingest` | OpenTelemetry collector overlay |
| Detection | Online z-score | Robust seasonal, or isolation forest (`CORRELATOR_KIND`) |
| Runbook selection | Keyword rules | Sentence embeddings in pure Rust with [candle](https://github.com/huggingface/candle) (on in compose) |
| Remediation | Dry-run | Kubernetes API via [kube-rs](https://kube.rs) (`REMEDIATOR_MODE=k8s`) |
| Pre-flight | — | Rehearsal in a throwaway namespace (`SANDBOX_MODE=k8s`) |
| Persistence | Postgres (sqlx) | in-memory |
| Frontend | React · TypeScript · Tailwind | — |
| Auth | Off | Bearer token (`AUTH_MODE=token`) |
| Deploy | Docker Compose | Helm chart; amd64 and arm64 images |

---

## Python vs Rust

The backend was rebuilt from Python (FastAPI, SQLAlchemy, River, scikit-learn, sentence-transformers) to Rust. Both versions were measured with the same script on the same machine (a 12-thread laptop, Docker Desktop), on fresh containers, twice each. Ranges show both runs; raw results are in [docs/rust/baselines](docs/rust/baselines).

| | Python | Rust |
|---|---|---|
| Image: most platform services | 495 MiB | 42–54 MiB |
| Image: correlation and action | 3,916 MiB | 42 and 54 MiB |
| Image: RCA (includes the embedding model) | 495 MiB, without the model | 219 MiB, with it |
| Idle memory, seven platform services | 486–507 MiB | 111 MiB (97 of it RCA's model) |
| Idle memory, demo app and Meridian | 287–288 MiB | 9 MiB |
| `/ingest`, events accepted per second | 1,763–2,617 | 18,282–20,000 |
| Events per second through detection | 1,195–1,877 | 10,235–10,241 |
| `/ingest` p99 latency, 200-event batch | 156–234 ms | 31–32 ms |
| Cold start to ready | 2.6–5.6 s | 1.8–5.4 s |

**Read these honestly:**

- Part of the ingest gain is a design change, not the language: the Rust service publishes a batch to Redis in one round trip, which Python could also have done.
- Cold start is dominated by starting a container, so there is no meaningful difference to claim.
- The end-to-end time from a fault to an incident is set by the scrape interval and correlation window. The rewrite does not change it.
- The 3.9 GiB Python images were mostly PyTorch and scientific libraries.

### Why Rust suits this project

- **It fits on a free server.** About 120 MiB for every service and workload, against roughly 780 MiB before, plus multi-gigabyte images. Small ARM instances can run the whole platform.
- **The safety property is a type.** Remediation actions are a closed `enum`. Neither a person, a stored playbook nor a language model can express an action that is not in it, and the compiler checks every place that handles one.
- **A service that changes a live cluster should not fail late.** Every error path in action is explicit; unwraps are linted out of service code.
- **Small images, small attack surface.** The images are distroless: one binary, no shell, no package manager, running unprivileged.

**What Rust cost:** the first image build compiles everything and takes several minutes; there is no River or scikit-learn, so the isolation forest was written by hand; and trying out a new model is slower than in Python.

### How the behaviour was preserved

Before any Python was removed, its behaviour was recorded and the Rust code is tested against the recordings in [`testdata/`](testdata):

| Area | Recorded and replayed |
|---|---|
| Correlation engine | 20,800 operations, including suppression |
| Online detector | 9,000 samples (to 1e-12) |
| Remediation gates | 2,500 cases, effects in the same order |
| Health verification | 600 cases |
| Permission matching | 3,015 `fnmatch` cases |
| RCA ranking | 400 diagnoses |
| Embeddings | vectors above 0.9999 cosine similarity with sentence-transformers |
| `/ingest` | 93 well-formed and malformed requests |
| Wire format | live bus messages and API responses round-trip |

### Bugs the rewrite found

- **Rolling back to a revision did not roll back.** The old pod template was merged, so settings added later (such as resource limits) survived. Found by testing against a real cluster.
- **An approval could be recorded and still time out.** A decision landing as the wait ended was audited as approved while the outcome said `aborted:timeout`.
- **Embedding-based runbook selection never ran** in the compose stack: the image lacked the library, and it silently fell back to keyword rules.
- **One bad event half-published a batch** and answered 500 instead of refusing it.

Every intentional behaviour change is listed in [docs/rust/DECISIONS.md](docs/rust/DECISIONS.md).

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

Every incident in a demo needs a real target. **Meridian** (`crates/sim`, with its UI in `meridian-ui/`) is a four-service financial/audit reporting sample platform — gateway, validation, aggregation, reporting — with its own client portal and ops panel. It runs alongside IntelliOps in the same compose file and gives the pipeline something genuinely production-shaped to watch.

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
- **Destructive-action denylist** — a typed vocabulary gate prevents catastrophic actions from being expressed at all; the action type is a closed Rust `enum`
- **Pre-flight sandbox** — fixes are rehearsed on an isolated namespace clone; a failed rehearsal blocks auto-remediation and surfaces the verdict in the UI
- **Immutable audit trail** — every RBAC decision, approval, execution, and outcome is recorded in Postgres, threaded by `correlation_id`
- **AI is bounded** — the AI can *draft* a runbook for a gap, but it cannot join the registry without a human approval; it can *rank* vetted playbooks by embedding similarity, but it never chooses an unvetted fix
- **Compliance-aligned** — NIST AI RMF (Govern/Map/Measure/Manage), EU AI Act risk-tiered documentation, DORA 4-hour notification window; deployable on-prem for sovereign-cloud needs

---

## Test suite & CI

```bash
cargo test                                   # unit, recorded-behaviour and API tests
cargo clippy --all-targets -- -D warnings
bash scripts/smoke.sh                        # against a running compose stack
npm --prefix frontend run build              # type-check and contrast gate
```

Optional tests, skipped unless enabled:

- `INTELLIOPS_TEST_DATABASE_URL` runs the storage tests on a real Postgres.
- `INTELLIOPS_KUBE_TEST=1` runs remediation and the sandbox against the current kube context. Use a throwaway cluster.
- Model files in `models/minilm` run the embedding parity test.

CI runs all of these, plus Helm rendering, dependency audits and a full compose smoke test. On `master` it publishes amd64 and arm64 images.

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
| Whole-stack Helm deploy; multi-arch images | ✅ |
| Rebuilt in Rust, checked against recorded behaviour | ✅ |

---

## Documentation

| Document | What it covers |
|---|---|
| [architectural.md](architectural.md) | Design principles, layer mapping, 30 ADRs with context and trade-offs |
| [flow.md](flow.md) | One-incident journey, bus topics, data contracts, per-function service reference |
| [docs/DEMO.md](docs/DEMO.md) | Guided two-act demo — live loop then real kind-cluster remediation |
| [docs/MERIDIAN.md](docs/MERIDIAN.md) | The sample financial platform — services, fault scenarios, verified results, honest limits |
| [docs/rust/DECISIONS.md](docs/rust/DECISIONS.md) | Decisions of the Rust rebuild and every intentional behaviour change |
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
├── crates/
│   ├── contracts/      wire types and ids
│   ├── bus/ store/     Redis Streams; Postgres
│   ├── service/        config, probes, metrics, auth, shutdown
│   ├── ingestion/      normalize and publish telemetry
│   ├── detect/ correlation/   anomaly detection and incident windows
│   ├── rca/ embed/     ranking, embeddings, explanations
│   ├── action/         safety gates, Kubernetes, sandbox
│   ├── governance/ llm/       RBAC, audit, registry, AI runbook author
│   ├── feedback/       labelled outcomes, graduation
│   ├── read/           read model, live stream
│   └── sim/            demo app and Meridian
├── frontend/           React operator console
├── meridian-ui/        Meridian ops panel
├── deploy/             Dockerfile, compose, Helm chart, kind
├── testdata/           recorded behaviour of the original
└── docs/
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
