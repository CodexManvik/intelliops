# IntelliOps CoE — Architecture & Decisions

This document explains **why the system is shaped the way it is**. It covers the layer model,
the major architectural decisions (as numbered ADRs with context, trade-offs, and rejected
alternatives), and how the design maps to the compliance obligations the project targets.

- For **what each part does and how a signal flows through it**, see [flow.md](flow.md).
- For the **full design spec** this document draws from, see
  [docs/superpowers/specs/2026-08-13-intelliops-coe-design.md](docs/superpowers/specs/2026-08-13-intelliops-coe-design.md).

> **Provenance.** The capstone proposal defines the problem, the five conceptual layers, the
> phased roadmap, and the tech-stack intent. It does **not** specify service boundaries, data
> contracts, or component design. Those are engineering decisions made here to produce a
> buildable system, and each one that carries a trade-off is recorded below as an ADR.

---

## 1. Design principles

Everything below serves five principles, taken directly from the proposal's intent:

1. **Augment, don't replace.** IntelliOps sits *alongside* existing observability, CI/CD, and
   ticketing. It consumes their output; it never asks to be the system of record.
2. **Open-source-first, lock-in-averse.** Named default tools are open source, and each one
   sits behind an interface so a commercial or different tool can be swapped in.
3. **Human-in-the-loop by construction.** Automated action is *structurally* gated on a
   governance decision — the guardrail is enforced in the call graph, not by convention.
4. **Reversible-only automation.** The system only ever automates actions it can undo, and
   it verifies health after acting so it can roll back.
5. **The loop must close.** Remediation outcomes are training data. The plumbing that carries
   outcomes back to the model exists from the first build, because that feedback loop is the
   project's central innovation.

## 2. From five conceptual layers to six services

The proposal describes five layers: **Data → Correlation/ML → Action → Governance → Feedback**.
This architecture implements them as **six deployable services plus a shared library** —
the *write* side of the system. A seventh service, the **read-service**, was added later as the
CQRS read side that serves the dashboard ([ADR-009](#adr-009--a-read-model-service-cqrs-for-the-dashboard)),
and a **React operator console** consumes it. The layer mapping is deliberately not one-to-one:

```
 Proposal layer          Implemented as
 ─────────────────────   ───────────────────────────────────────────
 Data                →   ingestion-service
 Correlation/ML      →   correlation-service  +  rca-service   (split — see ADR-002)
 Action              →   action-service
 Governance/CoE      →   governance-service   (active gate — see ADR-003)
 Feedback loop       →   feedback-service
 (read side)         →   read-service         (CQRS projection — see ADR-009)
 (operator UI)       →   React console        (reads read-service + governance)
 (cross-cutting)     →   common/  shared library (contracts + interfaces + bus)
 (spine)             →   event bus                              (see ADR-001)
```

### The six services at a glance

```
                    ┌───────────────────────────────────────────────┐
                    │   governance-service  (RBAC · audit · registry)│
                    │            ▲ sync gate       ▲ audit (async)    │
                    └────────────┼─────────────────┼─────────────────┘
                                 │                 │
  telemetry     ┌──────────┐   ┌─┴────────────┐  ┌─┴─────────┐   ┌────────────┐
  sources  ───▶ │ingestion │──▶│ correlation  │─▶│    rca    │──▶│   action   │
  (Prom/Loki/   └──────────┘   └──────┬───────┘  └───────────┘   └─────┬──────┘
   OTel)          telemetry.raw       │ situations.detected            │ remediation.outcomes
                                      │ ▲                              ▼
                                      │ │ retrain              ┌──────────────┐
                                      │ └──────────────────────│  feedback    │
                                      │      training store    └──────────────┘
                                      └──────────  CLOSED LOOP  ───────────────┘
```

Everything moves over the **event bus** asynchronously, with a single exception: the
`action → governance` approval call is synchronous (ADR-003).

---

## 3. Architecture Decision Records

Each ADR follows: **Context → Decision → Why → Consequences → Alternatives rejected.**

### ADR-001 — Event bus as the spine

**Context.** Six services must exchange a growing volume of telemetry and derived events. The
proposal calls for "horizontally scalable ingestion" and a "platform-agnostic correlation
layer."

**Decision.** Services communicate through a **message bus**, as decoupled producers and
consumers, over a fixed set of named topics. Default binding: **Kafka** in production,
**Redis Streams** for local development, both behind a `BusClient` interface.

**Why.** A bus decouples producers from consumers, lets each service scale independently,
absorbs alert-storm bursts as backpressure instead of dropping data, and makes the pipeline
observable topic-by-topic. Redis-in-dev keeps the local stack to a single lightweight
dependency; Kafka-in-prod gives durability and partitioned throughput when telemetry grows.

**Consequences.** (+) Independent scaling and clean service isolation. (+) New consumers can
subscribe without touching producers. (−) A bus is operational surface area, and
eventual-consistency semantics must be reasoned about. Mitigated by hiding both brokers
behind one interface and keeping dev on Redis.

**Alternatives rejected.** *Direct HTTP between services* — tight coupling, no burst
absorption, cascading failures. *A shared database as the queue* — turns the DB into a
bottleneck and couples schemas; explicitly the anti-pattern the bus avoids.

### ADR-002 — Split RCA into its own service

**Context.** The proposal groups anomaly detection, correlation, and RCA suggestion under one
"Correlation/ML" layer. But correlation ships in **Phase 1** and RCA in **Phase 2**.

**Decision.** Keep **`correlation-service`** (detect + cluster → `Situation`) separate from
**`rca-service`** (enrich + rank causes + surface runbook).

**Why.** They have different lifecycles, dependencies, and scaling profiles. Correlation is a
hot, high-throughput consumer of raw telemetry. RCA is a lower-frequency enrichment step that
reaches out to *other* systems (deploy history, config/change data, topology). Splitting them
lets Phase 1 ship and run without any RCA dependency, and lets each scale and fail
independently.

**Consequences.** (+) Phase-aligned, independently deployable, independently testable.
(+) RCA's external integrations can't destabilize the hot correlation path. (−) One extra
service and one extra topic hop (`situations.detected → situations.diagnosed`). Accepted: the
hop is cheap and the isolation is worth it.

**Alternatives rejected.** *One combined ML service* — couples a Phase-1 deliverable to
Phase-2 integrations and mixes a hot path with a slow enrichment path in one deployable.

### ADR-003 — Governance is an active gate, not passive logging

**Context.** The proposal requires RBAC on automated actions, full audit trails, HITL
approval, and rollback. A tempting reading is "log everything and add RBAC later."

**Decision.** `governance-service` is a **control plane every action must pass through
synchronously**. Before `action-service` executes any remediation it makes a **blocking**
call to governance for the RBAC + approval decision. Governance also receives asynchronous
audit events from every service.

**Why.** Making governance an active gate moves the HITL/RBAC guarantee from *convention*
(hopefully everyone logs and checks) to *structure* (an action **cannot** execute without a
governance yes). This is the enforceable teeth behind the compliance story — the difference
between "we have audit logs" and "no unauthorized action is possible."

**Consequences.** (+) The guardrail is impossible to bypass by construction. (+) One place
owns RBAC policy, the approval workflow, and the playbook registry (the CoE). (−) Governance
is now on the critical path for actions, so it must be highly available; and the synchronous
call is a deliberate exception to the otherwise-async design. Accepted — correctness of the
gate outranks purity of "everything async."

**Alternatives rejected.** *Passive audit sink + RBAC in each service* — scatters policy,
lets services drift, and cannot actually *prevent* an unauthorized action, only record it
after the fact.

### ADR-004 — `Situation` as the universal currency

**Context.** Services need a shared notion of "an incident in progress" to hand off between
correlation, RCA, and action.

**Decision.** A single `Situation` object is the currency passed between services. It carries
a lifecycle status (`detected → diagnosed → acting → resolved | failed`), its member telemetry
events, severity, timestamps, and a stable **`signature`** (content-hash).

**Why.** One well-defined object per incident means every service speaks the same language and
the incident is traceable as it moves through the pipeline. The `signature` lets the system
recognize a **recurring** incident across time — essential for "have we seen this before, and
did the fix work last time?", which is what makes the feedback loop valuable.

**Consequences.** (+) Clean handoffs; the object *is* the API between stages. (+) Recurrence
detection comes for free from the signature. (−) The contract is load-bearing and changes to
it ripple across services — which is exactly why contracts live in `common/` and are tested
first.

**Alternatives rejected.** *Ad-hoc per-service payloads* — every handoff becomes a
translation, and recurrence is impossible to detect without a stable identity.

### ADR-005 — Pluggable adapters behind interfaces

**Context.** The proposal is emphatic about avoiding vendor lock-in and staying
platform-agnostic, while still naming concrete tools.

**Decision.** Name concrete **default** tools (Prometheus, Loki, OTel, River, scikit-learn,
Kubernetes API, Ansible, Kafka/Redis, Postgres) but put each behind an interface
(`TelemetrySource`, `Correlator`, `Remediator`, `AuditSink`, `BusClient`). Tool choice is a
config-time binding, not a code-time assumption.

**Why.** This is the concrete mechanism that makes "platform-agnostic" real rather than
aspirational. It also makes the whole system testable: unit tests bind fake adapters
(`FakeBus`, `FakeRemediator`) and exercise a service in complete isolation.

**Consequences.** (+) Swap tools without touching business logic; test without real
infrastructure. (−) One layer of indirection per integration point. Accepted — the indirection
is what buys both the lock-in-aversion and the testability.

**Alternatives rejected.** *Hard-code the default tools everywhere* — maximal clarity for one
toolchain, but breaks the proposal's central lock-in-aversion promise and makes isolated
testing far harder.

### ADR-006 — Monorepo with a shared `common/` library

**Context.** Six services share data contracts and a bus client. Those contracts are
load-bearing and must not drift between services.

**Decision.** One repository. Shared contracts, interfaces, bus client, and config live in a
single `common/` library that every service imports.

**Why.** Contracts in one place cannot drift. A change to a contract is one edit, one review,
one test run across all consumers. For a solo/small-team, phase-by-phase build this is far
lower friction than versioning a contracts package across six repos.

**Consequences.** (+) No contract drift; atomic cross-service changes; one place to test the
contracts. (−) Services aren't independently versioned as separate repos. Accepted at this
stage; if the team and services grow, `common/` can be extracted into a versioned package
later without changing the code that imports it.

**Alternatives rejected.** *Six repos + a published contracts package* — real version-skew
risk and heavy release ceremony, unjustified at current scale.

### ADR-007 — Reversible-only, health-verified remediation

**Context.** Auto-remediation is the highest-risk capability. The proposal scopes it to
"pre-approved, low-risk, reversible actions" with rollback.

**Decision.** Every `Playbook` declares `reversible` and carries explicit `rollback_steps`.
`action-service` executes, then **verifies health**, and **rolls back** if the system is
unhealthy after acting. A playbook without a rollback path cannot run in `auto` mode.

**Why.** "Reversible-only" is a safety property that must be enforced, not documented. Tying
rollback steps to the playbook and gating `auto` on their presence makes an irreversible
automated action structurally impossible.

**Consequences.** (+) A failed remediation self-heals back to the prior state. (+) The safety
scope is machine-checkable. (−) Authoring a playbook costs more (you must define the undo).
Accepted — that cost *is* the safety.

**Alternatives rejected.** *Fire-and-forget remediation* — one bad automated action with no
undo is exactly the outcome the whole guardrail design exists to prevent.

### ADR-008 — Three HITL modes, graduating by evidence

**Context.** Trust in automation must be earned incrementally (the proposal's phased-rollout
thesis).

**Decision.** Each playbook is `auto`, `hitl`, or `disabled`. Phase 3 starts **every**
playbook at `hitl` (human approves each run). A playbook graduates to `auto` only after a
measured track record, reviewed through governance.

**Why.** This encodes "build trust incrementally" as a state machine per playbook rather than
a one-time global switch. It matches how organizations actually adopt automation — prove it on
a few actions, then widen.

**Consequences.** (+) Automation scope expands on evidence, not optimism. (+) The mode is data,
so graduation can be policy-driven. (−) Early on, humans are in the loop for every action —
which is the intended cost during trust-building.

**Alternatives rejected.** *Global auto/manual toggle* — all-or-nothing, ignores that
different playbooks earn trust at different rates.

---

> **ADRs 009–012 were added after the original six-service build**, as the design met contact
> with a real running stack and a UI. They record decisions the first draft didn't — the read
> side, the cross-container gate, the live demo harness, and how adapter selection actually works.

### ADR-009 — A read-model service (CQRS) for the dashboard

**Context.** The six services are event-driven producers/consumers — none of them is designed
to answer "what are all the open situations right now?" or "what's the live MTTR?" over HTTP.
The React console needs exactly those reads. Bolting query endpoints onto (say) correlation or
action would put a synchronous read path on a hot write service and scatter the read shape.

**Decision.** A separate **`read-service`** subscribes to `situations.detected`,
`situations.diagnosed`, `remediation.outcomes`, and `situations.suppressed`, folds them into an
**in-memory projection**, and serves `GET /situations`, `/outcomes`, `/metrics`. It holds no
source-of-truth state: the Redis event streams are the record, and the projection rebuilds from
them on startup. This is CQRS-lite — a read side separated from the write side.

**Why.** One place owns the read shape (mapped to exactly what the UI types expect, so there's
no translation layer), reads never touch the hot write path, and because the read-service sees
a situation's whole lifecycle (`first_seen` through the resolving outcome's `ts`) it can compute
**real** KPIs — true MTTR, noise-reduction, auto-remediated % — with no fabrication and no new
timestamp threading. A rebuildable projection means it can be wiped and restarted freely, which
is also what makes repeatable simulations cheap.

**Consequences.** (+) Clean read/write split; truthful live metrics; the dashboard reads plain
JSON. (+) The projection is a pure, deterministic structure — trivially unit-testable, no
wall-clock inside it (time is passed in). (−) One more service, and the read model is
eventually-consistent with the write side by up to one poll. Accepted — a dashboard number that
lags by a second is fine; a read query blocking the correlation hot path is not.

**Alternatives rejected.** *Query endpoints on the existing services* — couples reads to hot
write paths and spreads the read shape across services. *The frontend derives KPIs client-side
from raw events* — scatters metric logic into the UI, and every client recomputes.

### ADR-010 — Cross-container governance gate over HTTP (fail-closed)

**Context.** ADR-003 makes `action → governance` a synchronous gate. The first implementation
shared an in-memory approvals dict between the two — fine in one process and in tests, but in
docker-compose `action` and `governance` are **separate containers**, so the shared dict isn't
shared at all. A human approval written to governance's dict was invisible to action, which
polled its own empty copy: every HITL remediation timed out.

**Decision.** Keep the gate interface, but add an **`HttpGovernanceGate`** binding that talks to
governance over REST (`POST /approvals`, `GET /approvals/{id}`, `POST /rbac/check`,
`POST /audit`). It is selected by a `GOVERNANCE_MODE=in_process|http` switch — `in_process` (the
default) for single-process tests, `http` for the compose stack. `await_decision` polls
`GET /approvals/{id}` until non-pending or timeout.

**Why.** This makes the HITL gate — the centerpiece guarantee — actually work across the
deployed topology, without weakening the interface `remediate.py` depends on. Crucially, the
HTTP gate is **fail-closed by construction**: any network error, non-200, or malformed body
during a poll is caught and treated as *still pending*, so the caller never remediates on a
governance it couldn't reach (upholding ADR-003's fail-closed promise even under a flaky
network).

**Consequences.** (+) HITL works across containers; the gate degrades safely. (−) `await_decision`
blocks the action consumer thread while polling for a human decision (bounded by the HITL
timeout) — acceptable at one-incident-at-a-time demo scale. Accepted.

**Alternatives rejected.** *Approvals over the bus* — approvals are request/response with a
waiting caller, not a stream; a topic adds no value and complicates "has this specific approval
been decided yet?". *A shared database for approvals* — real, but heavier than the demo needs
when governance already owns the approval store behind REST.

### ADR-011 — A live, breakable demo harness with explicit simulation controls

**Context.** "The loop is closed" is only believable if you can *watch* it close on real,
moving data — and a capstone demo must be re-runnable on demand, not a one-shot. But the online
anomaly detector, by design, **learns**: after a few break/fix cycles it treats the injected
spike as normal and stops detecting.

**Decision.** Ship a **breakable demo target** (`services/demo_app` — a tiny FastAPI app that
emits Prometheus metrics and has `/break` and `/fix` toggles), a real **Prometheus** container
scraping it, and a **scenario-reset** path: correlation `POST /reset-baseline` (forget the
learned baseline), read `POST /reset` (empty the projection), and the demo `/fix`, composed by
`scripts/reset.sh`. Detection tuning (warm-up, z-threshold, window) is config-driven so the demo
detects within a minute while production defaults stay conservative.

**Why.** This turns "trust us, it works" into "run `docker compose up`, break the app, and watch
the incident flow to the approval gate." The reset path makes simulations repeatable without a
container restart, which is what lets the team iterate on scenarios.

**Consequences.** (+) A genuinely live, re-runnable demo on free local infra. (+) The same
`PrometheusSource` that scrapes the demo works against any real Prometheus later. (−) The
reset/break/fix endpoints are **operational surface that must not exist in production**. Accepted
and made explicit: they are documented as simulation controls, and gating/removing them is a
named follow-up when the stack points at a real system.

**Alternatives rejected.** *A static recorded dataset replayed through ingestion* — reproducible
but not a live loop you can perturb. *No reset (restart docker between runs)* — slow, and
deleting Redis streams to "reset" orphans consumer groups (observed to kill a consumer thread).

### ADR-012 — Config-switched adapter selection with test-safe defaults

**Context.** ADR-005 puts every integration behind an interface with a default binding. Once the
system had both test-only bindings (file source, in-process gate, dry-run remediator) and live
bindings (Prometheus source, HTTP gate), *how* the running binding is chosen became a decision
in its own right — and it must not let the deployed configuration change what the test suite
exercises.

**Decision.** Binding selection is an **environment switch with a test-safe default**:
`TELEMETRY_MODE=file|prometheus` (default `file`), `GOVERNANCE_MODE=in_process|http` (default
`in_process`), and correlation tuning knobs, all defaulting to the values the unit tests assume.
The docker-compose stack sets the live values; a bare `pytest` run gets the safe defaults.

**Why.** The default build stays deterministic and infra-free (tests never need Prometheus or a
second container), while the same code runs live by flipping env — no code branch, no separate
build. It keeps ADR-005's "config-time binding, not code-time assumption" literally true.

**Consequences.** (+) One codebase, two behaviors, chosen at deploy time; tests are hermetic.
(−) A new integration means a new switch and a documented default. Accepted — a small, explicit
cost per integration point.

**Alternatives rejected.** *Detect the environment at runtime* (e.g. "is Prometheus reachable?")
— implicit and non-deterministic, and it can make a test accidentally hit real infra. *Separate
prod/test builds* — drift risk between what's tested and what ships.

---

## 4. Cross-cutting concerns

**Traceability.** A `correlation_id` is threaded through every `AuditRecord`, so one
incident's full journey — detection, diagnosis, approval decision, action, outcome — is
reconstructable across all six services from the audit log alone.

**Failure handling.** Bus consumers are idempotent where possible (keyed on
`Situation.id` / event `fingerprint`) so redelivery is safe. The `action → governance` gate
fails **closed**: if governance is unreachable, the action does **not** proceed.

**Scalability.** The bus partitions by service; correlation (the hot path) scales
horizontally as a consumer group. RCA and action are lower-frequency and scale independently.

**Data at rest.** Audit records and labeled training outcomes live in Postgres (via
`AuditSink` / the training store). Both are deployable in-region/on-prem for sovereign-cloud
requirements.

## 5. Compliance mapping

| Obligation | How the architecture meets it |
|------------|-------------------------------|
| **NIST AI RMF** (Govern/Map/Measure/Manage) | Governance service centralizes policy (Govern), the `Situation`/audit model captures context (Map), `feedback-service` metrics quantify behavior (Measure), and RBAC + rollback + HITL enforce control (Manage). |
| **EU AI Act** (risk-tiered documentation for operational-decision systems) | HITL approval required for anything beyond pre-approved low-risk playbooks; every decision is audited with actor, resource, and outcome. |
| **DORA** (4-hour major-incident *notification*) | Faster MTTD/MTTR via correlation + RCA gives EU-regulated entities more runway to detect, assess, and notify within the window. *(Notification requirement — not a fixed recovery-time mandate.)* |
| **Sovereign cloud** | Open-source-first stack deployable in-region/on-prem; no hard dependency on a specific managed cloud service. |

## 6. What is built, and what is deliberately deferred

**Since the original ADRs, several deferred items shipped:**
- **Approval UI.** The REST approval endpoint now has a real front end — the React console's
  Incidents view drives `POST /approvals/{id}/decide` (Approve/Reject), with visible error
  feedback. (ChatOps — Slack/PagerDuty — is still deferred.)
- **Live metrics.** The read-service computes real MTTR/noise-reduction/rates from the situation
  lifecycle ([ADR-009](#adr-009--a-read-model-service-cqrs-for-the-dashboard)) — no longer a
  target, it runs.

**What remains deliberately deferred / simulated — the honest gap and the next milestones:**
- **Real remediation.** Remediation is **dry-run** today ([ADR-007](#adr-007--reversible-only-health-verified-remediation)):
  `DryRunRemediator` logs the steps; `AlwaysHealthyChecker` reports success. The real
  `KubernetesRemediator` + real health check are the **next major milestone** — scoped and owned
  in [WORKPLAN.md](WORKPLAN.md), targeting a local kind cluster.
- **Authentication / authorization at the edge.** RBAC gates *actions* internally, but the
  read/console/simulation endpoints have no auth. Deferred; planned in the workplan.
- **Automated model retraining.** The loop's *plumbing* exists; the retrain *trigger* is
  manual/scheduled, automated as a later maturity milestone.
- **Kafka in production.** Redis Streams runs dev and demo; the Kafka `BusClient` binding is
  deferred behind the same interface.
- **Simulation controls in production.** The `/break`, `/fix`, `/reset`, `/reset-baseline`
  endpoints ([ADR-011](#adr-011--a-live-breakable-demo-harness-with-explicit-simulation-controls))
  must be gated or removed when pointed at a real system.

These are maturity milestones, not gaps — calling them out is part of the design's rigor, and
the ones with a `→ WORKPLAN` pointer are actively being built next.
