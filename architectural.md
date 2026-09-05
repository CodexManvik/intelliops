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

> **Update.** The Postgres store adapters named here are now **built** — `PostgresAuditSink`,
> `PostgresPlaybookStore`, `PostgresTrainingStore` sit behind the `AuditSink` / `PlaybookStore` /
> `TrainingStore` interfaces, selected by the `STORE_BACKEND=file|postgres` switch. See
> [ADR-014](#adr-014--postgres-persistence-with-a-hybrid-schema).

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
Accepted — that cost *is* the safety. (+) This property is what let the real `KubernetesRemediator`
ship safely (behind `REMEDIATOR_MODE=k8s`) without weakening the guardrail — see
[ADR-013](#adr-013--structured-remediationplan-is-what-made-real-k8s-remediation-safe).

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

> **ADRs 009–013 were added after the original six-service build**, as the design met contact
> with a real running stack and a UI. They record decisions the first draft didn't — the read
> side, the cross-container gate, the live demo harness, how adapter selection actually works,
> and what made it safe to point remediation at a real cluster.

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

### ADR-013 — Structured `RemediationPlan` is what made real K8s remediation safe

**Context.** ADR-007 requires remediation to be reversible and health-verified, but the original
`Playbook.steps` were free-form strings (e.g. `"restart pod"`). That was fine for
`DryRunRemediator`, which only logs them — but a real adapter driving the Kubernetes API off
parsed strings would mean shell-outs or ad-hoc string matching against user-authored playbook
text: exactly the kind of untyped surface a "never delete, never do the wrong thing" guarantee
can't be built on.

**Decision.** Steps are a typed `RemediationStep` (`action: restart|scale|rollback_deploy|wait`,
plus a typed `replicas` delta and an optional note), and a `RemediationPlan` bundles the ordered
steps, their `rollback_steps`, and a `RemediationTarget` (`namespace`, `deployment`) resolved
once from the diagnosed `Situation`'s `service` label. `action-service` builds the `RemediationPlan`
before calling `Remediator.execute()`; the adapter never sees a raw string or the `Situation`
itself.

**Why.** A closed, typed vocabulary of actions is what let `KubernetesRemediator` map each step
directly to one typed `AppsV1Api` call (`patch_namespaced_deployment` for restart,
`patch_namespaced_deployment_scale` for scale) with no shell and no string parsing — there is no
input shape that can be coerced into an unintended API call. Resolving the target once, before
the adapter runs, also means the adapter itself never decides *what* to act on, only *how* —
keeping the blast radius of a bug in the adapter limited to the actions it's typed to perform,
never to a different deployment than the one the situation named.

**Consequences.** (+) The real remediator is exhaustively typeable and testable against a fake
`AppsV1Api` with no cluster in CI. (+) `KubernetesRemediator` is fail-safe by construction: any
`ApiException` or client error is caught and turns into `False`, never an escaped exception, and
the action set structurally excludes delete. (−) Adding a new remediation action means extending
the `RemediationStep` literal and every adapter's dispatch, not just writing a new playbook
string. Accepted — that's the same authoring cost ADR-007 already accepts, now paying off for a
real cluster instead of just a log line.

**Alternatives rejected.** *Keep free-form step strings and parse them in the K8s adapter* —
would have made the adapter's input surface exactly as unconstrained as shelling out, defeating
the purpose of a typed, fail-safe remediator. *Let the adapter resolve its own target from the
`Situation`* — duplicates resolution logic per adapter and lets an adapter act on a target the
rest of the pipeline never agreed on.

### ADR-014 — Postgres persistence with a hybrid schema

**Context.** ADR-005 names Postgres as the durable store behind the `AuditSink` / `PlaybookStore`
/ `TrainingStore` interfaces, but the only implementations were file-backed (JSONL logs, a YAML
playbook dir). Files are fine for tests and a single-process demo, but the audit log is the
compliance backbone (NIST AI RMF) and the training store is the closed loop's memory — both want
real durability, indexed queries (audit by `correlation_id`, training by `signature`), and a
schema that survives replicas. The open question was *how* to persist without forcing every model
change through a migration and without letting the database's shape drift away from the Pydantic
contracts.

**Decision.** Build Postgres adapters on **SQLAlchemy Core** (not the ORM) plus **Alembic**
migrations, with a **hybrid schema**: each of the three tables carries a set of *promoted* key
columns (indexed / queried) alongside a `JSONB` **`payload`** holding the full serialized record.
The **payload is the source of truth** — reads always reconstruct the Pydantic object from
`payload`, never from the columns, so the promoted columns are a denormalized index that steers
*which* rows are found but can never change *what* a row means. The tables are defined once as a
shared `MetaData` in `common/db.py` (used both by the adapters and by Alembic autogenerate).
Backend choice is a **`STORE_BACKEND=file|postgres` switch defaulting to `file`**, realized in
one factory (`common/stores.py` `make_stores`) shared by all four store-constructing services.
Persistence errors **propagate** — they are not caught and turned into a silent no-op.

**Why.** Core over the ORM keeps the mapping explicit and the payload literal — the adapter
serializes a Pydantic model to JSON and stores it, with no lazy-loading, session, or identity-map
machinery between the contract and the row. The hybrid schema gets both properties that matter:
indexed columns for the few real query paths, and a payload that means a model field added later
needs no migration to be *stored and read back* (only a migration if it must become a new indexed
column). Payload-as-source-of-truth is what makes the promoted columns safe — they can't silently
corrupt a reconstructed record. Alembic as a **dedicated migration step** (never auto-on-startup)
avoids the race where booting replicas all try to create the same tables; in compose this is the
one-shot `migrate` service the store services wait on
(`condition: service_completed_successfully`). The `file`-default switch follows ADR-012's
config-switch-with-test-safe-default pattern, so a bare `pytest` never needs a database.

**Consequences.** (+) Durable, queryable, replica-safe persistence for audit / playbooks /
training, with the model contract still owning the record shape. (+) One factory means the backend
can't split (governance writing playbooks to Postgres while rca reads files is not expressible).
(+) `playbooks` upserts on `id` via Postgres `ON CONFLICT DO UPDATE`, so re-registering (including
seed-on-init) is idempotent. (−) Two storage backends to keep behaviorally equivalent, and a
migration is required whenever a *promoted* column changes. Accepted — a cross-backend contract
test pins the file / in-memory / Postgres adapters to the same behavior.

**Errors propagate, deliberately unlike the remediator.** A failed audit or training write raises
rather than degrading to a swallowed no-op — a lost audit record is a compliance failure and must
be *visible*. This is the opposite of the fail-safe K8s remediator
([ADR-007](#adr-007--reversible-only-health-verified-remediation),
[ADR-013](#adr-013--structured-remediationplan-is-what-made-real-k8s-remediation-safe)), which
catches every API error and returns `False`. The postures diverge because the goals diverge: the
remediator must never *act* on uncertainty; the store must never *hide* a lost write.

**Alternatives rejected.** *The SQLAlchemy ORM* — more machinery (sessions, identity map, lazy
loading) than a serialize-to-JSONB adapter needs, and it blurs the line between the Pydantic
contract and the row. *A fully normalized schema* (a column per model field, no payload) — every
model change becomes a migration, and the DB shape can drift from the contract; the hybrid schema
keeps the contract authoritative. *Testing against SQLite* — its JSON and upsert semantics differ
from Postgres (no real `JSONB`, different `ON CONFLICT`), so the tests use **testcontainers** (a
real throwaway Postgres) to verify against the database that actually runs. *Auto-migrating on
service startup* — races across replicas; migrations run as their own step instead.

### ADR-015 — Durable runtime state

**Context.** ADR-014 persists the *records* the system writes (audit, playbooks, training). But
two pieces of live **runtime state** stayed in memory and were lost on restart: the **pending HITL
approvals** governance holds (a plain dict), and the correlator's **z-score baseline** — the
per-metric running mean/variance that *is* its learned notion of normal. Both losses hurt mid-run:
a governance restart during an incident drops a human's in-flight approval, and a
correlation-service restart throws away a warm detector and re-enters the cold-start warm-up
blackout (`warmup_samples` observations during which anomalies are suppressed), going blind exactly
when an operator restarted it to fix something. The question was how to make these durable without
inventing a second persistence story, and — because the two behave very differently — what to do
when persistence itself fails.

**Decision.** Persist both behind the **same `STORE_BACKEND=postgres` switch and the same
`make_stores` factory** as the Tier-1a stores, but with **two different patterns matched to the
two kinds of state**:

- **Approvals — a synchronous, keyed store**, exactly like the Tier-1a stores. `ApprovalStore`
  (`create` / `get` / `decide` / `list_pending`) has an `InMemoryApprovalStore` and a
  `PostgresApprovalStore`; the Postgres table is the hybrid schema again (promoted `id` /
  `status` columns, JSONB `payload` as source of truth), and `decide` is an upsert on `id`
  (`ON CONFLICT DO UPDATE`) so a decision flips status in place rather than duplicating a row.
- **Baseline — a periodic snapshot + reload**, not a per-write store. `BaselineStore`
  (`save(rows)` / `load_all()`) persists one row per metric in `correlation_baseline`. The
  correlation-service's existing background **flusher** thread piggybacks the snapshot on its own
  `time.monotonic()` schedule every `baseline_snapshot_seconds` (default 30). On boot,
  `_reload_baseline` restores the baseline **before the consumer thread starts**, so the first
  events are scored against the recovered state — no cold-start blackout.

**Why the split posture — the deliberate part.** The two holders fail *differently on purpose*,
because one loss is a correctness failure and the other is recoverable:

- **Approvals propagate errors**, exactly like the audit sink (ADR-014). A dropped approval write
  silently loses a human's decision or a pending request — a correctness failure that must be
  *visible*, never a swallowed no-op.
- **The baseline snapshot/reload is best-effort — logged and fail-safe.** A baseline is a
  slowly-settling statistic; a missed 30-second snapshot, or a failed reload, only makes the
  detector slightly staler or starts it cold — both recoverable. So `_snapshot_baseline_once` and
  `_reload_baseline` catch every exception, log a warning, and continue; a persistence hiccup can
  **never** crash the flusher thread or the service boot. This is the **fail-safe posture of the
  Kubernetes remediator** (ADR-007 / ADR-013), which catches every API error and degrades rather
  than escaping — the opposite of the audit sink and the approval store. The system now runs *both*
  postures side by side, chosen per-holder by what a loss actually costs.

**The river codec — verified, not assumed.** Reloading the baseline means reconstructing river's
online statistics from stored scalars. The snapshot stores per metric `(n, mean, variance, count)`
and reload rebuilds via `stats.Mean._from_state(n, mean)` and
`stats.Var._from_state(n, mean, variance, ddof=1)`. Critically, `Var._from_state` takes the
**variance** as its `sig` argument — **not** river's internal running sum-of-squares `_S`. Storing
`_S` reconstructs a diverging detector; this was verified during design and is pinned by a codec
test (`tests/test_baseline_codec.py`) so a river upgrade can't silently break it.

**Consequences.** (+) A governance or correlation restart mid-incident resumes: approvals survive,
and the detector reloads warm so a genuine outlier fires immediately (pinned by a restart-survival
test that settles a baseline, persists it to a real Postgres, reloads into a fresh engine, and
asserts the outlier fires with no warm-up blackout). (+) No new persistence machinery — same
switch, same factory, same hybrid schema and testcontainers contract test, now covering
`ApprovalStore` too. (−) Two error postures to keep straight, and one more pair of tables to
migrate (Alembic `0002_runtime_state`). Accepted — the postures are documented and each is matched
to what a loss costs.

**Scope — what is deliberately *not* persisted.** The **read-model stays on event replay**
(ADR-009): the dashboard projection is rebuilt from the situation/outcome stream, so persisting it
would duplicate state the event log already owns. And **reliability is recovered, not stored**: the
closed loop's per-signature reliability is re-derived on boot by replaying the durable **training
records** through `retrain(...)` — the labeled outcomes are the source of truth, so a separate
reliability table would be a redundant, drift-prone copy.

**Alternatives rejected.** *One uniform error posture for both holders* — either would be wrong for
one of them: propagating on a baseline snapshot would let a transient DB blip crash the detector's
flusher, and swallowing an approval write would hide a lost human decision. *Persisting the baseline
on every event* — needless write amplification for a statistic that changes slowly; a periodic
snapshot captures it at a fraction of the cost. *A dedicated reliability table* — duplicates the
training records that already exist and can drift from them; recomputing on boot keeps one source of
truth. *Storing river's raw `_S`* — reconstructs a diverging detector (see the codec note).

### ADR-016 — Observability & readiness

**Context.** The stack ran end-to-end, but two operational surfaces were thin. Logs were the
default root-logger text — fine to read locally, but nothing a log aggregator could parse across
services, and with no consistent `service` tag. And the only health signal was `/health`, which
returns `200` as long as the process is up: it says nothing about whether the service can actually
reach Redis or Postgres, so an orchestrator had no honest way to know a container was *degraded but
alive* versus *ready to serve*. Both gaps are cross-cutting — every one of the seven services needs
the same behavior — so the question was where to put it so it stays uniform.

**Decision.** Add two capabilities, both wired **once in the shared `create_app` factory**
(`services/base.py`) so every service gets them identically:

- **Structured logging.** `configure_logging(service_name, settings)` installs a single root
  handler behind `INTELLIOPS_LOG_FORMAT=text|json` (default `text`). `text` keeps the readable
  formatter for local dev and pytest; `json` emits one object per line via a small stdlib
  `JsonFormatter` (`ts / level / logger / service / msg / module / line`, plus `exc_info` on an
  exception and any caller `extra={...}` fields). A filter stamps the `service` name onto every
  record; the installer is idempotent so repeated `create_app()` calls never stack handlers. The
  compose stack sets `INTELLIOPS_LOG_FORMAT: json` on each of the seven app services.
- **An active `/ready` readiness probe, split from `/health` liveness.** `/health` stays the
  liveness signal — always `200` while the process can answer, checks nothing external, so a
  dependency outage never triggers a restart loop. `/ready` **actively pings** dependencies on
  each call: `bus.ping()` always, plus a `db_ready(engine)` `SELECT 1` for services that pass a
  `readiness` callable and hold a real engine. It returns `200 {"ready": true}`, or
  `503 {"ready": false, "failed": [...]}` naming the down dependency (`redis` / `postgres`). The
  handler never raises — a failed check becomes a `failed`-list entry, not a `500`. File-mode or
  no-DB services (`ingestion`, `read`) get a `None` engine and are bus-only, never claiming a
  Postgres dependency they don't have. Both probes short-circuit the auth gate, so compose/k8s
  probes need no token in any `AUTH_MODE`.

**Why.** One seam keeps the behavior honest: because both are wired in `create_app`, a new service
gets structured logs and a real readiness probe for free, with no per-service opt-in to forget.
Logging is **zero-dependency** — stdlib `logging` plus a ~20-line `JsonFormatter`, not a new
logging framework — so it adds no supply-chain surface and can't diverge from Python's own logging.
Readiness is **active, not assumed**: pinging the bus and running `SELECT 1` reports a dependency
that is actually reachable *now*, rather than trusting a cached connection. Splitting liveness from
readiness matters because they drive different orchestrator actions — liveness *restarts* a wedged
process, readiness *removes a pod from rotation* while a dependency is down without killing it — and
conflating them would make a transient Redis blip cause pod restarts instead of a brief
out-of-rotation. The compose healthcheck uses a Python one-liner against `/ready` (the shared image
has Python but no `curl`); a real cluster maps `livenessProbe: /health` + `readinessProbe: /ready`.

**Consequences.** (+) Uniform, aggregator-ready logs with a `service` tag across all seven
services, behind a switch that keeps local/test output readable. (+) An orchestrator can tell
*alive* from *ready* and see which dependency is down from the `503` body. (+) It closed a small
inconsistency as a side effect: unlike the other store-using services, correlation never kept its
DB engine on `app.state` at all (it stored only the detector engine, under a different attribute).
Wiring the DB readiness closure added `app.state.db_engine = stores.engine` inside correlation's
guarded store init (where `stores` is bound), so a DB-down cold-start leaves it unset and the
`/ready` probe degrades to a bus-only check rather than reporting a false Postgres failure. (−) Two error postures in the probe
(never-raise for `/ready`, versus the propagate-on-write posture of the stores) and one more env
switch to document. Accepted — the readiness handler's job is to *report* a failure, not re-raise
it, which is the opposite need from a store write that must never hide a lost record.

**Alternatives rejected.** *A logging framework* (structlog / loguru) — more dependency and
configuration surface than a stdlib formatter needs for JSON-lines. *Only `/health`* — cannot
distinguish a wedged process from a healthy one whose database is down, so an orchestrator either
restarts on dependency blips or serves traffic it can't fulfill. *A passive readiness flag* set at
startup — goes stale the moment a dependency drops mid-run; an active per-request ping reports the
live state. *`curl`-based healthchecks* — the slim shared image ships no `curl`; a Python one-liner
uses what's already there.

### ADR-017 — Edge authentication

**Context.** Every service ran wide open: the whole HTTP surface — read/console feeds, the
governance approval endpoints, and the simulation controls — answered any caller with no
credential. That was fine for local dev and the demo, but it left the system with no auth story at
all, which is not a credible production posture. RBAC already gates *who can approve what* inside
governance ([ADR-003](#adr-003--governance-is-an-active-gate-not-passive-logging)), but that is
authorization *after* a request is in; it does nothing to stop an unauthenticated request from
reaching the surface in the first place. The need was a network-access gate in front of the
services — and it had to be able to stay **off** so dev, the test suite, and CI keep running with
no token.

**Decision.** Add a **config-switched bearer gate at the edge**: `AUTH_MODE=off|token` (default
`off`). In `off` mode every endpoint is open — current dev/test/CI behavior, unchanged. In `token`
mode a request must carry `Authorization: Bearer <INTELLIOPS_AUTH_TOKEN>` or the service returns
`401`. The check is a **timing-safe** comparison (`hmac.compare_digest`) in `common/auth.py`,
wired **once** as HTTP middleware in the shared `create_app` factory (`services/base.py`), so it
covers every route on all seven app services without per-service opt-in. `/health` and `/ready`
are **always exempt** — the middleware short-circuits them before the gate, in every mode — so
compose and k8s liveness/readiness probes never need a token. Internal service-to-service calls
**authenticate rather than being bypassed**: when `AUTH_MODE=token`, callers like action's
`HttpGovernanceGate` attach the same `Bearer` token to their governance requests, so there is no
trusted-network hole to exempt. The React operator console authenticates with that **same shared
token** (`VITE_AUTH_TOKEN`, sent on both its read fetches and its approve/reject write) — so the
read endpoints stay gated and `token` mode leaves **no public read surface**.

**Why.** One seam, off by default, keeps every existing workflow intact while giving a real gate
when it's switched on: because it lives in `create_app`, a new service is protected for free with
nothing to forget, exactly like the observability wiring
([ADR-016](#adr-016--observability--readiness)). A timing-safe compare avoids leaking the token
through response-time differences on a byte-by-byte mismatch. Making probes exempt at the
middleware — not via the per-service exempt predicate — means even a service that passes a custom
exemption list can never accidentally gate its own healthchecks. And having internal callers send
the token, rather than carving out an internal-network exemption, keeps the gate honest end to
end: there is no path that is trusted merely because of where it originates.

**Consequences.** (+) `AUTH_MODE=token` genuinely protects the whole surface — verified: the read
endpoints return `401` without a token and `200` with the right one, and internal governance calls
keep working because the callers authenticate. (+) The default build is byte-for-byte the old
behavior, so tests and CI need no token and no code branch changes. (−) It's a **shared token**,
not per-user — every authorized caller presents the same secret, so it authenticates the *stack*,
not an individual, and can't distinguish or revoke one operator. (−) Because Vite inlines `VITE_*`
at build time, the console token is **baked into the client bundle** — anyone who can load the
bundle has it. Both costs are accepted as the honest shape of a shared-token demo gate, not the
production identity model.

**Alternatives rejected.** *Exempt the read endpoints* (gate only writes and controls) — leaves
the audit log and the situations/outcomes feed publicly readable, which is exactly the surface
that most needs protecting; rejected. *A separate read-only token* — a second secret to
distribute and rotate, over-engineered for a single shared-token demo; rejected. *A full IdP /
JWT with per-user identity and revocation* — the real production path, and the right end state,
but far more than a demo needs now; deferred and noted, not built. *Trust the internal network and
exempt service-to-service calls* — reintroduces an unauthenticated path and couples security to
network topology; rejected in favor of internal callers carrying the token.

### ADR-018 — Real-time console read path (SSE)

**Context.** The React console polled every backend endpoint on a flat 5-second `setInterval`,
in every view, since the first version of the read-model work. That is livable for a dashboard but
undercuts the demo's central moment — an operator watching an incident move through detection,
diagnosis, gate, and remediation — where a 5-second lag between "the backend resolved it" and "the
screen shows it resolved" reads as sluggish rather than live. The read-service's projection
(`ReadModel`, `services/read/projection.py`) already sees every mutation the moment a consumer
thread applies it; the gap was purely in how that moment reached the browser. Two more things had
to be true of whatever filled that gap: it had to run through the existing auth/CORS/middleware
path unchanged (no new gate to keep honest), and it had to degrade to the existing poll rather than
silently going dark if it broke.

**Decision.** Add one new **Server-Sent Events** endpoint, `GET /stream`, fed by a **stdlib
thread→async pub/sub** inside `ReadModel`, authenticated under `AUTH_MODE=token` by a **query-param
token** scoped to that one route. Three parts:

- **SSE, not WebSocket.** The data need is one-way, server→client only — the console never sends
  anything over this channel; approve/reject stays the existing `POST /approvals/{id}/decide`.
  `EventSource` (the browser API for SSE) has auto-reconnect built in, and — verified against the
  installed stack, FastAPI 0.133.1 / Starlette 1.0.1 — `BaseHTTPMiddleware` (the `_auth_gate` in
  `services/base.py`) streams responses through an anyio task group in this version rather than
  buffering the body, so a `StreamingResponse(media_type="text/event-stream")` passes through the
  auth middleware unbroken with no special-casing. A WebSocket would add a connection-lifecycle
  state machine and a separate auth-upgrade path for a capability (client→server realtime) nothing
  here needs.
- **Thread→async fan-out, stdlib only.** `ReadModel`'s `apply_detected/apply_diagnosed/apply_outcome/apply_suppressed`
  run on daemon consumer threads (`services/read/consumer.py`, one per Redis Streams topic), while
  `/stream`'s generators drain on the uvicorn event loop. `asyncio.Queue` cannot be written to
  safely from another thread, so `ReadModel` gains a subscriber registry (`set[asyncio.Queue]`
  behind a `threading.Lock`) and a captured loop reference (`bind_loop`, called once from the async
  lifespan); consumer threads call `publish()`, which hands delivery to each subscriber via
  `loop.call_soon_threadsafe(...)` — the one asyncio primitive built for exactly this cross-thread
  handoff. No `janus`, no `run_in_executor`, no new dependency: `asyncio` + `threading` from the
  standard library. The event on the wire is a single generic nudge, `{"type": "changed"}`, not a
  typed diff — on receipt the client just re-runs its existing `/situations` / `/outcomes` /
  `/metrics` fetch, so the client contract stays trivial and the projection needs no per-event
  serialization.
- **Query-param token, scoped to `/stream` only.** `EventSource`'s constructor takes a URL and a
  `withCredentials` flag — it cannot set an `Authorization` header, so the standard Bearer-header
  gate ([ADR-017](#adr-017--edge-authentication)) is structurally unreachable here. `/stream`
  instead accepts `?token=`, checked in-route with the same `hmac.compare_digest` timing-safe
  comparison (and the same `bool(auth_token)` no-accidental-open guard) as the header path. A
  custom `auth_exempt` predicate passed into `create_app` exempts exactly `GET /stream` from the
  header gate — every other route on every service keeps the unmodified gate.

**Why.** Each piece answers a constraint that would otherwise have forced a heavier design: SSE
because the data need really is one-way and the existing middleware already streams correctly, so
adding WebSocket machinery would buy nothing; the thread→async handoff because the projection's
mutation point is fundamentally on a different thread than the delivery point, and
`call_soon_threadsafe` is the correct, dependency-free bridge for that; the generic nudge because a
typed per-event payload would require the projection to serialize on every mutation and the client
to track incremental state, for a UI that already re-fetches full snapshots cheaply; and the
query-param token because it is the only way to authenticate a browser `EventSource` at all without
changing what "the read endpoints are gated" means.

**Backpressure: lossy-but-live, not blocking.** Each subscriber's queue is bounded (maxsize 1000).
A client that falls behind has its oldest queued event dropped to make room, rather than blocking
the publishing consumer thread or buffering without limit. This is deliberate: the read-model's
own docstring already states it is a rebuildable projection of the Redis event stream, not a
durable log, so a client that misses a nudge loses nothing it cannot get by reconnecting and
re-`GET`ting the current snapshot. Blocking the consumer thread on a slow SSE client would let one
stalled browser tab stall the entire projection; dropping a stale nudge cannot.

**Honest limit — the query-param token is scoped to the shared-demo-token model.** The token
landing in a query string means it can land in access logs and in a `Referer` header if one is ever
forwarded — a real cost, not a hidden one. It is accepted here specifically because
`docs/OPERATIONS.md` already documents `VITE_AUTH_TOKEN` as **baked into the client bundle at
build time**: anyone who can load the console already has the token, so a copy in a log leaks
nothing new. **This reasoning is scoped to that shared-token model. If per-user tokens or a real
IdP are ever introduced, `/stream` auth must be revisited** — the query-param approach does not
extend to a world where the token identifies an individual. A cookie-based alternative was
considered and rejected: the console is cross-origin (the `:5173` Vite bundle calling the `:8007`
read-service), so a cookie would require credentialed CORS (`allow_credentials=True`, a
non-wildcard origin, `SameSite=None; Secure`) — materially more surface than a public demo token
justifies.

**Also in this effort: the console repaint to Apple's light palette.** Alongside the transport
work, the entire console was repainted from its original dark theme to Apple's actual light
website palette — white/`#F5F5F7` backgrounds, `#1D1D1F` ink, `#0071E3` system-blue accent, and
Apple's system status colors — centralized in `tailwind.config.js`'s token groups plus a
mechanical re-tune of roughly 55 utility classes that had assumed a dark background and would not
have picked up a token swap alone (white-on-white hairlines, dark-tuned glows, light-on-light
button text). This is a visual decision without the transport's structural trade-offs, so it is
noted here rather than given its own ADR; see [docs/UI.md](docs/UI.md) for the full before/after
description.

**Consequences.** (+) The console's three pre-existing views (Overview, Incidents, Governance) and
the two new ones added alongside this work (Pipeline, Audit) all update within about a second of a
backend mutation in live mode, with no per-view special-casing — they all go through one
`useLiveData` hook. (+) Zero new runtime dependencies on either side. (+) `AUTH_MODE=off` and mock
mode are byte-unchanged — the stream is opt-in live-mode behavior, and the existing synchronous
`ReadModel` unit tests (which call `apply_*` with no loop bound) keep passing unmodified because
`publish()` is a no-op until `bind_loop` has run. (−) One endpoint now authenticates differently
from every other route in the system — a reader auditing "how does auth work here" has to know
`/stream` is the one exception, and why. (−) The query-param token is a real, documented crack in
an otherwise uniform header-based gate; it is scoped and justified above, not hidden. (−) Backpressure
is lossy by design: a sufficiently slow or unlucky client can miss a nudge, though never the
underlying state (a reconnect or the next nudge always re-syncs it).

**Alternatives rejected.** *WebSocket* — bidirectional machinery for a channel that only ever
needs to carry server→client nudges; rejected as unneeded complexity. *A cookie-based `/stream`
auth* — forces credentialed cross-origin CORS for a single endpoint, more surface than a shared
demo token warrants; rejected. *Typed per-event payloads* (e.g. `{"type": "situation.updated",
"situation": {...}}`) — would need the projection to serialize a payload on every mutation and the
client to merge incremental state, in exchange for saving a snapshot re-fetch the client already
does cheaply; rejected in favor of the generic nudge. *Unbounded or blocking queues* — trades a
dropped, recoverable UI hint for either unbounded memory growth or a stalled consumer thread;
rejected in favor of the bounded drop-oldest queue.

### ADR-019 — Pluggable detectors, the finetuning loop, and LLM-assisted RCA

**Context.** The system had one correlator, `RiverCorrelator`: a per-metric online
z-score against a running mean/variance, with a warm-up gate. It has two known,
well-understood weaknesses. First, no seasonal awareness — a metric that legitimately
plateaus higher at certain hours (a daily traffic pattern, a nightly batch job) looks
like a global outlier to a single running baseline, which is a direct false-positive
source. Second, a single early spike inflates the running mean/variance and can
desensitize the detector to a second, same-size spike later — the z-score's classic
failure mode. Separately, RCA's `rank_hypotheses` used three fixed-confidence rules
with no memory of which suggested runbook had actually worked before, and diagnosis
produced no human-readable explanation beyond a short rule description. The ask (from
`WORKPLAN.md`'s Stream B) was to make detection and RCA measurably better *and prove
it* — not just add a model and assert it helps.

**Decision.** Four pieces, kept strictly additive so the existing `river` default and
its tests stay byte-unchanged:

- **`CORRELATOR_KIND` switch (`common/config.py`, default `river`)** selects the
  correlator via `make_correlator(settings)`
  (`services/correlation/adapters/__init__.py`), wired where `correlation/app.py`
  used to construct `RiverCorrelator` directly. All three correlators subclass a new
  `BaseCorrelator` ABC (`services/correlation/adapters/base_correlator.py`) that
  makes explicit the contract `CorrelationEngine` already relied on implicitly:
  `_z_threshold`/`_warmup_samples` attributes, `_severity_band`, `should_suppress`,
  `reliability`, a `retrain` that aggregates per-signature reliability, and the
  `snapshot()`/`load()` pair the engine's `reset()` uses to reconstruct a correlator
  via `type(correlator)(z_threshold=..., warmup_samples=...)`. `RiverCorrelator` was
  refactored onto this base as a pure move — the hoisted methods are verbatim, proven
  behavior-identical by the full pre-existing correlation suite passing unmodified.
- **`RobustCorrelator` (`kind=robust`)** replaces mean/variance with **median +
  MAD** (median absolute deviation): score = `|value − median| / (1.4826 × MAD)`,
  the constant making MAD a consistent σ-estimator for normal data; `MAD == 0` scores
  `0` rather than dividing by zero. This alone fixes the single-spike-desensitizes
  problem — a robust median barely moves from one outlier. On top of that, it keeps a
  **seasonal baseline**: a separate numpy window per `(metric_name, hour-of-day
  bucket)` pair (`correlation_seasonal_buckets`, default 24 buckets), so a metric is
  scored against its own hour's local distribution instead of one global one. Config:
  `correlation_seasonal_buckets`, `correlation_robust_window` (window size per
  bucket), `correlation_robust_warmup` (samples required before a bucket scores
  anything, default 30).
- **`TrainedCorrelator` (`kind=trained`)** *composes* a `RobustCorrelator` for the
  online path and adds a **scikit-learn `IsolationForest`**, blending
  `max(online_score, model_score)` so the model can only add sensitivity, never
  suppress the online detector. The model score is derived from the forest's own
  decision boundary, not its raw `score_samples` output: `decision_function(x) =
  score_samples(x) − offset_` is `≥ 0` for points the model calls normal and `< 0`
  only for outliers, so the anomaly **margin** `max(0, −decision_function(x))` is
  exactly `0` for a normal point (no false-positive contribution from the model) and
  positive only for a genuine outlier, scaled onto the z-threshold range by a fixed
  constant (`_MODEL_SCALE = 120`). This is a deliberate fix over the naive approach:
  `score_samples` alone is negative for *every* point (normal points score around
  −0.45, outliers around −0.65), so a naive `-score_samples * k` flags essentially
  everything — the earlier form of this class did exactly that, caught by the
  sign-convention test, and the `decision_function`-margin blend is the fix (commit
  `668b0c7`). A cold `TrainedCorrelator` (no model fitted yet) returns the online
  score alone — byte-identical to a bare `RobustCorrelator` — so a fresh `trained`
  deployment works correctly before any fit ever runs.
- **The persisted fit/retrain loop — the finetuning story, told honestly.**
  `TrainedCorrelator.fit()` trains the `IsolationForest` on a rolling buffer of up to
  4096 featurized events (a frozen 9-column schema — value, the online z-score,
  hour-of-day as sin/cos, day-of-week, one-hot event kind, label count — see
  `FEATURE_NAMES`); `serialize()`/`load_model()` round-trip the fitted model plus its
  feature schema as a joblib blob, refusing to load a blob whose schema drifted from
  the running code's `FEATURE_NAMES` (the correlator just stays cold rather than
  scoring with a mismatched model). The blob persists to a new `model_artifacts`
  table (Postgres `bytea`, migration `0003`) through a `ModelStore`
  (`InMemoryModelStore` / `PostgresModelStore`), following `BaselineStore`'s
  best-effort posture — a failed save just means the next boot cold-starts and
  re-fits, never a crash. **The honest fact: the fit is triggered by `POST
  /retrain`, not automatically.** An earlier design assumed `retrain()` at process
  boot would fit the model; it doesn't, because the feature buffer is empty at boot
  — there is nothing to fit *from* yet. `POST /retrain` (`correlation/app.py`) is the
  real trigger: it calls `fit()` on the live correlator (which has been observing
  events since boot) and persists the result. This is what the demo/benchmark fire to
  make the learning loop visible; it is not (yet) wired to a scheduler or an
  outcome-driven trigger inside the running service — see Consequences.
- **RCA: reliability-weighted ranking + on-by-default, advisory-only LLM
  explanation.** `rank_hypotheses(situation, context, reliability_provider=None)`
  gained an optional third parameter — `None` preserves the exact prior ranking
  (existing tests pass unmodified); when provided (a `signature -> float` callable
  built in `rca/app.py` from `training_store.read_all()`, the same per-signature
  worked/total aggregation `RiverCorrelator.retrain` already did), a hypothesis whose
  `suggested_runbook_id` has a proven track record for this situation's signature
  gets a bounded confidence boost. The top suggestion still always resolves to a real
  playbook id — the downstream action path is unaffected. Separately, a new
  `ExplanationProvider` Protocol (`common/interfaces.py`) is **on by default** in the
  RCA flow: every diagnosed hypothesis carries advisory explanation text, but the
  *provider* is config-selected. `TemplateExplanationProvider` — deterministic,
  dependency-free, no network call — is the provider whenever
  `llm_explanation_endpoint` is empty (the default), so CI, tests, and dev-without-
  a-key never make a network call. `OpenAICompatibleExplanationProvider` — a sync
  `httpx.Client` POST to `{endpoint}/chat/completions` — is used only when an
  endpoint is configured, and works against OpenAI, a local Ollama, vLLM, or any
  OpenAI-chat-completions-shaped server. **Advisory-only, structurally:** the
  consumer sets the LLM text on the top hypothesis via
  `model_copy(update={"explanation": ...})` — an additive field
  (`RootCauseHypothesis.explanation: str | None = None`) — after ranking is already
  final, so the explanation can never influence confidence, ordering, or
  `suggested_runbook_id`. Every LLM failure mode (connection error, non-200,
  non-JSON body, missing `choices`, empty content) is caught inside the provider and
  falls back to the template output — a flaky or misbehaving LLM endpoint can never
  break diagnosis or the downstream action path.
- **sklearn stays off the default path.** `scikit-learn`/`joblib` are imported
  **lazily**, inside `TrainedCorrelator.fit()`/`serialize()`/`load_model()` only —
  never at module import time. A `river`- or `robust`-configured service never pays
  the import cost or carries the dependency risk; only a `trained` deployment that
  actually calls one of those methods touches sklearn.

**Why.** `BaseCorrelator` turns an implicit, easy-to-break contract (the engine
reaching into `_z_threshold`, calling `_severity_band`, reconstructing via a
positional-kwarg factory) into an explicit one every new correlator is checked
against, so `robust` and `trained` cannot silently violate an assumption only
`RiverCorrelator` happened to satisfy. Median/MAD plus seasonal buckets was chosen
over, say, a heavier seasonal-decomposition model because it is cheap, explainable
in one line (a robust statistic against a matching hour's own window), and directly
targets the two documented failure modes rather than a general-purpose smoothing
technique. Composing `RobustCorrelator` inside `TrainedCorrelator` — rather than
having the trained path reinvent an online score — means the model can only ever
*add* sensitivity on top of a detector that already works, which is also why the
blend is `max()` and not, say, an average that could let a confident-but-wrong model
score suppress a real online signal. Making the LLM explanation advisory-only and
additive-field-only is the same reversible-automation principle the rest of the
system already applies to remediation ([ADR-007](#adr-007--reversible-only-health-verified-remediation)):
a non-deterministic, third-party-dependent component is only ever allowed to narrate
a decision that was already made deterministically, never to make it. Keeping the
provider config-selected (template vs. real endpoint) rather than always calling out
is what let "LLM explanation" ship **on by default** without adding network calls,
flakiness, or cost to CI and tests.

**Consequences.** (+) `river` stays the byte-unchanged default; the entire existing
correlation and RCA test suites pass with no code changes, and `robust`/`trained` are
purely opt-in via `CORRELATOR_KIND`. (+) The seasonal false-positive reduction and
correlation-break recall gains are real and CI-enforced (one comparison) or
documented with fresh, reproducible numbers (the rest) — see
[docs/BENCHMARKS.md](docs/BENCHMARKS.md), not asserted in prose alone. (+) RCA gains
a genuine feedback signal (reliability-weighted ranking) and a richer, human-readable
explanation with no new failure mode reaching the action path. (+) sklearn's
supply-chain and import cost is fully opt-in. (−) **The retrain trigger is manual**
(`POST /retrain`), not automatic — there is no scheduler or outcome-driven hook in
this build that calls it, so a `trained` deployment left alone never improves past
its first (also manual) fit; this is explicitly listed as a deferred maturity
milestone in §6 below, not hidden. (−) `robust`'s per-bucket seasonal windows are
**in-process only** for this build — not written to a durable table — so a restart
re-warms every bucket from scratch (`correlation_baseline`, the existing durable
z-score-baseline table from [ADR-015](#adr-015--durable-runtime-state), has a
`metric_name`-only primary key and no window column, so it cannot hold 24
per-metric buckets without its own migration; that migration was deliberately
deferred rather than blocking this effort — see docs/BENCHMARKS.md's honest limits).
(−) `robust`/`trained` are measurably **more sensitive**, not strictly better: the
benchmark shows meaningfully higher false-positive rates and lower precision than
`river` on `sustained_anomaly`/`correlation_break` alongside their recall gains — a
real sensitivity/specificity trade an operator switching `CORRELATOR_KIND` should
expect, not a free upgrade. (−) The `IsolationForest`'s anomaly margin saturates on a
near-constant feature vector, so `trained` tracks `robust` closely on the univariate
scenarios (point/sustained) and only shows an independent edge on the multivariate
`correlation_break` scenario — its practical value in this build is the persisted
fit/re-fit loop and reliability learning, not a large standalone detection win.

**Alternatives rejected.** *A single smarter correlator replacing `RiverCorrelator`
outright* — would break the "test-safe by default" constraint every other adapter
switch in this system honors ([ADR-012](#adr-012--config-switched-adapter-selection-with-test-safe-defaults))
and removes the ability to measure the new detector against the old one on the same
stream; rejected in favor of a config switch with `river` still default. *Automatic
retrain on a fixed schedule or at boot* — boot has an empty feature buffer (nothing
to fit from), and a fixed-interval scheduler adds a background timer and failure
mode this effort did not have time to make safe (partial fits, overlapping retrains);
deferred to `POST /retrain` as an explicit, observable trigger the operator/demo
controls. *Averaging the model score into the blend instead of `max()`* — would let
a wrong-but-confident model score pull a genuine online anomaly's combined score
down; rejected, `max()` guarantees the model can only add sensitivity. *Making the
LLM explanation replace or gate the ranked hypothesis/runbook selection* — would
make the deterministic action path dependent on a non-deterministic, potentially
unavailable third-party call; rejected outright as inconsistent with the reversible-
automation principle the whole system is built on. *Async LLM calls* — the RCA
consumer is a synchronous daemon thread, not an async event loop; a sync `httpx.Client`
matches the actual call site instead of introducing an event loop for one provider.

### ADR-020 — Meridian sample production system

**Context.** Every incident IntelliOps had ever detected, diagnosed, and remediated was against
`demo-app` — a single-endpoint toy target whose only job is to expose one `cpu_usage` gauge and
flip it via `/break`/`/fix`. That is enough to prove the loop closes, but it is not a credible
"we connected a real system" story for a PPO panel: a real target has multiple independently
faulting services, a service topology RCA can reason about, and traffic that looks like a product,
not a probe. The instruction was to make Meridian's faults **genuinely real** — detected off real
metrics, diagnosed by the unmodified RCA rules, gated by the unmodified governance path — rather
than build a second demo harness that fakes the interesting part. Three verified facts shaped
every decision below: (1) the correlator's z-score baseline is keyed on metric *name* only, so a
service pinned "broken" from boot never spikes — each Meridian service needed the same
toggle-at-runtime pattern `demo-app` already uses; (2) `CorrelationEngine` groups anomalies by time
window, not by service, so concurrent faults on two services merge into one Situation; (3) RCA's
`rank_hypotheses` maps by metric-name token and a recent-deploy match, not by value, so getting
three *different* diagnoses out of one rule set requires engineering which signal each fault
raises, not just how large it is.

**Decision.** Build Meridian as a small, realistic **Deloitte-style financial/audit reporting
platform** — four backend services (`gateway`, `validation`, `aggregation`, `reporting`) built from
one shared factory (`services/meridian/common.py`'s `make_meridian_service()`, mirroring
`services.base.create_app`), plus a client-portal + ops-panel UI served by the gateway — and wire
IntelliOps to observe it through **additive-only** changes:

- **A distinct `service`-labeled Prometheus scrape job per Meridian backend**
  (`deploy/prometheus.yml`), alongside the untouched `demo-app` job, so RCA can attribute an
  incident to the right service.
- **The ingestion query broadened to an instant-vector selector, in compose only.**
  `INTELLIOPS_PROMETHEUS_QUERY: '{__name__=~"cpu_usage|meridian_error_rate"}'` is set on the
  `ingestion` service's compose environment; `common/config.py`'s default stays the bare
  `cpu_usage` query. No ingestion code changed — `PrometheusSource` already treats its configured
  query as an opaque PromQL string, and a regex selector is still a valid instant-vector query.
  This was the design's single riskiest unknown and was verified live against a real Prometheus
  before being relied on (see [docs/MERIDIAN.md §5](../docs/MERIDIAN.md#5-verified-live--the-real-end-to-end-run)):
  `resultType: vector`, each Meridian service its own series, its `service` label intact.
- **A shared named volume (`rca-context`) mounted on both `rca` and `meridian-gateway`.** Before
  this, `rca-service` had no mount for its on-disk deploy-context file, so `recent_deploys()` was
  always empty and `rollback-deploy` could never fire for anyone. The gateway's new
  `POST /api/ops/deploy` writes `deploys.json` into the shared volume; RCA's existing enrichment
  step reads the same file unmodified.
- **A time-window constraint honored by the UI, not fought in the detector.** Rather than teach
  `CorrelationEngine` to group by service (a real change to a component every other service also
  depends on, for a sample-system-only need), the Meridian Operations panel enforces **sequential
  fault injection**: firing a new fault is disabled while one is active, with an explicit banner
  naming the ~15-second window and a Clear action. The constraint is real and was confirmed live
  (a stale fault overlapping a new one inside the window caused a genuine situation merge during
  verification) — the UI encodes the operational discipline the detector's current design requires,
  rather than papering over it.
- **No new playbooks, no IntelliOps service code changes.** `scale-service`, `restart-pod`, and
  `rollback-deploy` are already `${service}`-templated, so they target Meridian by name with zero
  registry changes. Meridian imports only the shared platform utilities
  (`services.base.create_app`, `common.auth`, `common.config`, `common.db`) — never IntelliOps
  domain logic.

**Why.** The additive-wiring shape follows directly from the project's own design principle that
new behavior lives behind a switch defaulting to current behavior
([ADR-012](#adr-012--config-switched-adapter-selection-with-test-safe-defaults)): a fresh
`docker compose up` without Meridian, and the full `pytest` suite, must see the exact same
ingestion query and the exact same RCA rules they saw before this effort — and they do, because the
query override lives in one compose service's environment block, not in a code default. Choosing a
compose-level query override over a code change to `PrometheusSource`'s query mechanism (a
documented fallback in the original design) kept this a zero-Python-diff change to a service nobody
on this effort owned outright. Enforcing sequential injection in the UI, instead of adding
per-service grouping to `CorrelationEngine`, was the narrower fix for the actual need: Meridian
needs *demonstrable, distinct* incidents for a scripted demo, not a general-purpose multi-tenant
correlator, and changing the shared correlator's grouping semantics would have been a much larger,
riskier change for a benefit only this sample system currently needs. The `error`-fault
baseline-hold (keep `cpu_usage` at 18.0 while raising `meridian_error_rate`) is the same kind of
narrow, verified engineering: without it, `scale-service`'s 0.6 confidence would always beat
`restart-pod`'s 0.5 and the demo would never show a diverse diagnosis, so the fault mechanism itself
was built to raise exactly one signal at a time.

**Consequences.** (+) IntelliOps now has a second, structurally different target — four
independently-faultable services instead of one, a real cross-service rollback story, and a UI a
non-engineer can drive — with a genuinely diverse diagnosis proven live: `scale-service`,
`restart-pod`, and `rollback-deploy` all fired correctly across three sequential, real scenarios
(see [docs/MERIDIAN.md §5](../docs/MERIDIAN.md#5-verified-live--the-real-end-to-end-run)). (+) The
regex-selector approach required zero changes to `PrometheusSource`, `ingestion-service`, or any
other IntelliOps code path. (+) The rollback-deploy path is now real for the first time — the
`rca-context` volume gap existed before Meridian and is now fixed for any future service that wants
the same deploy-aware RCA rule. (−) **Sequential injection is a real, load-bearing constraint, not
a cosmetic UI choice** — it exists because `CorrelationEngine` groups by time window, not by
service, and that grouping was deliberately left unchanged. A future multi-tenant or
concurrent-incident demo would need to revisit that grouping, not just add another UI guard. (−)
The `crash` fault type has no dedicated RCA rule in `rank_hypotheses` today — it is detected but not
richly diagnosed (it lands in the generic low-confidence fallback unless it happens to co-occur
with a saturation-token metric), a known, documented gap rather than a hidden one. (−) Only the
gateway has real domain business logic wired in; `validation`/`aggregation`/`reporting` are fully
faultable and independently observed but their domain endpoints are scaffolded, not yet real
request handlers. (−) The demo's remediation is dry-run by default, same as the rest of the system
— Meridian does not currently have a `REMEDIATOR_MODE=k8s` path of its own.

**Alternatives rejected.** *Teaching `CorrelationEngine` to group by service instead of enforcing
sequential injection* — would let Meridian run concurrent scenarios, but changes grouping semantics
every other service and test in the system depends on, for a benefit scoped to one sample system;
rejected in favor of an honestly-documented UI constraint. *A code-level multi-query ingestion
enhancement (`_make_source` polling `cpu_usage` and `meridian_error_rate` as two separate queries)*
— named in the original design as the fallback if the regex selector broke; not needed once the
selector was verified live to return a correct instant vector, so the simpler compose-only override
shipped instead. *A second Postgres/Redis for Meridian* — unnecessary; Meridian's two tables
(`meridian_submissions`, `meridian_reports`) live on the existing `common.db.METADATA` and share the
existing bus. *Faking Meridian's metrics from canned incident scripts instead of a real toggleable
gauge* — would have been faster to build but would not exercise the real scrape → ingest → detect
path this effort exists to prove; rejected as contrary to the entire point of a "sample production
system." *A cyan/Geist-themed Meridian UI matching the console* — rejected deliberately: a visually
distinct light enterprise theme makes the demo's two systems ("the client's app" vs. "IntelliOps
watching it") legible at a glance, which matters for an audience seeing both for the first time.

### ADR-021 — Evidence exposure & honesty pass

**Context.** An adversarial audit set out to answer one question: if someone opened the console
and asked "prove this is real," could it? The engine underneath was genuinely real — 414+ tests,
a live correlator, RCA rules running against real telemetry, real (dry-run) remediation — but the
read-model *projection* (`services/read/projection.py`) that feeds the console was throwing away
every rich signal at the exact boundary where a human would look for proof. Member telemetry
events were collapsed to a bare count. A hypothesis's supporting evidence and its LLM (or
template) explanation were computed and then discarded. The correlator's peak z-score was computed
against a real baseline and then reset without ever being attached to the emitted `Situation`. The
remediation outcome was appended to a global outcomes list but never joined back onto the situation
it belonged to, so a resolved incident's own record couldn't say what actually happened to it. On
top of the missing evidence, two UI bugs made the console actively misleading rather than merely
uninformative: the approve/reject gate reappeared on a situation that had already been decided, and
the outcome panel rendered hardcoded `"healthy"` / `"aborted"` text regardless of what the backend
reported. Separately, the LLM-assisted explanation path ([ADR-019](#adr-019--pluggable-detectors-the-finetuning-loop-and-llm-assisted-rca))
was dormant and invisible by default — nothing in the compose stack or the console told an operator
whether an explanation came from a real model or the offline template, or how to turn a real one on.

**Decision.** Fix this by widening the read-model projection, not by touching engine logic —
`CorrelationEngine`, `rank_hypotheses`, and the remediation playbooks are unchanged. Every new
field is an additive, optional contract field with a test-safe default, the same discipline that
keeps contract changes cheap and keeps the existing suite green
([ADR-006](#adr-006--monorepo-with-a-shared-common-library),
[ADR-012](#adr-012--config-switched-adapter-selection-with-test-safe-defaults)). Concretely:

- The projection now carries each member event's real `name`, `value`, `labels`, and `kind`
  instead of a count, and attaches the correlator's peak z-score plus the baseline it was measured
  against to the `Situation` it belongs to.
- A diagnosed situation's ranked hypotheses keep their evidence list and their explanation text
  (labeled by source — LLM or template) all the way to the read model, instead of being summarized
  away.
- The real remediation outcome — result, executed steps, and mode (dry-run vs. live) — is joined
  onto its situation by id, alongside a stage timeline, and the read model resolves a readable
  title instead of the raw signature.
- Three new read-only introspection endpoints expose the internals directly: read's
  `GET /situations/{id}` (the full per-incident record) and `GET /system` (live correlator
  baselines, configured backends, remediation mode, LLM provider status), plus correlation's
  `GET /baseline`. Governance's `GET /audit` already existed and needed no change.
- The LLM explanation provider becomes live-configurable instead of boot-time-only: RCA gets
  `POST /config/llm` and `POST /config/llm/test`, backed by a `ProviderHolder` — a small
  lock-guarded holder the daemon consumer re-reads every iteration and the request thread writes
  to, so swapping providers takes effect without a restart. Both endpoints sit behind the existing
  edge auth ([ADR-017](#adr-017--edge-authentication)) and the response never echoes the
  `api_key` back, configured or not.
- The console's drill-down and a new System view are built on top of these endpoints; the
  reappearing-approve-gate and hardcoded-outcome-text bugs are fixed as part of the same pass,
  since they were found during the same audit and are console-side, not projection-side.

**Why.** The rule this pass enforces is simple: no fabricated numbers in live mode, and every
number or claim the console shows must trace to a real source — a metric, a stored evidence
record, a stored outcome, or a value explicitly labeled as a dry-run simulation. Widening the
projection rather than changing the engine keeps the blast radius small and testable: the engine's
own 414+ tests are untouched, and the new fields default to shapes the existing tests and mock mode
already produce, so nothing that passed before this pass can start failing because of it. This
followed the same reasoning as [ADR-012](#adr-012--config-switched-adapter-selection-with-test-safe-defaults) —
new behavior is additive and defaults to the old behavior — and the same contract discipline as
[ADR-006](#adr-006--monorepo-with-a-shared-common-library), where shared shapes live in one place
so a change to them is one edit reviewed once rather than drift across services. The LLM stays
**off by default** — an offline template explanation ships out of the box, which is the honest
default for a system with no API key configured — and turning it on is opt-in, either via the
compose environment variables or live from the console's System view, never assumed.

### ADR-022 — Slim per-service Docker images

**Context.** All 13 compose services built from the same `Dockerfile`, so all 13 shipped the same
dependency set — including `numpy`, `scikit-learn`, `river`, `joblib`, and the `kubernetes` client,
libraries only `correlation` (trained/robust anomaly detection) and `action` (the k8s remediator
adapter) actually import at runtime. Every other service — `ingestion`, `rca`, `governance`,
`feedback`, `read`, `migrate`, and the four Meridian sample-system services — paid for ~270MB of
ML and k8s dependencies it never used, pushing every image, base or not, to ~1.5GB. Worse, the
bloat wasn't confined to a `pyproject.toml` line: `common/stores.py` unconditionally imported
`adapters/__init__.py`, which eagerly imported the trained correlator module, which imported
`numpy` and `river` at module scope — so even a service that only touched `common.stores` for its
audit-log adapter pulled in the ML stack transitively, with no explicit import anywhere in that
service's own code to point at. A per-service dependency split was impossible until that leak was
closed, because the "boundary" it needed to respect didn't hold even in principle.

**Decision.** Fix the leak, then split the dependency graph and the build to match it:

- **Break the transitive leak.** `common/stores.py`'s adapter wiring no longer eagerly imports the
  trained/robust correlator at module load. The correlator's own `numpy`/`river` imports move to
  lazy, function-scope imports inside the adapters that actually construct a trained model — the
  same lazy-import pattern the codebase already used elsewhere for optional heavy deps. A
  subprocess-based `test_import_boundary.py` (Task 1) asserts `common.stores` can be imported in a
  process with no `numpy`/`river` installed, so the boundary is a running test, not a convention.
- **Split the dependency graph.** `pyproject.toml` moves `numpy`, `scikit-learn`, `river`, and
  `joblib` into an `ml` extra and the `kubernetes` client into a `k8s` extra; `pyyaml` (previously
  pulled in transitively) is pinned explicitly in the base dependency set since base no longer
  guarantees it arrives as a side effect of the ML stack. `uv.lock` is regenerated so both `uv sync
  --frozen` (base) and `uv sync --frozen --extra ml --extra k8s` (full) resolve from the same lock
  file with no version drift.
- **Multi-stage build, targeted per service.** `deploy/Dockerfile` gains a shared `builder-base`
  stage and two leaves: `base` (`uv sync --frozen --no-dev --no-install-project`, no extras) and
  `full` (the same, plus `--extra ml --extra k8s`). `deploy/docker-compose.yml`'s service anchor
  builds `target: base`; only `correlation` and `action` override to `target: full`. Every other
  service — including `migrate` and the four Meridian services, which inherit the anchor —
  gets the slim image for free.
- **Verification gate.** CI's `slim-boundary` job builds a base-only venv and imports all 11
  base-target services plus `common.stores.make_stores`, asserting none of `numpy`/`scipy`/
  `sklearn`/`river`/`joblib`/`kubernetes` land in `sys.modules`, plus two grep-lints guarding
  against a regression (no heavy-dep references in `services/feedback/`; no module-scope
  sklearn/joblib import in the trained correlator). The `compose-smoke` job builds all 13 images
  and asserts `migrate` exits 0 and every service's `/ready` (7 core services) or `/health`
  (5 Meridian/demo-app services) returns 200 — proof the split doesn't just build, it boots.

**Why.** The measured result: base-target images are **619MB**, down from **~1.5GB** (a ~59%,
~900MB drop), confirmed both by `docker images` after a full `docker compose build` and by a
runtime check (`python -c "import services.rca.app"` inside the built image, then asserting
`sklearn`/`kubernetes`/`numpy`/`river`/`joblib` are absent from `sys.modules`). `correlation` and
`action` stay at ~1.5GB on the `full` target, which is correct — they need the libraries they
carry. This is the same discipline as [ADR-012](#adr-012--config-switched-adapter-selection-with-test-safe-defaults):
optional behavior (there, a live vs. test-safe adapter; here, ML/k8s dependencies) stays behind an
explicit, test-verified boundary rather than an implicit one a future change could silently
reopen — the difference is ADR-012's boundary is a runtime config switch, while this one is a
build-time dependency graph, but both are enforced by a test that fails loudly if the boundary is
crossed rather than a convention that quietly erodes. Fixing the import leak first, rather than
just splitting `pyproject.toml` and hoping the Dockerfile stages sorted themselves out, was the
load-bearing step: a dependency split on top of an unfixed transitive leak would have left every
"slim" image still pulling in the full ML stack at import time, silently defeating the whole
effort.

---

### ADR-023 — Pre-flight sandbox rehearsal before remediation

**Context.** [ADR-007](#adr-007--reversible-only-health-verified-remediation) makes remediation
reversible and health-verified, but the first time a fix touched a real pod was still
**production**: `execute_remediation` ran the gates, then executed on the live target, then
health-checked, then rolled back if unhealthy. There was no *trial* step. `dry_run` mode
rehearses nothing — `DryRunRemediator.execute` literally logs and returns `True`. Kubernetes
server-side dry-run is admission-only (it validates the manifest against the API server; it
never schedules a pod, so it produces no health signal). Neither answers the question a human
approver actually has: *will this fix work?*

**Decision.** Add a **pre-flight rehearsal** that runs on an isolated copy **before** the human
approves (and before an `auto` playbook executes). A `Sandbox` interface has two config-switched
adapters ([ADR-012](#adr-012--config-switched-adapter-selection-with-test-safe-defaults)):
`NullSandbox` (default, `SANDBOX_MODE=off`, passes through — base demo/tests byte-identical) and
`NamespaceCloneSandbox` (`SANDBOX_MODE=k8s`). The clone sandbox copies the target Deployment
(plus, best-effort, its Service and referenced ConfigMaps) into a throwaway
`intelliops-sandbox-<id>` namespace, waits for the clone's rollout, applies the **same typed
`RemediationPlan`** to the clone via a reused `KubernetesRemediator`, watches the clone's pod
recover via the reused `KubernetesHealthChecker`, tears the namespace down, and returns a
`PreflightResult` (passed / detail / mode / sandbox_namespace). The gate is inserted **before**
the HITL approval wait: a failed rehearsal **blocks** an `auto` playbook (returns a
`preflight-failed` outcome, never executes); for `hitl` it **advises** — the verdict rides onto
the `ApprovalRequest` so the human decides with it in hand. The verdict is additive on every
`RemediationOutcome` and surfaces in the incident timeline.

**Why.** The rehearsal converts "trust the fix" into "try the fix safely, then trust it." It is
**fail-safe by construction** — the sandbox never raises out of `execute_remediation`; any error
is a `PreflightResult(passed=False)`, and the throwaway namespace is always torn down in a
`finally` — mirroring the never-raise discipline of the k8s remediator/health adapters. The
honest limit, documented rather than hidden: the clone shares the same kind node (it is *isolated*,
not *production-grade isolated*), and pod-readiness is the primary pass signal (the demo's
`cpu_usage` series is per-metric-name, not per-namespace, so a clone's metric series isn't
reliably distinguishable — a per-namespace metric query is deferred). The live path runs only on
a real kind cluster and is a documented manual step; everything else (the gate logic, the
`NullSandbox` path, the contract/projection/UI plumbing, fail-safety, teardown) is unit-tested.
Critically, the sandbox catches an action's **effect, not its blast radius** — a clean `delete`
would pass a rehearsal and then destroy production — which is exactly why the vocabulary widening
in [ADR-024](#adr-024--tier-2-remediation-vocabulary--a-destructive-action-denylist) needs a
denylist too, not just the sandbox.

---

### ADR-024 — Tier-2 remediation vocabulary + a destructive-action denylist

**Context.** [ADR-013](#adr-013--structured-remediationplan-is-what-made-real-k8s-remediation-safe)
made `RemediationStep.action` a **closed `Literal`** — the core safety property: an AI or a
misconfigured playbook literally cannot express an action outside the set, because
`model_validate` rejects it. But the set was only four verbs (`restart` / `scale` /
`rollback_deploy` / `wait`), thin for a credible remediation catalog. Widening it naïvely would
either reintroduce unsafe actions or break the "every action is typed and rehearsable" guarantee
[ADR-023](#adr-023--pre-flight-sandbox-rehearsal-before-remediation) just established.

**Decision.** Widen the `Literal` to **seven** Deployment-scoped, sandbox-rehearsable verbs (add
`patch_resource_limits`, `rollback_to_revision`, `patch_probe`) — each one typed
(`AppsV1Api`-only), each with a same-shape rollback so [ADR-007](#adr-007--reversible-only-health-verified-remediation)
still holds, each fully rehearsable by the ADR-023 clone (the sandbox was extended to seed the
clone's ReplicaSet history so `rollback_to_revision` can rehearse truthfully). **Node** actions
(`cordon`/`uncordon`) and **HPA** actions are deliberately **excluded** — a node is cluster-scoped
and can't be cloned into a sandbox (the widest blast radius in tier-2, and the sandbox guarantee
wouldn't hold); an HPA needs a different API and only partially rehearses. Catastrophic actions
(`delete`, `exec`, scale-to-zero, secret access, any cluster-scoped mutation) stay **permanently**
out of the `Literal`. Alongside the widening, add a **defense-in-depth denylist gate** in
`execute_remediation` that runs **before the sandbox** and refuses dangerous *shapes* of allowed
verbs: `denied:unsafe-scale` (a delta that would zero a deployment), `denied:unsafe-limits`
(implausibly small ceilings, or a no-op patch), `denied:unsafe-probe` (a defeated/malformed
probe), `denied:unsafe-revision` (an indeterminate rollback target).

**Why.** The `Literal` is the primary guard and the denylist is belt-and-suspenders — for tier-2
verbs a name-blocklist would be redundant (the `Literal` already excludes bad *names*), so the
gate's real value is guarding dangerous *shapes*, and it is positioned where it will also guard
the AI-authored runbooks of [ADR-025](#adr-025--ai-authored-runbooks-propose--approve) and any
future open field. It runs before the sandbox on purpose: the sandbox catches *effect*, not
*blast radius* (ADR-023), so a dangerous-but-valid-looking step must be refused by a hard gate
*before* a rehearsal could lull an operator into approving it. The safety invariant survives the
widening intact: still a closed `Literal` (grown by exactly three vetted verbs), still one typed
API call + a same-shape rollback per action, still every action sandbox-rehearsable, and the
default path unchanged.

---

### ADR-025 — AI-authored runbooks (propose → approve)

**Context.** Every playbook in the registry was human-authored and seeded. When RCA diagnosed a
situation with no matching playbook, the incident simply had no suggested remediation — a **gap**.
Closing that gap with an LLM is tempting, but letting a model *choose or execute* an action would
throw away the entire safety story ([ADR-007](#adr-007--reversible-only-health-verified-remediation),
[ADR-013](#adr-013--structured-remediationplan-is-what-made-real-k8s-remediation-safe)) — a
model must never be the thing that acts.

**Decision.** Add a **propose → approve lifecycle**, entirely human-initiated: a human on a gap
incident requests an AI draft; governance calls a `RunbookAuthor` (a `NullRunbookAuthor` default
plus an `OpenAICompatibleRunbookAuthor`, mirroring the LLM-explanation adapter pattern of
[ADR-019](#adr-019--pluggable-detectors-the-finetuning-loop-and-llm-assisted-rca)) that returns a
**typed `Playbook`** parsed via `model_validate`; the draft is stored as a `ProposedPlaybook`
(status `proposed`) — **not** the live registry; a human with `approve` RBAC reviews it and
approves (→ the inner `Playbook` is `register()`-ed into the live `PlaybookStore`) or rejects,
both RBAC-gated and audited exactly like the existing decide/graduate flows. On propose,
`hitl_mode` is **forced to HITL** and the `id` is **server-assigned** — an AI can neither grant
itself auto-execution nor overwrite an existing playbook.

**Why.** *The AI proposes, a human disposes, and the type system guards.* The load-bearing
guarantee is inherited for free from [ADR-024](#adr-024--tier-2-remediation-vocabulary--a-destructive-action-denylist)'s
closed `Literal`: the AI's output is text, and the *only* path from that text to the store or the
registry runs through `Playbook.model_validate`, which rejects any out-of-set action — so an
unsafe draft can never even become a proposal. The author is **fail-to-nothing** (any LLM failure
— unreachable, non-JSON, invalid Playbook — returns `None`, never raises), off by default
(`RUNBOOK_AUTHOR_MODE=off` → base suite/CI never hit an endpoint), and the **only** route that
reaches the live registry is the RBAC-gated approve route. Crucially there is **no execution-path
change** — an approved AI-authored playbook enters the same registry and is thereafter subject to
every existing gate: the denylist, the ADR-023 sandbox, and the HITL approval it is forced into.

---

### ADR-026 — Semantic runbook selection (embedding fallback)

**Context.** Runbook *selection* was pure keyword matching in `rca/rank.py`: `if "cpu" in
metric_name` → `scale-service`, and so on. That is brittle — a metric named
`container_memory_working_set_bytes`, or a hypothesis worded "the service is thrashing under
load," shares no literal token with the saturation rule, so a perfectly good runbook is missed
and the incident falls into the gap. Real AIOps needs *semantic* matching — but, per the same
principle as [ADR-025](#adr-025--ai-authored-runbooks-propose--approve), an LLM must not be the
thing that picks the action.

**Decision.** Make selection **rules-first, semantic-fallback**. The keyword rules stay primary
(fast, high-precision, fully auditable); when no rule fires, an `EmbeddingRunbookSelector` (a
local `sentence-transformers` `all-MiniLM-L6-v2` model, cosine similarity) ranks the
**registered** playbooks by embedding their new curated `symptoms` field against the incident's
symptoms + hypothesis, and picks the best match above a threshold (default 0.45); below → the gap
→ the ADR-025 authoring flow. A `RunbookSelector` interface with a `NullRunbookSelector` default
(`RUNBOOK_SELECTOR_MODE=off`) keeps selection byte-identical to the keyword-only behavior; the
embedding model is opt-in via the `ml` extra, imported **lazily** so the slim-image boundary of
[ADR-022](#adr-022--slim-per-service-docker-images) holds.

**Why.** This is **retrieval — semantic matching among human-vetted playbooks — not an LLM
choosing the fix.** It can only ever return the id of a *registered* playbook (it ranks
`store.list()`) or `None`; it never fabricates a runbook or an action, and a plain threshold
comparison (not a model) makes the binding decision — so it is deterministic given the vectors
and adds genuine semantic reach with no hallucination risk. It is **fail-safe** (any model/encode
error → `None`, never raises out of `diagnose`), the rules stay primary (the selector isn't
consulted when a rule fires), and a semantic match records its provenance
(`semantic match: <id> (<score>)`) on the hypothesis evidence so the operator sees *why* — the
same honesty as the LLM/template explanation provenance. Together with ADR-025, this closes the
gap from both sides: match an existing runbook when one fits semantically, or draft a new one for
human approval when none does.

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

**Data at rest.** Audit records, labeled training outcomes, and the playbook registry persist
to **Postgres** behind the `STORE_BACKEND=postgres` switch (the compose default) — a hybrid
schema with a JSONB payload as the source of truth, migrated by Alembic
([ADR-014](#adr-014--postgres-persistence-with-a-hybrid-schema)). The same switch makes two
pieces of live runtime state durable — pending HITL approvals and the correlator's z-score
baseline ([ADR-015](#adr-015--durable-runtime-state)) — so a restart mid-incident resumes rather
than forgetting. `file` stays the default for tests and quick dev. Postgres is deployable
in-region/on-prem for sovereign-cloud requirements.

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
- **Real remediation.** `KubernetesRemediator` + `KubernetesHealthChecker` are **built**
  ([ADR-007](#adr-007--reversible-only-health-verified-remediation),
  [ADR-013](#adr-013--structured-remediationplan-is-what-made-real-k8s-remediation-safe)): typed
  `AppsV1Api` calls (restart/scale/rollback via annotation patch and
  `patch_namespaced_deployment_scale` — no shell, no string parsing, never deletes), health
  verified from pod readiness plus a live Prometheus query. It runs behind
  `REMEDIATOR_MODE=k8s` / `HEALTH_CHECK_MODE=k8s` against a local kind cluster, following
  [deploy/k8s/README.md](deploy/k8s/README.md) — a documented runbook, not part of CI. Dry-run
  (`DryRunRemediator` + `AlwaysHealthyChecker`) is still the default everywhere else (compose
  without the k8s overlay, tests, CI), so nothing changes for the base build.
- **Postgres persistence.** The audit log, playbook registry, and training store now have
  real Postgres adapters behind their interfaces ([ADR-014](#adr-014--postgres-persistence-with-a-hybrid-schema)):
  a hybrid schema (indexed columns + a JSONB payload that is the source of truth), Alembic
  migrations applied as a dedicated step, and a `STORE_BACKEND=file|postgres` switch. Postgres
  is the compose default; `file` stays the default for tests and quick dev. See
  [docs/PERSISTENCE.md](docs/PERSISTENCE.md).
- **Durable runtime state.** Two pieces of live in-memory state are now durable behind the same
  switch ([ADR-015](#adr-015--durable-runtime-state)): **pending HITL approvals** (a keyed store
  whose errors propagate like the audit log) and the correlator's **z-score baseline** (periodic
  best-effort snapshot on the flusher's `time.monotonic()` schedule, reloaded on boot before the
  consumer starts — so a restart mid-incident keeps approvals and reloads a warm detector instead
  of re-entering the cold-start blackout). The read-model stays on event replay and reliability is
  recovered from training records — neither is a new stored table.
- **Observability & readiness.** All seven services now emit **structured logs**
  (`INTELLIOPS_LOG_FORMAT=text|json`, default `text`; compose sets `json`) and expose a real
  **`/ready`** probe that actively pings the bus (and Postgres for the DB-backed services),
  distinct from the always-`200` `/health` liveness probe — both wired once in `create_app`
  ([ADR-016](#adr-016--observability--readiness)). Compose runs `/ready` as a per-service
  healthcheck; a real cluster maps `livenessProbe: /health` + `readinessProbe: /ready`. See
  [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md).
- **Edge authentication.** A config-switched bearer gate now fronts the whole HTTP surface
  ([ADR-017](#adr-017--edge-authentication)): `AUTH_MODE=off|token` (default `off`), a timing-safe
  check (`hmac.compare_digest`) wired once in `create_app`, with `/health` + `/ready` always
  exempt. Internal service-to-service calls authenticate (they send the token), and the React
  console authenticates with the same shared token — so under `token` mode the read endpoints are
  gated and there's no public read surface. The honest limits: a **shared** token (not per-user)
  and the frontend token is baked into the client bundle; per-user tokens / an IdP are the deferred
  production path. See [docs/OPERATIONS.md](docs/OPERATIONS.md).
- **Real-time console + live pipeline view.** The console no longer polls alone: the read-service
  exposes a Server-Sent Events endpoint (`GET /stream`), fed by a stdlib thread→async pub/sub
  inside `ReadModel`, that nudges the console to re-fetch within about a second of a backend change
  ([ADR-018](#adr-018--real-time-console-read-path-sse)), with a poll fallback if the stream can't
  be used. A new **Pipeline** tab animates every open incident through five stage lanes
  (Detected → Diagnosed → Gate · HITL → Acting → Resolved) as its status changes, and a new
  **Audit** tab adds a filterable explorer over the audit trail. Overview's fleet health and
  sparklines are now derived from real data in live mode instead of the old mock fallback. The
  whole console was also repainted to Apple's light website palette. See
  [docs/UI.md](docs/UI.md).
- **Pluggable detectors, the finetuning loop, and LLM-assisted RCA.** A
  `CORRELATOR_KIND=river|robust|trained` switch ([ADR-019](#adr-019--pluggable-detectors-the-finetuning-loop-and-llm-assisted-rca))
  adds two new correlators behind `RiverCorrelator`'s unchanged default: `robust`
  (median/MAD + per-hour seasonal baseline) and `trained` (a persisted scikit-learn
  `IsolationForest` blended on top of `robust`'s online score, fitted via `POST
  /retrain`). RCA's `rank_hypotheses` gained an optional reliability-weighted boost
  from learned runbook track records, and every diagnosed hypothesis now carries an
  advisory, on-by-default explanation (`TemplateExplanationProvider` — no network —
  unless `llm_explanation_endpoint` is configured, in which case an OpenAI-compatible
  endpoint is called with a template fallback on any error). A reproducible benchmark
  (`docs/BENCHMARKS.md`) measures the actual gains — and the actual trade-offs —
  against the `river` baseline, with one comparison CI-enforced.
- **Meridian — a sample production system.** A four-service, Deloitte-style financial/audit
  platform (`services/meridian/`) plus its own client-portal + ops-panel UI now runs alongside
  IntelliOps in `docker compose up` ([ADR-020](#adr-020--meridian-sample-production-system)),
  wired to the pipeline through additive-only changes: per-service Prometheus scrape jobs, an
  ingestion query broadened to a regex selector in the compose environment only (the
  `common/config.py` default is unchanged), and a shared `rca-context` volume that makes the
  `rollback-deploy` playbook fire for the first time. Three real fault scenarios were verified
  live against real Docker, each producing a genuinely different diagnosis — `scale-service`,
  `restart-pod`, `rollback-deploy` — see [docs/MERIDIAN.md](docs/MERIDIAN.md). Faults must be
  injected **sequentially** (the correlator groups by time window, not by service — a real
  constraint the Meridian UI enforces, confirmed live during verification), and the `crash` fault
  type is detection-only today (no dedicated RCA rule).
- **Automated model retraining.** The loop's *plumbing* exists; the retrain *trigger* is
  manual (`POST /retrain` on correlation-service — see [ADR-019](#adr-019--pluggable-detectors-the-finetuning-loop-and-llm-assisted-rca)),
  not scheduled or outcome-driven yet; automating it is a later maturity milestone.
- **Kafka in production.** Redis Streams runs dev and demo; the Kafka `BusClient` binding is
  deferred behind the same interface.
- **Simulation controls in production.** The `/break`, `/fix`, `/reset`, `/reset-baseline`, and
  `/reset-approvals` endpoints ([ADR-011](#adr-011--a-live-breakable-demo-harness-with-explicit-simulation-controls))
  must be gated or removed when pointed at a real system. (Under `AUTH_MODE=token` they are gated
  like the rest of the surface, but they remain demo controls, not production endpoints.)

These are maturity milestones, not gaps — calling them out is part of the design's rigor, and
the ones scoped in [WORKPLAN.md](WORKPLAN.md) are actively being built next.
