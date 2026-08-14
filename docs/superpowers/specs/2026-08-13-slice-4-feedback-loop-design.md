# Slice 4 — Feedback Loop + Closing the Loop Design

**Date:** 2026-08-13
**Status:** Approved for planning — the FINAL slice
**Parent spec:** [2026-08-13-intelliops-coe-design.md](2026-08-13-intelliops-coe-design.md) (§4 feedback loop, §7 feedback, ADR-008 evidence-based graduation; the closed loop is the project's central innovation)
**Builds on:** Slices 0-3 (skeleton, ingestion→correlation, rca+governance, action), all on master.

> Every engineering decision the parent spec left open is tagged **[INVENTED]** here.

## 1. Goal & scope

Close the loop and finish the project: `feedback-service` consumes `remediation.outcomes`, labels
each as training data, persists it to a shared training store; `correlation` reads that store to
make `RiverCorrelator.retrain` real (a genuine behavioral change); `feedback` computes
outcome-derived metrics and, on evidence, proposes a playbook for graduation, which governance
promotes hitl→auto under RBAC.

**Non-goals (this slice):** Postgres training store, real MTTR/MTTD timestamp threading, ChatOps
graduation approval, model persistence across restarts (the reliability map is rebuilt from the
store), and the `common/` shared-adapter refactor (deferred to a dedicated post-Slice-4 PR).
Absence is by design.

## 2. New shared pieces (`common/`)

### 2.1 Contract (`common/contracts.py`, additive) **[INVENTED]**

```
TrainingRecord:
    situation_id: str
    signature: str
    playbook_id: str
    result: RemediationResult          # success | failure | rolled_back
    worked: bool                        # result == success
    ts: datetime
```

Additive; frozen contracts untouched. `RemediationOutcome` (the input) is unchanged.

### 2.2 Interface (`common/interfaces.py`, additive) **[INVENTED]**

```
TrainingStore (Protocol, runtime_checkable):
    append(record: TrainingRecord) -> None
    read_all() -> list[TrainingRecord]
```

In-memory (tests) + File JSONL (running service). Postgres deferred. Same pattern as `AuditSink`.

### 2.3 Config (`common/config.py`) **[INVENTED]**

Add `training_store_path: str = "data/training.jsonl"`, `reliability_suppress_threshold: float = 0.8`,
`graduation_min_successes: int = 3`.

## 3. The signature bridge **[INVENTED]**

`RemediationOutcome` has `situation_id` but no `signature`. Since `RiverCorrelator.correlate`
sets `id = "sit-" + signature`, the signature is recoverable by stripping the `"sit-"` prefix
(verified: `situation_id[4:] == signature`). A helper `signature_from_situation_id(situation_id)`
returns `situation_id.removeprefix("sit-")`. This avoids touching the frozen `RemediationOutcome`.
(Coupling to the id convention is flagged; a future contract could carry the signature explicitly.)

## 4. feedback-service

### 4.1 Labeling (`services/feedback/label.py`)

`label_outcome(outcome: RemediationOutcome) -> TrainingRecord` — derives signature from
`situation_id`, sets `worked = (result == SUCCESS)`, copies playbook_id/result/ts.

### 4.2 Metrics (`services/feedback/metrics.py`) **[INVENTED: outcome-derived only]**

`compute_metrics(records: list[TrainingRecord]) -> dict`:

```
{
  "total_outcomes": N,
  "success_rate": successes/N,     "rollback_rate": rolled_back/N,   "failure_rate": failures/N,
  "by_result": {"success": .., "failure": .., "rolled_back": ..},
  "by_signature": {sig: {"worked": .., "total": ..}, ...},
  "note": "MTTR/MTTD require end-to-end detection→resolution timestamps not yet threaded; metrics are outcome-derived."
}
```

Empty input → zeros (no division by zero). No fabricated MTTR/MTTD — the `note` states what's deferred.

### 4.3 Graduation policy (`services/feedback/graduate.py`) **[INVENTED]**

`playbook_stats(records, playbook_id) -> dict` → `{successes, failures, rollbacks}`.
`should_graduate(stats, min_successes: int) -> bool` → `successes >= min_successes AND failures == 0
AND rollbacks == 0`. Conservative, evidence-based (ADR-008). The threshold is a config value, not a
magic number.

### 4.4 Consumer (`services/feedback/consumer.py` + `app.py` lifespan + `GET /metrics`)

Daemon thread (lifespan) consuming `iter_models(bus, "remediation.outcomes", "feedback",
RemediationOutcome)`. For each: `label_outcome` → `store.append` → update in-memory metrics; then,
per playbook, if `should_graduate` and not already graduated, call `gate.graduate(playbook_id)` (the
governance graduate endpoint via the in-process gate) and record the proposal in the audit. `GET
/metrics` returns `compute_metrics(store.read_all())`. Breaks on stop_event.

## 5. Making retrain real (`services/correlation`)

### 5.1 `RiverCorrelator.retrain` **[INVENTED: per-signature reliability]**

`retrain(training_data: list[dict]) -> None` (signature frozen by the `Correlator` protocol) —
each dict carries at least `{signature, worked}`. Aggregate per signature into a reliability ratio
`worked/total`, store in `self._reliability: dict[str, float]`. Add
`reliability(signature) -> float` (default 0.0 for unseen).

### 5.2 The loop-closing behavior — suppression at emit **[INVENTED]**

Signatures don't exist per-event (`detect` scores a single `TelemetryEvent`); a signature is only
formed when a group is correlated into a `Situation`. So the reliability nudge applies at the
**correlation/emit** stage, NOT per-event detect (which stays unchanged):

`RiverCorrelator.should_suppress(signature, threshold) -> bool` → `reliability(signature) >= threshold`.

`CorrelationEngine._correlate_buffer` forms the `Situation` (which has a signature); the engine then
checks `should_suppress` — if a signature's remediation reliably WORKS (reliability ≥ threshold), the
formed Situation is **suppressed** (not emitted / `add`/`flush` return None) because the system has
learned "when this fires, we fix it — don't re-alert." A signature that FAILS/rolls back stays
fully sensitive (low reliability → not suppressed). This is the genuine behavioral change that
closes the loop: **accuracy compounds — proven self-healing incidents stop generating noise.**

Bounded by design: suppression only ever applies to signatures with a *proven* success history in
the store; an unseen or failing signature is never suppressed (fail-safe toward alerting).

## 6. Governance change

### 6.1 Graduate endpoint **[INVENTED: RBAC-gated hitl→auto]**

`POST /playbooks/{id}/graduate` (body `{decided_by}`): 404 if unknown; RBAC check
`rbac.check(decided_by, "graduate", f"playbook:{id}")` → 403 if denied; else flip the playbook's
`hitl_mode` hitl→auto in the store, write an audit record, return the updated Playbook. Policy
grants a `coe-admin` role `graduate playbook:*`; feedback-service acts as an authorized grad­uator
(add `feedback-service` → a role with graduate, or `coe-admin`).

## 7. Data flow (the closed loop)

```
[remediation.outcomes] (RemediationOutcome, from action)
  → feedback consumer: label_outcome → TrainingStore.append + metrics
       └─ per playbook: should_graduate? → governance POST /playbooks/{id}/graduate (RBAC, hitl→auto)
  ...retrain time...
  → correlation reads TrainingStore → RiverCorrelator.retrain(training_data) → per-signature reliability
       └─ CorrelationEngine suppresses emit for reliably-self-healing signatures  ⟲ LOOP CLOSED
  GET /metrics on feedback → outcome-derived metrics
```

## 8. Testing

- **Unit:** `TrainingRecord`/`TrainingStore` round-trip (in-mem + file); `label_outcome`
  (signature-from-id, worked flag); `compute_metrics` (rates, by-signature, empty→zeros);
  `playbook_stats` + `should_graduate` (meets/doesn't-meet each condition); `retrain` +
  `reliability` (aggregates correctly); `should_suppress` (≥ threshold True, below False, unseen
  False); engine suppresses a reliable signature's Situation but still emits an unreliable one;
  governance graduate endpoint (200 flips hitl→auto, 403 unauthorized, 404 unknown);
  feedback consumer (labels + stores + proposes graduation).
- **Acceptance (in-process):** feed a stream of SUCCESS outcomes for one signature into feedback →
  training store fills → correlation.retrain(store) → assert that signature is now suppressed
  (engine returns None for a would-be Situation of that signature) while a different, failing
  signature is still emitted; AND assert the playbook graduated hitl→auto via governance. Full loop,
  no infra.

## 9. Repository layout

```
common/            contracts.py(+TrainingRecord)  interfaces.py(+TrainingStore)  config.py(+3)
services/feedback/ app.py  label.py  metrics.py  graduate.py  consumer.py  adapters/training_store.py  tests/
services/correlation/  adapters/river_correlator.py (+retrain/reliability/should_suppress)  engine.py (+suppress)
services/governance/   app.py (+graduate endpoint)   policies/rbac_policy.yaml (+graduate grant)
tests/test_slice4_acceptance.py
```

## 10. Build phasing

1. Contracts (`TrainingRecord`) + interface (`TrainingStore`) + config.
2. TrainingStore adapters (InMemory + File JSONL).
3. `label_outcome` + signature helper.
4. `compute_metrics`.
5. `playbook_stats` + `should_graduate`.
6. `RiverCorrelator.retrain` (real) + `reliability` + `should_suppress`; engine suppresses at emit.
7. Governance graduate endpoint (RBAC-gated hitl→auto) + policy grant.
8. feedback consumer + lifespan + `GET /metrics`.
9. Acceptance (loop closes + graduation) + README flip + **mark project complete**.

## 11. Compliance mapping (parent spec §12)

Closing the loop realizes the parent architecture's central claim — outcomes become training data,
accuracy compounds — with running code. Evidence-based graduation under RBAC + audit satisfies the
NIST AI RMF *Measure*/*Manage* functions (the feedback metrics quantify behavior; graduation is a
governed, audited control change).

## 12. Deferred / open (not blocking)

- Postgres training store; real MTTR/MTTD timestamp threading; ChatOps graduation; reliability-map
  persistence across restarts (rebuilt from the store on retrain).
- The `common/` shared-adapter refactor (rca/action/feedback importing governance adapters) — its
  own post-Slice-4 PR.
- Slice-1 cold-start warm-up (reduced not eliminated); Slice-0 Dockerfile `--frozen --no-dev`.
