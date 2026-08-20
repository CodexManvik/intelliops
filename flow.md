# IntelliOps CoE — Flow & Function Reference

This document tracks **how a signal flows through the system** and **what each function does
and why**. Read it alongside:

- [architectural.md](architectural.md) — *why* the system is shaped this way (ADRs).
- [docs/superpowers/specs/2026-08-13-intelliops-coe-design.md](docs/superpowers/specs/2026-08-13-intelliops-coe-design.md) — the full spec.

> **Status (updated 2026-08-18).** The six-service closed loop is **built and running
> end-to-end**, plus three things that came after the original design: a **read-model service**
> (the CQRS read side the dashboard reads from), a **React operator console**, and a **live,
> repeatably-runnable demo stack** on docker-compose. What ran only as a "target design" in the
> first draft now runs live — including remediation itself, which can now drive a real
> Kubernetes cluster behind an opt-in switch (dry-run stays the production-safe default). See
> [§8 Current status & what's next](#8-current-status--whats-next) for exactly what's real, what's
> still simulated, and what's next.

---

## 1. The one-incident journey (happy path)

A single production incident, from raw telemetry to a closed feedback loop:

```
   Prometheus / Loki / OpenTelemetry
            │  (1) scrape / receive
            ▼
   ┌────────────────────┐
   │ ingestion-service  │   normalize → TelemetryEvent, dedup by fingerprint
   └────────────────────┘   ── publish ▶ topic: telemetry.raw
            │
            ▼
   ┌────────────────────┐
   │ correlation-service│   (2) detect_anomaly per event
   │                    │   (3) correlate anomalies → one Situation
   └────────────────────┘   ── publish ▶ topic: situations.detected
            │
            ▼
   ┌────────────────────┐
   │ rca-service        │   (4) enrich with deploy/config/topology context
   │                    │   (5) rank root-cause hypotheses + attach runbook
   └────────────────────┘   ── publish ▶ topic: situations.diagnosed
            │
            ▼
   ┌────────────────────┐         ┌───────────────────────┐
   │ action-service     │ (6) ──▶ │ governance-service    │  RBAC check + HITL approval
   │  select_playbook   │ ◀── ✔/�’ │  (SYNCHRONOUS gate)   │  + write audit record
   │  execute → verify  │         └───────────────────────┘
   │  rollback if unhealthy                                   │ (audit events from ALL
   └────────────────────┘   ── publish ▶ remediation.outcomes │  services flow here async)
            │
            ▼
   ┌────────────────────┐
   │ feedback-service   │   (7) label outcome → training store
   │                    │       compute MTTR/MTTD metrics
   └────────────────────┘   ── training data ▶ correlation-service.retrain()   ⟲ LOOP CLOSED

   ── ALSO consuming the situation/outcome stream (the read side): ──
   ┌────────────────────┐
   │ read-service       │   projects situations.detected/diagnosed + remediation.outcomes
   │  (CQRS read model) │   + situations.suppressed into the exact shapes the UI needs
   └────────────────────┘   ── serves ▶ GET /situations · /outcomes · /metrics
            │
            ▼
   ┌────────────────────┐
   │ React console      │   Overview (live KPIs) · Incidents (HITL approve/reject)
   │  (operator UI)     │   · Governance (gates, audit, playbook registry)
   └────────────────────┘
```

**The single synchronous step** is `action-service → governance-service` (step 6). Everything
else is asynchronous over the bus. The gate fails **closed**: no governance approval → no
action. See [ADR-003](architectural.md#adr-003--governance-is-an-active-gate-not-passive-logging).

**The read side (CQRS).** The six services above are the *write* side — they consume, decide,
and emit events. The **read-service** ([ADR-009](architectural.md#adr-009--a-read-model-service-cqrs-for-the-dashboard))
is a separate consumer that projects those events into an in-memory read model and serves the
dashboard over plain `GET` endpoints. It holds no source-of-truth state — the Redis event
streams are the record, and the projection rebuilds from them on restart.

## 2. Bus topics (the wiring)

| Topic | Produced by | Consumed by | Payload | Meaning |
|-------|-------------|-------------|---------|---------|
| `telemetry.raw` | ingestion | correlation | `TelemetryEvent` | a normalized signal |
| `situations.detected` | correlation | rca, **read** | `Situation` (detected) | an alert storm collapsed into one incident |
| `situations.diagnosed` | rca | action, **read** | `DiagnosedSituation` (situation + hypotheses) | incident with likely cause + suggested fix |
| `situations.suppressed` | correlation | **read** | `Situation` (suppressed) | a signature that reliably self-heals — detected, then *not* emitted as an incident (closed-loop suppression, made visible for metrics) |
| `remediation.outcomes` | action | feedback, **read** | `RemediationOutcome` | did the fix work / roll back |
| `audit.events` | all services | governance | `AuditRecord` | append-only trail (fire-and-forget) |

> **Note.** `situations.diagnosed` carries a `DiagnosedSituation` (the `Situation` plus ranked
> `RootCauseHypothesis` list and the suggested runbook id) — additive over the frozen
> `Situation` contract. Governance also handles approvals over REST (`/approvals`), not the bus
> — approvals are request/response, not a stream (see §5.5).

## 3. Shared data contracts (`common/contracts.py`)

These are the nouns every service passes around. Defined once in `common/` so services can't
drift ([ADR-006](architectural.md#adr-006--monorepo-with-a-shared-common-library)).

| Contract | Key fields | Why it exists |
|----------|-----------|---------------|
| `TelemetryEvent` | `source, kind, name, value/payload, labels, ts, fingerprint` | Canonical shape so every downstream service is source-agnostic. |
| `Situation` | `id, status, member_events[], severity, first_seen, last_seen, signature` | The universal incident currency; `signature` recognizes recurrences ([ADR-004](architectural.md#adr-004--situation-as-the-universal-currency)). |
| `RootCauseHypothesis` | `situation_id, description, confidence, evidence[], suggested_runbook_id` | Makes RCA output rankable and actionable, not just prose. |
| `DiagnosedSituation` | `situation, hypotheses[], suggested_runbook_id` | The `situations.diagnosed` payload: a diagnosed `Situation` plus ranked causes. Additive — never mutates the frozen `Situation`. |
| `Playbook` | `id, name, match_rule, steps[], hitl_mode, reversible, rollback_steps[]` | A remediation with its safety scope and undo baked in ([ADR-007](architectural.md#adr-007--reversible-only-health-verified-remediation)). |
| `ApprovalRequest` | `id, situation_id, playbook_id, requested_by, status, decided_by` | The record a human approves/rejects at the HITL gate. |
| `RemediationOutcome` | `situation_id, playbook_id, result, health_after, ts, hitl_mode` | The feedback signal that closes the loop. `hitl_mode` (added later) lets the read model report auto-vs-HITL remediation truthfully. |
| `AuditRecord` | `actor, action, resource, decision, ts, correlation_id` | Immutable compliance trail; `correlation_id` threads one incident end to end. |

## 4. Pluggable interfaces (`common/interfaces.py`)

The swap points that keep the system platform-agnostic and testable
([ADR-005](architectural.md#adr-005--pluggable-adapters-behind-interfaces)).

| Interface | Methods | Implementations that exist today | Deferred / planned |
|-----------|---------|----------------------------------|--------------------|
| `TelemetrySource` | `poll()`, `subscribe()` | `FileTelemetrySource` (JSONL, tests), **`PrometheusSource`** (real PromQL over the Prometheus HTTP API, defensive: never raises on a bad response) | Loki, OTel sources |
| `Correlator` | `detect()`, `correlate()`, `retrain()` | `RiverCorrelator` (online z-score anomaly + windowed clustering) | scikit-learn / smarter models |
| `Remediator` | `execute()`, `rollback()` | `DryRunRemediator` (logs steps, touches nothing — the safe default), `RecordingRemediator` (tests), **`KubernetesRemediator`** (real `AppsV1Api` calls — restart via a `restartedAt` annotation patch, scale via `patch_namespaced_deployment_scale`, rollback via a rollout-restart annotation; no shell, no string parsing; never deletes; any API error → `False`, never raises. Behind `REMEDIATOR_MODE=k8s`, targeting a local kind cluster — see [deploy/k8s/README.md](deploy/k8s/README.md)) | — |
| `HealthChecker` | `check()` | `AlwaysHealthyChecker` (pairs with dry-run), `FixedHealthChecker` (tests), **real `KubernetesHealthChecker`** (two signals — pod readiness from deployment status, and metric recovery re-queried from Prometheus — polled to a timeout; fails closed. Behind `HEALTH_CHECK_MODE=k8s`) | — |
| `GovernanceGate` (action→governance) | `check_rbac()`, `request_approval()`, `await_decision()`, `write_audit()` | `InProcessGovernanceGate` (shared dict, single-process/tests), **`HttpGovernanceGate`** (REST — works across containers; fail-closed on any error) | — |
| `AuditSink` | `write()`, `records()` | `FileAuditSink` (JSONL), `InMemoryAuditSink` (tests), **`PostgresAuditSink`** (hybrid schema — indexed columns + a JSONB payload that is the source of truth; errors propagate, a lost audit write is a compliance failure. Behind `STORE_BACKEND=postgres` — see [docs/PERSISTENCE.md](docs/PERSISTENCE.md)) | — |
| `PlaybookStore` | `register()`, `get()`, `list()` | `InMemoryPlaybookStore` (tests), `FilePlaybookStore` (YAML dir), **`PostgresPlaybookStore`** (upsert via `ON CONFLICT`; same `STORE_BACKEND=postgres` switch) | — |
| `TrainingStore` | `append()`, `read_all()` | `InMemoryTrainingStore` (tests), `FileTrainingStore` (JSONL), **`PostgresTrainingStore`** (same switch; errors propagate) | — |
| `BusClient` | `publish()`, `consume()` | **`RedisBus`** (Redis Streams, consumer groups) | Kafka (prod) |

Tests bind fakes (`RecordingRemediator`, `FixedHealthChecker`, `InMemory*`) to exercise a
service in isolation. **Selection is config-driven** — env switches pick the live vs test-safe
binding (`TELEMETRY_MODE=file|prometheus`, `GOVERNANCE_MODE=in_process|http`,
`STORE_BACKEND=file|postgres`), so the default build stays test-safe and the compose stack turns
the live bindings on.

## 5. Per-service function reference

For each function: **what it does · why we use it · what it depends on.**

### 5.1 `ingestion-service` — get clean, deduped telemetry onto the bus

| Function | What it does | Why | Depends on |
|----------|--------------|-----|-----------|
| `run_poll_loop()` | On an interval, pulls from each configured `TelemetrySource` and hands raw signals to `normalize`. | The service's heartbeat; keeps telemetry flowing without downstream services knowing the source. | `TelemetrySource`, `BusClient` |
| `normalize(raw) → TelemetryEvent` | Converts a vendor-specific signal (Prom sample, Loki line, OTel span) into the canonical `TelemetryEvent`. | So correlation and everything after it are **source-agnostic** — the whole point of the data layer. | `contracts.TelemetryEvent` |
| `compute_fingerprint(event) → str` | Stable hash of the event's identity (name + labels). | Kills duplicate alerts **at the door** — the first, cheapest cut of the 80–95% noise-reduction goal. | — |

### 5.2 `correlation-service` — collapse the storm into a Situation

| Function | What it does | Why | Depends on |
|----------|--------------|-----|-----------|
| `detect_anomaly(event) → score` | Scores an event against the learned baseline; flags the abnormal ones. | The noise-reduction engine — separates signal from the 85–95% false positives. | `Correlator` |
| `correlate(anomalies) → Situation` | Clusters related anomalies (by time / topology / labels) into **one** `Situation`. | Turns an alert storm into a single actionable incident — the core value proposition. | `Correlator`, `contracts.Situation` |
| `load_model()` | Loads the current anomaly/correlation model at startup. | Lets the model evolve without code changes. | model store |
| `retrain(training_data)` | Re-fits the model from labeled outcomes produced by `feedback-service`. | **This is where the closed loop lands** — accuracy compounds over time. | training store, `Correlator` |

### 5.3 `rca-service` — explain the Situation and suggest a fix

| Function | What it does | Why | Depends on |
|----------|--------------|-----|-----------|
| `enrich(situation) → context` | Attaches recent deploys, config/change data, and service topology to the situation. | Context is what makes a root-cause suggestion **credible** instead of a guess. | deploy/config/topology sources |
| `rank_hypotheses(situation, context) → [RootCauseHypothesis]` | Scores and orders likely causes with their supporting evidence. | Gives responders a ranked starting point, not a wall of data. | `contracts.RootCauseHypothesis` |
| `surface_runbook(hypothesis) → Playbook` | Maps the top hypothesis to a known playbook/runbook. | Hands the responder (or the action layer) a concrete next step. | governance playbook registry |

### 5.4 `action-service` — do the fix, safely

| Function | What it does | Why | Depends on |
|----------|--------------|-----|-----------|
| `select_playbook(situation) → Playbook` | Matches a diagnosed situation to a remediation via `match_rule`. | Connects "what's wrong" to "what to do." | governance registry |
| `request_approval(playbook, situation) → decision` | **Synchronous** call to governance for RBAC + HITL approval. | The structural HITL gate — action can't proceed without a yes ([ADR-003](architectural.md#adr-003--governance-is-an-active-gate-not-passive-logging)). | `governance-service` |
| `execute(playbook)` | Runs the playbook's steps through a `Remediator`. | Performs the actual fix (restart pod, scale service, …). | `Remediator` |
| `verify_health() → bool` | Checks system health after acting. | Confirms the fix worked before declaring success. | telemetry / health checks |
| `rollback(playbook)` | Runs `rollback_steps` if health verification fails. | Enforces **reversible-only** automation ([ADR-007](architectural.md#adr-007--reversible-only-health-verified-remediation)). | `Remediator` |
| `emit_outcome(...)` | Publishes a `RemediationOutcome` to `remediation.outcomes`. | Feeds the feedback loop. | `BusClient` |

### 5.5 `governance-service` — the control plane

| Function | What it does | Why | Depends on |
|----------|--------------|-----|-----------|
| `check_rbac(actor, action, resource) → allow \| deny` | Decides whether an actor may perform an action. | Every automated action passes here — the enforceable RBAC guarantee. | RBAC policy |
| `create_approval_request() → ApprovalRequest` | Opens a pending approval for a `hitl` playbook. | Materializes the human decision point. | `contracts.ApprovalRequest` |
| `decide(id, approve \| reject)` | REST endpoint (`POST /approvals/{id}/decide`) a human/console calls to approve or reject. | The approval interface the React console's Approve/Reject buttons drive. | — |
| `get_approval(id)` / `list_approvals()` | REST reads (`GET /approvals/{id}`, `GET /approvals`) of the pending queue. | Lets the HTTP gate poll for a decision across containers, and the dashboard show what's pending. | — |
| `write_audit(record)` | Appends an immutable `AuditRecord`. | The compliance backbone (NIST AI RMF / DORA / EU AI Act). | `AuditSink` |
| `register_playbook()` / `list_playbooks()` | Maintains the CoE playbook registry. | Standardization — playbooks are shared, not reinvented per team. | playbook store |

### 5.6 `feedback-service` — close the loop and prove the ROI

| Function | What it does | Why | Depends on |
|----------|--------------|-----|-----------|
| `label_outcome(outcome) → TrainingRecord` | Turns a `RemediationOutcome` into a labeled training example (worked / failed / rolled back). | Converts operational results into learning signal — the innovation. | `contracts.RemediationOutcome` |
| `persist(record)` | Writes the labeled record to the training store `correlation-service` reads. | The physical link that closes the loop. | training store |
| `compute_metrics()` | Tracks success/rollback/failure rates and per-signature reliability from outcomes. | Proves remediation quality with real numbers. *(True MTTR/MTTD, which need detection→resolution timestamps, are computed by the read-service — see §5.7.)* | outcome + situation history |

### 5.7 `read-service` — the CQRS read side the dashboard reads from

The one service that sees a situation's whole lifecycle (`first_seen` → outcome `ts`), so it is
where live KPIs are computed truthfully. Holds no source-of-truth state — a rebuildable
projection of the event streams ([ADR-009](architectural.md#adr-009--a-read-model-service-cqrs-for-the-dashboard)).

| Function | What it does | Why | Depends on |
|----------|--------------|-----|-----------|
| `apply_detected/diagnosed/outcome/suppressed(...)` | Folds each event into an in-memory projection keyed by situation id, mapping backend contracts to the exact shapes the React types expect. | One place owns the read shape, so the UI needs no translation layer. | the four situation/outcome topics |
| `situations(now_ms)` / `outcomes()` | Serve the live incident queue and outcome ticker (`GET /situations`, `GET /outcomes`). Ages out resolved/failed situations past a TTL and caps the total; **active situations are never pruned.** | Keeps the queue legible over long runs without touching the durable event log. | the projection |
| `metrics()` | Computes the 8 dashboard KPIs — noise-reduction, **real MTTR** (mean of `outcome.ts − first_seen` over resolved situations), auto-remediated %, success rate, open/pending counts, suppressed count (`GET /metrics`). | The Overview tiles read this — every number is derived, nothing fabricated. | the projection |
| `reset()` | Clears the projection (`POST /reset`). Paired with correlation's `POST /reset-baseline` and demo-app's `/fix` (see [`scripts/reset.sh`](../scripts/reset.sh)). | A one-command clean slate for repeatable simulation runs — **a simulation control, not a production endpoint.** | the projection |

## 6. Two worked scenarios

**Scenario A — noise gets collapsed (Phase 1).** A deploy triggers 200 correlated alerts.
`ingestion` normalizes and dedups them; `correlation.detect_anomaly` flags the abnormal ones;
`correlation.correlate` clusters them into **one** `Situation` on `situations.detected`. A
responder sees a single incident instead of 200 pages. *No action is taken* — Phase 1 stops at
noise reduction.

**Scenario B — a full closed loop (Phase 3–4).** The same `Situation` is diagnosed by `rca`
(top hypothesis: a crash-looping pod after the deploy; runbook: restart-pod). `action.select_playbook`
picks `restart-pod` (mode `hitl`, reversible). `action.request_approval` blocks on
`governance`; an on-call engineer POSTs approve to `governance.decide`. `action.execute`
restarts the pod, `verify_health` passes, `emit_outcome` reports `success`. `feedback.label_outcome`
records it; after enough successes, governance graduates `restart-pod` to `auto`
([ADR-008](architectural.md#adr-008--three-hitl-modes-graduating-by-evidence)). On the next
retrain, `correlation` has learned this signature-plus-fix pair.

## 7. Build phasing (delivered)

The functions above were delivered in slices mapped to the proposal's Phase 0–4. **All four
slices are complete** — each is a working vertical.

| Slice | Proposal phase | What runs at the end of it | Status |
|-------|----------------|----------------------------|--------|
| **0** | — | Skeleton: `common/` contracts + interfaces, Redis bus, `docker-compose up`, health endpoints on all six services. | ✅ |
| **1** | Phase 1 | `ingestion → correlation`: real telemetry in, `Situation` out. (Scenario A works.) | ✅ |
| **2** | Phase 2 | `rca-service` enrichment + hypotheses; `governance` audit + RBAC live. | ✅ |
| **3** | Phase 3 | `action-service` runs one `hitl` reversible playbook end to end with approval + rollback. (Scenario B works.) | ✅ |
| **4** | Phase 4 | `feedback-service` closes the loop; metrics; first playbook graduated to `auto`. | ✅ |

**Since the original slices**, three things were added that the first draft didn't design:
the **read-service** (§5.7), the **React operator console**, and a **live, breakable demo
stack** (a demo target app + real Prometheus + a scenario-reset script) so the whole loop can
be driven and re-driven on docker-compose. See §8.

## 8. Current status & what's next

**What's real, live, end-to-end today** (verified on `docker compose up`):
- The full six-service closed loop, plus the read-service and the React console.
- Real Prometheus scraping a breakable demo app; anomalies detected by the online correlator;
  situations diagnosed; the **HITL approval gate working across containers**; a human approves
  in the console; the loop resolves; live KPIs (real MTTR, noise-reduction) populate.
- Repeatable simulations via `scripts/reset.sh` (recover demo, forget detector baseline, empty
  the read model — no docker restart).

**Persistence is real — behind a backend switch.** The audit log, playbook registry, and
training store can be backed by **Postgres** (`STORE_BACKEND=postgres`), which is the compose
default: a `postgres:16-alpine` service, Alembic migrations applied by a one-shot `migrate`
step, and a hybrid schema (indexed columns + a JSONB payload that is the source of truth).
`file` stays the default for tests and quick dev without Docker. See
[docs/PERSISTENCE.md](docs/PERSISTENCE.md) and
[ADR-014](architectural.md#adr-014--postgres-persistence-with-a-hybrid-schema).

**Remediation is real — on an opt-in kind cluster.** Behind `REMEDIATOR_MODE=k8s` /
`HEALTH_CHECK_MODE=k8s`, `action-service` drives a real Kubernetes API: `KubernetesRemediator`
restarts, scales, and rolls back an actual deployment via typed `AppsV1Api` calls (no shell, no
string parsing, never deletes), and `KubernetesHealthChecker` confirms recovery from pod
readiness plus a live Prometheus query, polled to a timeout. "Resolved" in this mode means the
pod was actually restarted. This is a **documented runbook against a local kind cluster** —
[deploy/k8s/README.md](deploy/k8s/README.md) — **not** part of CI; CI/pytest bind fake K8s
clients and never touch a real cluster.

**Dry-run stays the default everywhere else** (compose without the k8s overlay, tests, CI):
`REMEDIATOR_MODE` defaults to `dry_run` — `DryRunRemediator` logs the steps and
`AlwaysHealthyChecker` reports success, so "resolved" means *the fix was logged and simulated*,
no real infrastructure touched ([ADR-007](architectural.md#adr-007--reversible-only-health-verified-remediation)).

**What is still deliberately simulated / deferred:**
- **No auth** on the read/console endpoints; the reset/break/fix endpoints are simulation
  controls, not production endpoints.
- Smarter detection, observability, and demo/report polish, per [WORKPLAN.md](WORKPLAN.md).
