# Slice 2 — RCA + Governance Design

**Date:** 2026-08-13
**Status:** Approved for planning
**Parent spec:** [2026-08-13-intelliops-coe-design.md](2026-08-13-intelliops-coe-design.md) (§4.1 services, §5 contracts, §6 interfaces, §7 rca/governance, §10 Slice 2)
**Builds on:** Slice 0 (skeleton), Slice 1 (ingestion→correlation, on master)

> Every engineering decision the parent spec left open is tagged **[INVENTED]** here.

## 1. Goal & scope

Turn the `rca` and `governance` stubs into working services:

- **rca-service** consumes `situations.detected`, enriches each `Situation` with deploy/topology/config
  context, ranks root-cause hypotheses, surfaces a runbook, marks the situation `diagnosed`, and
  emits a `DiagnosedSituation` on `situations.diagnosed`.
- **governance-service** exposes the control-plane REST API: an immutable audit log, a playbook
  registry, RBAC checks, and approval endpoints (pre-built for Slice 3, unused this slice).

**Non-goals (this slice):** enforcing RBAC (Slice 3 wires action→governance), real remediation,
the feedback loop, real Postgres/Prometheus/CMDB/git integrations, callers of the approval
endpoints. Absence of these is by design.

## 2. New shared pieces (`common/`)

### 2.1 Contracts (`common/contracts.py`, additive — frozen models untouched) **[INVENTED]**

```
EnrichmentContext:
    recent_deploys: list[dict]      # [{service, version, ts}, ...]
    topology: dict                  # service -> [dependencies]
    config_changes: list[dict]      # [{key, ts}, ...]
    # empty lists/dicts when no provider data

DiagnosedSituation:
    situation: Situation                    # status == diagnosed
    hypotheses: list[RootCauseHypothesis]   # ranked best-first
    suggested_runbook_id: str | None
```

`DiagnosedSituation` is the currency of the `situations.diagnosed` topic, parallel to how
`Situation` is the currency of `situations.detected`. It is additive — the frozen `Situation`
and existing `RootCauseHypothesis` are unchanged. **[INVENTED: rejected mutating `Situation` or
publishing a bare hypotheses list — see the parent architecture's frozen-contract rule.]**

### 2.2 Interfaces (`common/interfaces.py`, additive) **[INVENTED]**

```
PlaybookStore (Protocol):
    register(playbook: Playbook) -> None
    get(playbook_id: str) -> Playbook | None
    list() -> list[Playbook]

ContextProvider (Protocol):
    recent_deploys() -> list[dict]
    topology_for(labels: dict[str, str]) -> dict
    config_changes() -> list[dict]
```

`AuditSink` already exists (Slice 0: `write(record: AuditRecord) -> None`) — reused as-is.

### 2.3 Config (`common/config.py`) **[INVENTED]**

Add to `Settings`: `audit_store_path: str = "data/audit.jsonl"`,
`playbook_store_path: str = "data/playbooks"`, `rbac_policy_path: str = "policies/rbac_policy.yaml"`,
`rca_context_path: str = "data/rca_context"`. All optional with defaults; no new required infra.

## 3. governance-service

### 3.1 RBAC (`governance/rbac.py`) **[INVENTED: static role→permission]**

`RbacPolicy` loaded from a YAML file:

```yaml
roles:
  operator:   [{action: "enrich",  resource: "situation:*"},
               {action: "diagnose", resource: "situation:*"}]
  approver:   [{action: "approve", resource: "playbook:*"}]
actors:
  rca-service: [operator]
  oncall-alice: [operator, approver]
```

`check(actor, action, resource) -> bool`: the actor's roles' rules are scanned; a rule matches when
`action` equals the rule action and `resource` matches the rule's glob pattern (`fnmatch`). Default
deny. No inheritance, no explicit deny rules this slice.

### 3.2 Persistence adapters **[INVENTED: file default, in-memory for tests]**

- `governance/adapters/audit_sink.py`: `InMemoryAuditSink` (list) + `FileAuditSink` (append JSONL to
  `audit_store_path`). Both satisfy `AuditSink`.
- `governance/adapters/playbook_store.py`: `InMemoryPlaybookStore` (dict) + `FilePlaybookStore` (one
  YAML/JSON per playbook under `playbook_store_path`). Both satisfy `PlaybookStore`.
- Postgres adapters are explicitly deferred (later slice / hardening).

### 3.3 REST API (`governance/app.py`)

| Endpoint | Purpose |
|----------|---------|
| `POST /audit` | write an `AuditRecord` (fire-and-forget from callers) → 200 |
| `GET /audit?correlation_id=` | query audit records (all, or by correlation_id) |
| `POST /playbooks` | register a `Playbook` → 200 |
| `GET /playbooks` / `GET /playbooks/{id}` | list / fetch |
| `POST /rbac/check` | body `{actor, action, resource}` → `{allowed: bool}` |
| `POST /approvals` | create an `ApprovalRequest` → returns it (Slice 3 driver) |
| `POST /approvals/{id}/decide` | body `{decision, decided_by}` → updated request |
| `GET /health` | unchanged |

Approval store is in-memory this slice (Slice 3 exercises it). Sinks/stores are attached to
`app.state` in the lifespan so tests can swap them.

## 4. rca-service

### 4.1 Context provider (`rca/adapters/context_provider.py`) **[INVENTED: file/static]**

`FileContextProvider(path)` reads `deploys.json`, `topology.json`, `config_changes.json` from
`rca_context_path`. `NullContextProvider` returns empty context (the safe default and the test
double). Both satisfy `ContextProvider`.

### 4.2 Enrichment (`rca/enrich.py`)

`enrich(situation: Situation, provider: ContextProvider) -> EnrichmentContext` — pulls
`recent_deploys()`, `topology_for(labels)` (union of member-event labels), `config_changes()`,
and returns them as an `EnrichmentContext`. Pure function of (situation, provider).

### 4.3 Ranking (`rca/rank.py`) **[INVENTED: rule-based]**

`rank_hypotheses(situation, context) -> list[RootCauseHypothesis]` — deterministic rules, each
producing a scored hypothesis when it fires, sorted by confidence desc:

- **recent-deploy**: a deploy in `context.recent_deploys` whose service matches a situation member
  label, within a time window of `situation.first_seen` → high confidence (~0.8),
  `suggested_runbook_id="rollback-deploy"`.
- **resource-exhaustion**: member event names match a saturation pattern (cpu/mem/disk high) →
  medium (~0.6), `suggested_runbook_id="scale-service"`.
- **error-spike**: member events are logs/errors → medium (~0.5),
  `suggested_runbook_id="restart-pod"`.
- **fallback**: if no rule fires, one low-confidence "unknown" hypothesis (~0.2, no runbook).

`surface_runbook(hypothesis, store: PlaybookStore) -> Playbook | None` — looks up the top
hypothesis's `suggested_runbook_id` in the playbook registry.

### 4.4 Consumer (`rca/consumer.py` + `rca/app.py` lifespan)

Same pattern as correlation (Slice 1): a daemon thread started via FastAPI lifespan consumes
`situations.diagnosed`'s upstream — i.e. `iter_models(bus, "situations.detected", "rca", Situation)`.
For each: `enrich` → `rank_hypotheses` → set `situation.status = DIAGNOSED` → build
`DiagnosedSituation` → `publish_model(bus, "situations.diagnosed", diagnosed)`. Also writes an
`AuditRecord` (actor `"rca-service"`, action `"diagnose"`, resource `f"situation:{sit.id}"`,
`correlation_id=sit.id`) to the governance audit sink. The consumer is given its bus, context
provider, and audit sink; the lifespan wires the concrete ones.

**Audit path [INVENTED]:** to keep Slice 2 in-process-testable, rca writes audit via an injected
`AuditSink` (the same `FileAuditSink` the governance service uses), not an HTTP call to governance.
The `POST /audit` HTTP endpoint exists for cross-service callers; the co-located sink is the direct
path. Both write the same records to the same store.

## 5. Data flow

```
[situations.detected] → rca consumer:
    enrich(situation, ContextProvider)         → EnrichmentContext
    rank_hypotheses(situation, context)        → [RootCauseHypothesis] (ranked)
    surface_runbook(top, PlaybookStore)        → runbook id
    situation.status = diagnosed
    → DiagnosedSituation → publish [situations.diagnosed]   (consumed by action, Slice 3)
    → AuditSink.write(diagnose record, correlation_id=sit.id)
```

## 6. Testing

- **Unit:** `RbacPolicy.check` (allow/deny/glob/default-deny), audit sink round-trip (in-mem + file),
  playbook store round-trip, `DiagnosedSituation`/`EnrichmentContext` contract round-trips,
  `enrich` (fake provider), `rank_hypotheses` (each rule + fallback + ordering),
  `surface_runbook`, governance API endpoints (TestClient), the rca consumer (scripted bus).
- **Integration (acceptance):** a `Situation` whose members imply a recent deploy → onto
  `situations.detected` (in-memory bus) → rca consumer → assert exactly one `DiagnosedSituation`
  on `situations.diagnosed` with top hypothesis `recent-deploy` and `status == diagnosed`, and
  assert the audit sink recorded a `diagnose` record with `correlation_id == situation.id`.
  Fully in-process, no infra.

## 7. Repository layout

```
common/          contracts.py(+2)  interfaces.py(+2)  config.py(+4 settings)
services/rca/    app.py  enrich.py  rank.py  consumer.py  adapters/context_provider.py  tests/
services/governance/  app.py  rbac.py  adapters/{audit_sink,playbook_store}.py  tests/
policies/rbac_policy.yaml
playbooks/*.yaml
data/            (gitignored; file sinks write here)
tests/test_slice2_acceptance.py
```

## 8. Build phasing (task order — governance before rca, since rca reads the playbook registry)

1. Contracts + interfaces + config additions.
2. Governance: RBAC policy + `/rbac/check`.
3. Governance: audit sink + `/audit`.
4. Governance: playbook registry + `/playbooks` + seed playbooks.
5. Governance: approval endpoints (pre-built for Slice 3).
6. RCA: context provider (File/Null) + fixtures.
7. RCA: enrich + rank + surface_runbook.
8. RCA: consumer + lifespan (detected → diagnosed, writes audit).
9. Acceptance + README roadmap flip + sample data.

## 9. Compliance mapping (parent spec §12)

Audit log (immutable, correlation-id threaded) + RBAC policy realize the NIST AI RMF *Govern* and
*Manage* functions and seed the EU AI Act documentation trail — now with running code, not just
design.

## 10. Deferred / open (not blocking)

- Real Postgres audit + playbook store (swappable adapter, later).
- Real deploy/topology/config providers (Prometheus, CMDB, git) — file provider stands in.
- RBAC enforcement at an action gate (Slice 3).
- Approval-endpoint callers (Slice 3 action-service).
- Cold-start warm-up: already handled in Slice 1 (per-metric gate).
