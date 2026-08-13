# Slice 3 — Action / HITL-Gated Remediation Design

**Date:** 2026-08-13
**Status:** Approved for planning
**Parent spec:** [2026-08-13-intelliops-coe-design.md](2026-08-13-intelliops-coe-design.md) (§4.1 services, §4.3 sync gate, §7 action/governance, §8 HITL modes, §10 Slice 3; ADR-003 active gate, ADR-007 reversible-only, ADR-008 HITL modes)
**Builds on:** Slices 0-2 (skeleton, ingestion→correlation, rca+governance), all on master.

> Every engineering decision the parent spec left open is tagged **[INVENTED]** here.

## 1. Goal & scope

Turn the `action` stub into a working service that consumes `situations.diagnosed`, selects a
playbook, passes it through **three hard safety gates**, executes reversible remediation, verifies
health, rolls back on failure, and emits a `RemediationOutcome` on `remediation.outcomes`. Also
harden governance's approval endpoint with an RBAC check on the decider.

**The three gates (enforced in code, not convention):**
1. **RBAC fail-closed** — no execution without a governance `allow`; an unreachable/denying gate → no action.
2. **Reversible-only** — a non-`reversible` playbook is *refused* for auto-execution (surfaced only). ADR-007.
3. **HITL** — a `hitl` playbook waits for an explicit human `approved`; reject/timeout → no action, fail-closed. ADR-008.

**Non-goals (this slice):** real Kubernetes/Ansible execution, real health probing (Prometheus
re-query), ChatOps/Slack approval UI, feedback-loop consumption of outcomes (Slice 4), real HTTP
between services. Absence is by design.

## 2. New shared pieces (`common/`)

### 2.1 Interfaces (`common/interfaces.py`, additive) **[INVENTED]**

`Remediator` already exists (`execute(steps) -> bool`, `rollback(steps) -> bool`). Add:

```
GovernanceGate (Protocol):
    check_rbac(actor: str, action: str, resource: str) -> bool
    request_approval(request: ApprovalRequest) -> ApprovalRequest
    await_decision(approval_id: str, timeout_seconds: float) -> ApprovalRequest
    write_audit(record: AuditRecord) -> None

HealthChecker (Protocol):
    check(situation: Situation) -> bool
```

`GovernanceGate` is the **single seam** through which action reaches governance's RBAC, approval
store, and audit sink — the consolidation the Slice-2 review flagged. `HealthChecker` supplies the
post-remediation health signal (ADR-007's verify step).

### 2.2 Config (`common/config.py`) **[INVENTED]**

Add `hitl_poll_timeout_seconds: float = 30.0` and `hitl_poll_interval_seconds: float = 0.5`. The
HITL wait polls `await_decision` at the interval until a non-pending status or the timeout.

### 2.3 Contracts — NO changes

`RemediationOutcome` (Slice 0) is the topic currency, reused for **every** decision:
`situation_id, playbook_id, result: RemediationResult (success|failure|rolled_back), health_after: str, ts`.
Non-executed decisions encode the reason in `health_after` (see §4.3). **[INVENTED: reuse rather
than a new ActionDecision contract — one clean currency, and Slice-4 feedback sees every decision.]**

## 3. action-service

### 3.1 Playbook selection (`services/action/select.py`)

`select_playbook(diagnosed: DiagnosedSituation, gate_or_store) -> Playbook | None` — resolves
`diagnosed.suggested_runbook_id` against the playbook registry (via the gate/store). Returns None
if there is no suggested runbook or the id is unknown → outcome `"skipped:no-playbook"`.

### 3.2 Remediator adapters (`services/action/adapters/remediator.py`) **[INVENTED: dry-run default]**

- `DryRunRemediator` — logs the steps, returns True; performs **no real side effects**. The safe
  running-service default.
- `RecordingRemediator` — captures `execute`/`rollback` calls and returns injectable outcomes; the
  test double that proves the safety assertions (was execute called? was rollback called?).
- Real `K8sRemediator`/`AnsibleRemediator` are deferred.

### 3.3 Health checker (`services/action/adapters/health.py`) **[INVENTED]**

`AlwaysHealthyChecker` (default, pairs with dry-run) + a fake for tests that returns healthy or
unhealthy on demand, so tests drive **both** the success path and the rollback path.

### 3.4 In-process governance gate (`services/action/adapters/governance_gate.py`) **[INVENTED]**

`InProcessGovernanceGate(rbac, approvals, audit_sink, poll_interval)` — constructed with references
to the SAME `RbacPolicy`, approval dict, and `AuditSink` governance-service uses (shared state, not
a copy). `check_rbac` delegates to `rbac.check`; `request_approval` inserts a pending
`ApprovalRequest`; `await_decision` polls the approval store until non-pending or timeout;
`write_audit` writes to the audit sink. An `HTTPGovernanceGate` (real httpx) is a deferred stub.

### 3.5 Orchestration (`services/action/remediate.py`) — the heart

`execute_remediation(diagnosed, playbook, gate, remediator, health, settings) -> RemediationOutcome`:

```
actor = "action-service"; resource = f"playbook:{playbook.id}"; cid = situation.id
audit every branch with correlation_id = cid.

1. playbook.hitl_mode == disabled  → outcome failure "skipped:disabled"
2. not playbook.reversible         → outcome failure "refused:not-reversible"   (ADR-007)
3. gate.check_rbac(actor,"execute",resource) is False → outcome failure "denied:rbac"  (fail-closed)
4. if hitl_mode == hitl:
     req = gate.request_approval(ApprovalRequest(...pending...))
     decided = gate.await_decision(req.id, settings.hitl_poll_timeout_seconds)
     if decided.status != "approved" → outcome failure
         ("aborted:rejected" if status=="rejected" else "aborted:timeout")
   # hitl_mode == auto proceeds directly (already RBAC-checked; auto also requires reversible, met by step 2)
5. ok = remediator.execute(playbook.steps)
   if not ok → outcome failure "execute-failed"
6. if health.check(situation):     → outcome success "healthy"
   else: remediator.rollback(playbook.rollback_steps)
                                    → outcome rolled_back "unhealthy:rolled-back"
```

### 3.6 Consumer (`services/action/consumer.py` + `app.py` lifespan)

Daemon thread (lifespan, same pattern as correlation/rca) consuming
`iter_models(bus, "situations.diagnosed", "action", DiagnosedSituation)`. For each: `select_playbook`
→ if None emit `"skipped:no-playbook"` outcome; else `execute_remediation` →
`publish_model(bus, "remediation.outcomes", outcome)`. Breaks on stop_event.

## 4. Governance change

### 4.1 RBAC on decide **[INVENTED: security hardening]**

`POST /approvals/{id}/decide` currently accepts any decision from anyone. Add: the `Decision` body
gains no new field, but the endpoint checks `app.state.rbac.check(decision.decided_by, "approve",
f"playbook:{req.playbook_id}")`; if False → **403**. The policy already grants the `approver` role
`approve playbook:*`. This closes the "anyone can approve" gap now that a real caller exists.

## 5. Outcome mapping (what lands on remediation.outcomes)

| Scenario | result | health_after |
|----------|--------|--------------|
| executed, healthy | success | healthy |
| executed, unhealthy → rolled back | rolled_back | unhealthy:rolled-back |
| execute() returned False | failure | execute-failed |
| no suggested/known playbook | failure | skipped:no-playbook |
| playbook disabled | failure | skipped:disabled |
| not reversible | failure | refused:not-reversible |
| RBAC denied | failure | denied:rbac |
| hitl rejected | failure | aborted:rejected |
| hitl timeout | failure | aborted:timeout |

## 6. Data flow

```
[situations.diagnosed] (DiagnosedSituation, from rca)
   → action consumer → select_playbook → execute_remediation (3 gates) → RemediationOutcome
   → publish [remediation.outcomes]   (Slice-4 feedback consumes)
   → gate.write_audit(...) for every branch (correlation_id = situation.id)
```

## 7. Testing (the safety model is the heart)

- **Unit:** each Remediator + HealthChecker + InProcessGovernanceGate (request/await/timeout);
  `select_playbook`; and `execute_remediation` with a `RecordingRemediator` proving, per gate:
  disabled → no execute; not-reversible → no execute; RBAC denied → no execute; hitl rejected → no
  execute; hitl timeout → no execute (fail-closed); auto approved → execute; executed+unhealthy →
  rollback called, outcome rolled_back; executed+healthy → success.
- **Governance:** `/approvals/{id}/decide` returns 403 when the decider lacks the approve permission,
  200 + updated request when they have it.
- **Consumer:** scripted bus → one `RemediationOutcome` per diagnosed situation, correct topic.
- **Acceptance (in-process):** (a) a `hitl` reversible playbook, approved by an authorized actor,
  executes, health OK → `success` on `remediation.outcomes` + audit trail; (b) same but
  health-checker returns unhealthy → `rollback` invoked, outcome `rolled_back`. RecordingRemediator
  asserts execute/rollback call sequence. No real infra.

## 8. Repository layout

```
common/           interfaces.py(+2 protocols)  config.py(+2 settings)
services/action/  app.py  select.py  remediate.py  consumer.py
                  adapters/{remediator,health,governance_gate}.py  tests/
services/governance/  app.py (+RBAC on decide)
tests/test_slice3_acceptance.py
```

## 9. Build phasing (governance hardening first, then action bottom-up)

1. Interfaces (`GovernanceGate`, `HealthChecker`) + config (2 timeouts).
2. Governance RBAC-on-decide (403).
3. Remediator adapters (DryRun + Recording).
4. HealthChecker adapters (AlwaysHealthy + fake).
5. InProcessGovernanceGate (request/await/timeout/audit).
6. select_playbook.
7. execute_remediation (the orchestration + all three gates + outcome mapping).
8. action consumer + lifespan.
9. Acceptance (success + rollback paths) + README flip.

## 10. Compliance mapping (parent spec §12, ADR-003/007/008)

This slice makes the parent architecture's central safety claims *executable*: the sync fail-closed
gate (ADR-003), reversible-only with rollback (ADR-007), and HITL-before-action (ADR-008) — all
enforced in the call graph and proven by tests that assert the gates BLOCK.

## 11. Deferred / open (not blocking)

- Real K8s/Ansible Remediator; real health probing; HTTPGovernanceGate; ChatOps approval.
- Playbook `auto` graduation policy (ADR-008 evidence-based promotion) — mode is honored, graduation is manual/later.
- Slice-2 tech debt (shared adapters → `common/`): the `GovernanceGate` seam is the first step; full move is still open.
- Slice-4 feedback consumes `remediation.outcomes`.
