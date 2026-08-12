# IntelliOps CoE — Flow & Function Reference

This document tracks **how a signal flows through the system** and **what each function does
and why**. Read it alongside:

- [architectural.md](architectural.md) — *why* the system is shaped this way (ADRs).
- [docs/superpowers/specs/2026-08-13-intelliops-coe-design.md](docs/superpowers/specs/2026-08-13-intelliops-coe-design.md) — the full spec.

> **Status.** This describes the target design. The repo is being built phase by phase
> (see the [phase table](#7-build-phasing) at the end); function signatures below are the
> contract each phase implements, not code that all exists yet.

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
```

**The single synchronous step** is `action-service → governance-service` (step 6). Everything
else is asynchronous over the bus. The gate fails **closed**: no governance approval → no
action. See [ADR-003](architectural.md#adr-003--governance-is-an-active-gate-not-passive-logging).

## 2. Bus topics (the wiring)

| Topic | Produced by | Consumed by | Payload | Meaning |
|-------|-------------|-------------|---------|---------|
| `telemetry.raw` | ingestion | correlation | `TelemetryEvent` | a normalized signal |
| `situations.detected` | correlation | rca | `Situation` (detected) | an alert storm collapsed into one incident |
| `situations.diagnosed` | rca | action | `Situation` (diagnosed) + hypotheses | incident with likely cause + suggested fix |
| `remediation.outcomes` | action | feedback | `RemediationOutcome` | did the fix work / roll back |
| `audit.events` | all services | governance | `AuditRecord` | append-only trail (fire-and-forget) |

## 3. Shared data contracts (`common/contracts.py`)

These are the nouns every service passes around. Defined once in `common/` so services can't
drift ([ADR-006](architectural.md#adr-006--monorepo-with-a-shared-common-library)).

| Contract | Key fields | Why it exists |
|----------|-----------|---------------|
| `TelemetryEvent` | `source, kind, name, value/payload, labels, ts, fingerprint` | Canonical shape so every downstream service is source-agnostic. |
| `Situation` | `id, status, member_events[], severity, first_seen, last_seen, signature` | The universal incident currency; `signature` recognizes recurrences ([ADR-004](architectural.md#adr-004--situation-as-the-universal-currency)). |
| `RootCauseHypothesis` | `situation_id, description, confidence, evidence[], suggested_runbook_id` | Makes RCA output rankable and actionable, not just prose. |
| `Playbook` | `id, name, match_rule, steps[], hitl_mode, reversible, rollback_steps[]` | A remediation with its safety scope and undo baked in ([ADR-007](architectural.md#adr-007--reversible-only-health-verified-remediation)). |
| `ApprovalRequest` | `id, situation_id, playbook_id, requested_by, status, decided_by` | The record a human approves/rejects at the HITL gate. |
| `RemediationOutcome` | `situation_id, playbook_id, result, health_after, ts` | The feedback signal that closes the loop. |
| `AuditRecord` | `actor, action, resource, decision, ts, correlation_id` | Immutable compliance trail; `correlation_id` threads one incident end to end. |

## 4. Pluggable interfaces (`common/interfaces.py`)

The swap points that keep the system platform-agnostic and testable
([ADR-005](architectural.md#adr-005--pluggable-adapters-behind-interfaces)).

| Interface | Methods | Default implementations |
|-----------|---------|-------------------------|
| `TelemetrySource` | `poll()`, `subscribe()` | Prometheus, Loki, OpenTelemetry |
| `Correlator` | `detect()`, `correlate()`, `retrain()` | River (online), scikit-learn |
| `Remediator` | `execute()`, `rollback()` | Kubernetes API, Ansible |
| `AuditSink` | `write()` | Postgres, file |
| `BusClient` | `publish()`, `consume()` | Kafka (prod), Redis Streams (dev) |

Tests bind fakes (`FakeBus`, `FakeRemediator`) to exercise a service in isolation.

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
| `decide(id, approve \| reject)` | REST endpoints a human/ChatOps calls to approve or reject. | The Phase-1 approval interface (UI comes later). | — |
| `write_audit(record)` | Appends an immutable `AuditRecord`. | The compliance backbone (NIST AI RMF / DORA / EU AI Act). | `AuditSink` |
| `register_playbook()` / `list_playbooks()` | Maintains the CoE playbook registry. | Standardization — playbooks are shared, not reinvented per team. | playbook store |

### 5.6 `feedback-service` — close the loop and prove the ROI

| Function | What it does | Why | Depends on |
|----------|--------------|-----|-----------|
| `label_outcome(outcome) → TrainingRecord` | Turns a `RemediationOutcome` into a labeled training example (worked / failed / rolled back). | Converts operational results into learning signal — the innovation. | `contracts.RemediationOutcome` |
| `persist(record)` | Writes the labeled record to the training store `correlation-service` reads. | The physical link that closes the loop. | training store |
| `compute_metrics()` | Tracks MTTR, MTTD, and correlation precision over time. | Proves the 40–60% MTTR / 80–95% alert-reduction claims with real numbers. | outcome + situation history |

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

## 7. Build phasing

The functions above are delivered in slices mapped to the proposal's Phase 0–4. Each slice is
a working vertical, and each gets explicit approval before it's built.

| Slice | Proposal phase | What runs at the end of it |
|-------|----------------|----------------------------|
| **0** | — | Skeleton: `common/` contracts + interfaces, Redis bus, `docker-compose up`, health endpoints on all six services. |
| **1** | Phase 1 | `ingestion → correlation`: real telemetry in, `Situation` out. (Scenario A works.) |
| **2** | Phase 2 | `rca-service` enrichment + hypotheses; `governance` audit + RBAC live. |
| **3** | Phase 3 | `action-service` runs one `hitl` reversible playbook end to end with approval + rollback. (Scenario B works.) |
| **4** | Phase 4 | `feedback-service` closes the loop; metrics dashboard; first playbook graduated to `auto`. |
