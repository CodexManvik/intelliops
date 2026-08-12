# IntelliOps CoE — Design Spec

**Date:** 2026-08-13
**Status:** Approved for documentation; build to follow phase-by-phase
**Source of truth:** `IntelliOps_CoE_Capstone_Response_v2-1.pdf` (capstone proposal)
**Scope of this spec:** the concrete, buildable blueprint derived from the proposal — the system this repo will implement.

> **Note on invented decisions.** The capstone PDF defines the *what* (problem, 5 conceptual
> layers, phased roadmap, tech-stack intent). It does **not** specify service boundaries,
> data contracts, or function-level design. Every such decision in this spec is an
> engineering choice made to produce a buildable system. Each is tagged **[INVENTED]** and,
> where it carries trade-offs, recorded as a numbered ADR in `architectural.md`.

---

## 1. Purpose & problem (from the proposal)

Modern cloud-native estates emit more telemetry than humans can triage. Enterprises see
**500–1,200 alerts/day**, the large majority noise (Covasant, 2026). Downtime averages
**~$15,000/minute** (Splunk/Cisco with Oxford Economics, 2026 — verified against primary
press release). SRE/DevOps time is consumed by manual log correlation and alert triage
instead of resolution; alert fatigue drives on-call burnout and keeps MTTR in the
hours-not-minutes range.

**IntelliOps CoE** is an Agentic AIOps platform that:
1. Ingests existing telemetry (metrics, logs, traces) — *augments* observability, does not replace it.
2. Uses ML to **collapse alert storms** into a small number of meaningful *Situations*.
3. Produces **root-cause hypotheses** and surfaces runbooks for responders.
4. Executes **HITL-gated, reversible auto-remediation** for pre-approved low-risk actions.
5. **Closes the loop**: every remediation outcome becomes training data (the key innovation).
6. Wraps everything in a **governed Center of Excellence** — RBAC, audit, rollback,
   playbook standardization (the structural innovation).

## 2. Goals & non-goals

**Goals**
- Reduce MTTD and MTTR for production incidents (proposal target: 40–60% MTTR reduction).
- Cut alert volume reaching humans by 80–95% without losing genuine-incident signal.
- Provide a *governed, reusable* automation foundation (a CoE), not a one-off script.
- Be open-source-first and platform-agnostic (avoid vendor lock-in).

**Non-goals (initially)**
- Open-ended autonomous remediation. Actions are scoped to pre-approved, low-risk,
  **reversible** playbooks only.
- Replacing existing observability, ticketing, or CI/CD systems.
- A polished approval **UI** — Phase 1 uses a REST approval endpoint; UI comes later.
- Fully automated model retraining on day one — the *plumbing* exists from day one; the
  trigger is manual/scheduled early and automated as a maturity milestone.

## 3. Assumptions (from the proposal)

- Baseline observability (metrics/logs/traces) is already instrumented.
- ≥3–6 months of historical incident data is available for model training.
- Existing CI/CD and incident-management/ticketing tooling exists to integrate with.
- Auto-remediation is limited to reversible actions (e.g. restart a pod, scale a service).

## 4. Architecture

### 4.1 Conceptual layers (proposal) → concrete services (this spec) **[INVENTED]**

The proposal's 5 layers become **6 deployable Python/FastAPI services + a shared library**.
The event bus and the feedback loop are promoted to first-class components rather than being
folded into a layer.

| # | Service | Proposal layer | Purpose |
|---|---------|----------------|---------|
| 1 | `ingestion-service` | Data | Pull/receive metrics·logs·traces, normalize to `TelemetryEvent`, publish to bus |
| 2 | `correlation-service` | Correlation/ML | Anomaly detection + event clustering → `Situation` |
| 3 | `rca-service` | Correlation/ML (RCA) | Enrich Situation with change/deploy context; rank root-cause hypotheses; surface runbooks |
| 4 | `action-service` | Action | Match Situation→playbook; request HITL approval; execute reversible remediation; verify; rollback |
| 5 | `governance-service` | Governance/CoE | RBAC gate, immutable audit log, playbook registry, approval workflow |
| 6 | `feedback-service` | Feedback loop | Label remediation outcomes; persist as training data; compute MTTR/MTTD metrics |
| — | `common/` (shared lib) | cross-cutting | Data contracts, bus client, config, pluggable adapter interfaces |

### 4.2 Event bus **[INVENTED: Kafka default / Redis Streams dev]**

Services are decoupled producers/consumers on a message bus. This directly serves the
proposal's "horizontally scalable ingestion" and "platform-agnostic" goals. Default binding
is **Kafka** for production and **Redis Streams** for local dev, behind a `BusClient`
interface so either is swappable.

**Topics**

| Topic | Produced by | Consumed by | Payload |
|-------|-------------|-------------|---------|
| `telemetry.raw` | ingestion | correlation | `TelemetryEvent` |
| `situations.detected` | correlation | rca | `Situation` (status=detected) |
| `situations.diagnosed` | rca | action | `Situation` (status=diagnosed) + hypotheses |
| `remediation.outcomes` | action | feedback | `RemediationOutcome` |
| `audit.events` | all services | governance | `AuditRecord` (fire-and-forget) |

### 4.3 The one synchronous call **[INVENTED: governance as active gate]**

Everything flows asynchronously over the bus **except** `action-service → governance-service`
for the RBAC + approval decision. That call is **synchronous by design**: an action must not
proceed until governance authorizes it. This is the structural enforcement behind the
proposal's HITL/RBAC promise — governance is an *active gate*, not a passive log.

### 4.4 Closed loop

`feedback-service` writes labeled outcomes to a training store that `correlation-service`
reads at retrain time. Retraining is manual/scheduled in early phases and automated later;
the loop's *plumbing* is present from the first build so accuracy can compound over time.

## 5. Data contracts (`common/contracts.py`, Pydantic) **[INVENTED]**

```
TelemetryEvent      source, kind(metric|log|trace), name, value|payload, labels{}, ts, fingerprint
Situation           id, status(detected|diagnosed|acting|resolved|failed), member_events[],
                    severity, first_seen, last_seen, signature
RootCauseHypothesis situation_id, description, confidence, evidence[], suggested_runbook_id
Playbook            id, name, match_rule, steps[], hitl_mode(auto|hitl|disabled),
                    reversible(bool), rollback_steps[]
ApprovalRequest     id, situation_id, playbook_id, requested_by,
                    status(pending|approved|rejected), decided_by
RemediationOutcome  situation_id, playbook_id, result(success|failure|rolled_back),
                    health_after, ts
AuditRecord         actor, action, resource, decision, ts, correlation_id   (immutable)
```

**Two threading decisions [INVENTED]:**
- `Situation.signature` — stable content-hash so recurring incidents are recognized across time.
- `correlation_id` — threaded through every `AuditRecord` so one incident is traceable
  end-to-end across all six services.

## 6. Pluggable adapter interfaces (`common/interfaces.py`) **[INVENTED]**

Named default tools sit behind interfaces so they are swappable (proposal: avoid lock-in).

| Interface | Methods | Default impls |
|-----------|---------|---------------|
| `TelemetrySource` | `poll()`, `subscribe()` | Prometheus, Loki, OTel |
| `Correlator` | `detect()`, `correlate()`, `retrain()` | River (online ML), scikit-learn |
| `Remediator` | `execute()`, `rollback()` | Kubernetes API, Ansible |
| `AuditSink` | `write()` | Postgres, file |
| `BusClient` | `publish()`, `consume()` | Kafka, Redis Streams |

## 7. Per-service functions (the "what & why")

**ingestion-service** — `normalize(raw)→TelemetryEvent` (canonical shape; downstream is
source-agnostic); `compute_fingerprint(event)` (dedup at the door); `run_poll_loop()`.

**correlation-service** — `detect_anomaly(event)→score` (noise-reduction engine);
`correlate(anomalies)→Situation` (collapse storm into one situation); `load_model()` /
`retrain(training_data)` (closed loop lands here).

**rca-service** — `enrich(situation)→context` (deploys/config/topology); `rank_hypotheses(
situation, context)→[RootCauseHypothesis]`; `surface_runbook(hypothesis)→Playbook`.

**action-service** — `select_playbook(situation)→Playbook`; `request_approval(playbook,
situation)→decision` (**sync** governance call — the HITL gate); `execute(playbook)` /
`verify_health()` / `rollback(playbook)` (reversible-only enforced in code); `emit_outcome(...)`.

**governance-service** — `check_rbac(actor,action,resource)→allow|deny`; `create_approval_request()`
/ `decide(id, approve|reject)` (REST approval endpoints); `write_audit(record)` (append-only);
`register_playbook()` / `list_playbooks()` (CoE registry).

**feedback-service** — `label_outcome(outcome)→TrainingRecord`; `persist(record)`;
`compute_metrics()` (MTTR/MTTD/precision over time → proves ROI).

## 8. HITL modes

Each playbook declares one mode:
- `auto` — low-risk, reversible, pre-approved → executes after an RBAC check only.
- `hitl` — pauses for a human approve/reject via the governance REST endpoint.
- `disabled` — never executes; surfaced to responders as a suggestion only.

Phase 3 starts every playbook at `hitl`; a playbook graduates to `auto` only after a
measured track record (enforced/reviewed via governance).

## 9. Repository layout

```
intelliops/
├── README.md  architectural.md  flow.md
├── docs/superpowers/specs/2026-08-13-intelliops-coe-design.md
├── common/            contracts.py  interfaces.py  bus.py  config.py
├── services/          ingestion/ correlation/ rca/ action/ governance/ feedback/
│                        each: app.py (FastAPI)  handlers.py  adapters/  tests/
├── playbooks/         *.yaml   (CoE registry seed)
├── deploy/            docker-compose.yml (dev: Redis bus)  k8s/ (later)
├── pyproject.toml     .gitignore
```

**[INVENTED]** Monorepo with a shared `common/` lib (not 6 repos) — keeps contracts in one
place so services can't drift; right fit for a solo/small-team phased build.

## 10. Build phasing (mapped to proposal Phase 0–4)

| Slice | Proposal phase | Deliverable |
|-------|----------------|-------------|
| Slice 0 | — | Skeleton: repo, `common/` contracts+interfaces, Redis bus, docker-compose, health endpoints |
| Slice 1 | Phase 1 | `ingestion → correlation` vertical slice: telemetry in → `Situation` out |
| Slice 2 | Phase 2 | `rca-service` + enrichment; `governance` audit + RBAC |
| Slice 3 | Phase 3 | `action-service` with one `hitl` reversible playbook (restart-pod) + approval + rollback |
| Slice 4 | Phase 4 | `feedback-service` closes the loop; metrics; graduate a playbook to `auto` |

Each slice gets explicit approval before build.

## 11. Testing strategy

- **Contract tests** on `common/` models (serialization round-trips) — contracts are load-bearing.
- **Per-service unit tests** with adapters mocked (`FakeBus`, `FakeRemediator`) — every
  service testable in isolation (the reason the interfaces exist).
- **One integration test per slice** running the vertical path over the dev bus.
- TDD (red → green → refactor) once in code.

## 12. Security & compliance mapping (from proposal)

- **RBAC-gated** remediation with **full audit trails** → NIST AI RMF *Govern/Map/Measure/Manage*.
- **HITL approval** for anything beyond pre-approved low-risk playbooks → EU AI Act risk-tiered
  documentation obligations.
- Fast internal MTTD/MTTR supports **DORA's 4-hour major-incident notification** window
  (note: DORA requires *notification*, not a fixed recovery-time mandate).
- Deployable in-region/on-prem for sovereign-cloud requirements.

## 13. Tech stack

Python 3.11+ / FastAPI · Pydantic contracts · Kafka (prod) / Redis Streams (dev) · River +
scikit-learn (anomaly detection/correlation) · Prometheus / Loki / OpenTelemetry (telemetry
sources) · Kubernetes API + Ansible (remediation) · Postgres (audit + training store) ·
Docker Compose (dev) → Kubernetes (later) · pytest. Open-source-first, with an optional
integration path to commercial correlation engines (Moogsoft/BigPanda/Dynatrace) and
ticketing/on-call (PagerDuty) as adapters.

## 14. Open questions (deferred, not blocking docs)

- Concrete anomaly-detection algorithm choice per signal type (metric vs. log) — Slice 1 spike.
- Training-store schema for labeled outcomes — finalized in Slice 4.
- Approval UI and ChatOps (Slack/PagerDuty) integration — post-Phase-3.
