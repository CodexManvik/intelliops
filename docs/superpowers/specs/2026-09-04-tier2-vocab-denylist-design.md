# Tier-2 remediation vocabulary + destructive-action denylist — Design Spec (PR B)

**Date:** 2026-09-04
**Owner:** Manvik
**Status:** design (architectural — widens `RemediationStep.action`, the closed safety Literal; adds a denylist gate to the execution flow; extends the k8s dispatch + rollback). Second of a 3-PR arc (A: sandbox ✅ shipped as #33 · **B: denylist + tier-2 vocab** · C: AI-authored runbooks).

**Depends on:** PR A (the remediation sandbox — merged/merging as #33). This PR assumes `execute_remediation` already has the pre-flight `sandbox.rehearse(...)` step and the `preflight` plumbing. Branch PR B **off the updated master** *after* #33 lands (so it builds on the sandbox + #32's Meridian work).

**Companion:** `docs/sandbox-and-ai-runbooks-design-note.md` (the arc's rationale — why the sandbox catches *effect* not *blast radius*, which is exactly why the vocabulary can widen to typed Deployment-scoped actions but a denylist is still required).

## The problem

Today `RemediationStep.action` is a closed `Literal["restart", "scale", "rollback_deploy", "wait"]` (`common/contracts.py:80`). That closed set is the **core safety property**: an AI or a misconfigured playbook literally cannot express an unsafe action — `model_validate` rejects anything outside the four. But four verbs is thin for a credible AIOps remediation catalog. The user wants a wider, still-typed vocabulary so playbooks can do more than restart/scale/rollback/wait — without opening the door to catastrophic actions, and without weakening the "every action is typed and rehearsed" guarantee PR A established.

## Goal

Widen the vocabulary to **7 typed, Deployment-scoped actions** (add 3), and add a **defense-in-depth denylist gate** that inspects each step for dangerous shapes and refuses them *before* the sandbox runs. Every new action:
- targets the **Deployment** the incident already resolves to (`resolve_target` → `RemediationTarget(namespace, deployment)`), so it needs no new target-resolution;
- is a single typed `AppsV1Api` call in `_dispatch` (no shell, no string-built manifests);
- has a **same-shape rollback** so the reversible-only gate (ADR-007) still holds;
- is **fully sandbox-rehearsable** (the existing `NamespaceCloneSandbox` clones a Deployment, applies the same typed plan, watches the clone's pod) — so PR A's guarantee extends to every new verb.

## Key decisions (locked with the user)

1. **Scope: Deployment-scoped only.** Add exactly three actions: `patch_resource_limits`, `rollback_to_revision`, `patch_probe`. **Node actions** (`cordon_node`/`uncordon_node`) and **HPA actions** (`patch_hpa`) are **explicitly deferred to a later PR** — a node is cluster-scoped and singular (cannot be cloned into a sandbox, so the sandbox guarantee does not hold; cordoning a node evicts every pod on it — the widest blast radius in tier-2), and an HPA needs a different API (AutoscalingV2), its own target-resolution, and only *partially* rehearses (the patch applies, but the autoscaler's live-metric behavior isn't reproduced on a clone). Including them here would ship actions the sandbox can't protect. They get their own PR with proper target-resolution + rehearsal design.
   - **`rollback_to_revision` requires a sandbox extension (decided with the user):** a fresh clone has only revision 1 (no history), so `rollback_to_revision N` cannot rehearse against a naive clone. To keep the "every new action is sandbox-rehearsed" invariant intact for all three, PR B **extends `NamespaceCloneSandbox` (PR A's adapter) to seed the clone's revision history** — copy the production Deployment's owned ReplicaSets (with their `deployment.kubernetes.io/revision` annotations, stripped of cluster-assigned fields) into the sandbox namespace before applying the fix. This is the one place PR B touches PR A's code. See §4.
2. **Catastrophic actions stay permanently excluded.** `delete`, `exec`, scale-to-zero, secret access, and any cluster-scoped mutation are never added to the Literal (tier-3, permanently out). Tier-4 (an open free-form DSL) is rejected.
3. **The denylist is a defense-in-depth gate**, not merely a name blocklist. Because the Literal is already closed, a name blocklist is largely redundant *today*. The gate's real value is inspecting step **shapes/params** for dangerous values (a scale that would hit 0, a limit set implausibly low, a revision that doesn't exist) and refusing them — and it is positioned to guard **PR C's AI-authored runbooks** and any future open field, where the Literal alone won't suffice. Runs **before the sandbox** (per the arc's core insight: the sandbox catches effect, not blast radius — a dangerous-but-valid-looking step could pass the rehearsal, so a hard gate must precede it).
4. **Additive contract, test-safe.** New action-params are optional fields defaulting `None`; the existing 4 actions and all existing playbooks/tests are byte-identical. No behavior change on the default path.

## Non-goals / constraints

- **No node or HPA actions** (decision 1). No new API clients beyond `AppsV1Api` (already used by `KubernetesRemediator`). No `AutoscalingV2Api`, no `CoreV1` node calls.
- **No change to the gate ORDER of the existing gates or the sandbox** — the denylist inserts as a new gate; every existing gate keeps its exact reason string and position.
- **No RBAC granularity change.** RBAC stays coarse (`execute` on `playbook:*`); safety for the new actions comes from the closed Literal + the denylist + the sandbox, not per-action RBAC. (Noted, not changed.)
- **Test-safe default.** The base suite (443 on the post-#33 branch) stays green; `ruff` clean; `npm --prefix frontend run build` clean. The new actions only *run* against a real cluster (`REMEDIATOR_MODE=k8s`); `DryRunRemediator` is action-agnostic (logs `step.action` generically) so it needs no change.
- **Fail-safe dispatch.** `_dispatch`'s new branches follow the existing never-raise pattern (`KubernetesRemediator._run` catches all exceptions → `False`). A new action that errors is a safe failure, not an escape.

## Global Constraints

- **Gates:** `uv run pytest -m "not postgres and not kafka"` green (443 on the post-#33 base + new tests); `ruff check .` + `ruff format --check .` clean; `npm --prefix frontend run build` clean.
- **Safety invariant (must survive):** `RemediationStep.action` stays a closed `Literal`; `model_validate` rejects any out-of-set action; the sandbox rehearses every new action; the denylist runs before the sandbox and fails closed.
- **Commit trailer** on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **git:** branch `feat/tier2-vocab-denylist` off **the updated master** (after #33 merges). PR; user merges. Never merge to master.
- **Shared files:** `common/contracts.py`, `services/action/remediate.py`, `services/action/adapters/k8s_remediator.py`, and the action tests. Frontend `_format_steps`/display is best-effort (the new actions render via the existing generic `step.action`/`note` formatting — no required frontend change, but confirm the incident panel still renders them legibly).

---

## Design

### 1. Contract: widen the Literal + add typed params (`common/contracts.py`)

Current (`:79-82`):
```python
class RemediationStep(BaseModel):
    action: Literal["restart", "scale", "rollback_deploy", "wait"]
    replicas: int | None = None  # for scale: a delta, e.g. +2 / -2
    note: str | None = None  # human-readable / wait annotation
```

New:
```python
class RemediationStep(BaseModel):
    action: Literal[
        "restart",
        "scale",
        "rollback_deploy",
        "wait",
        "patch_resource_limits",
        "rollback_to_revision",
        "patch_probe",
    ]
    replicas: int | None = None  # for scale: a delta, e.g. +2 / -2
    note: str | None = None  # human-readable / wait annotation
    # patch_resource_limits: new container resource ceilings (a targeted change,
    # not a full spec rewrite). At least one must be set for the action to do work.
    cpu_limit: str | None = None  # e.g. "500m"
    mem_limit: str | None = None  # e.g. "512Mi"
    container: str | None = None  # which container; None -> the first/only one
    # rollback_to_revision: the Deployment revision to roll back to.
    revision: int | None = None
    # patch_probe: adjust a liveness/readiness probe timing (the common real fix
    # for a probe that's too aggressive and killing a slow-starting pod).
    probe: Literal["liveness", "readiness"] | None = None
    initial_delay_seconds: int | None = None
    period_seconds: int | None = None
    timeout_seconds: int | None = None
    failure_threshold: int | None = None
```

All new fields are optional/defaulted → additive. Existing steps (only `action`/`replicas`/`note`) validate unchanged. **This is the single load-bearing safety change: the Literal grows by exactly three vetted, Deployment-scoped verbs and nothing else.**

### 2. The denylist gate (`services/action/remediate.py`)

A new module-level function + a gate call inserted **after RBAC (Gate 2), before the plan-build + sandbox**:

```python
# services/action/remediate.py

# Hard safety floor: step shapes we refuse regardless of playbook/HITL/sandbox.
# The closed action Literal already excludes catastrophic *verbs*; this guards
# dangerous *shapes* of allowed verbs, and is the gate that will also protect
# AI-authored runbooks (PR C) and any future open field. Runs BEFORE the sandbox
# because the sandbox catches an action's effect, not its blast radius.
def _denylist_reason(playbook: Playbook) -> str | None:
    for step in playbook.steps + playbook.rollback_steps:
        if step.action == "scale":
            # A scale delta that could drive replicas to 0 is a disguised
            # take-down. (The k8s dispatch clamps to >=1, but a playbook that
            # *intends* 0 must be refused, not silently clamped.)
            if step.replicas is not None and step.replicas <= -_SCALE_FLOOR_GUARD:
                return "denied:unsafe-scale"
        if step.action == "patch_resource_limits":
            # Reject implausibly tiny ceilings that would OOM/throttle the pod
            # into a worse state than the incident.
            if _limit_below_floor(step.cpu_limit, step.mem_limit):
                return "denied:unsafe-limits"
        if step.action == "patch_probe":
            # Reject probe params that would make the probe trivially pass
            # (defeating the probe) or impossibly strict.
            if _probe_out_of_bounds(step):
                return "denied:unsafe-probe"
    return None
```

Gate insertion in `execute_remediation` (after the Gate 2 RBAC block at `:98-101`, before the plan-build at `:103`):
```python
    # Gate 2.5: destructive-shape denylist (fail closed). Runs before the
    # sandbox — a dangerous-but-valid-looking step could pass the rehearsal.
    reason = _denylist_reason(playbook)
    if reason is not None:
        _audit(gate, situation, playbook, "denied-unsafe")
        return _outcome(situation, playbook, RemediationResult.FAILURE, reason)
```

The exact numeric floors (`_SCALE_FLOOR_GUARD`, the CPU/mem minimums, the probe bounds) are named constants at the top of the module with a comment justifying each. The gate never raises; a malformed step (e.g. an unparseable limit string) is treated as unsafe → refused (fail closed).

**What this gate does and does NOT do (honest, per decision 3):** it guards the *shape* of allowed actions. It is deliberately NOT the thing that stops `delete` — the closed Literal does that (a `delete` step never validates, so it never reaches this gate). Documented in the module docstring and the design note.

### 3. Dispatch: three new typed branches (`services/action/adapters/k8s_remediator.py`)

Each new action is one `if step.action == "X"` block in `_dispatch` (mirroring the existing four at `:71-102`), using only `AppsV1Api`:

- **`patch_resource_limits`** → `patch_namespaced_deployment(deployment, ns, body)` with a strategic-merge body that sets `spec.template.spec.containers[<container or 0>].resources.limits` to the given `cpu_limit`/`mem_limit` (only the keys provided). A targeted patch, not a full spec rewrite.
- **`rollback_to_revision`** → resolve the target revision's pod template: list the Deployment's ReplicaSets (`list_namespaced_replica_set` filtered by owner + the `deployment.kubernetes.io/revision` annotation == `step.revision`), take that ReplicaSet's `spec.template`, and `patch_namespaced_deployment` to set the Deployment's `spec.template` to it. (This is the real "roll back to revision N" — distinct from the existing `rollback_deploy`, which is just a rollout-restart.)
- **`patch_probe`** → `patch_namespaced_deployment` setting the chosen container's `livenessProbe`/`readinessProbe` timing fields (only those provided).

Each also gets a **rollback path**: the dispatch is called for `plan.rollback_steps` too (via `KubernetesRemediator.rollback → _run(target, rollback_steps)`), so a playbook supplies a same-shape inverse step (e.g. `patch_resource_limits` back to the prior ceilings; `patch_probe` back to prior timings). The playbook author is responsible for the inverse (as today for scale's +/- delta); the spec's job is that the dispatch *can* execute the inverse. The unknown-action `logger.warning` fallback at `:102` stays.

### 4. Sandbox: seed revision history so `rollback_to_revision` rehearses (`services/action/adapters/sandbox.py`)

`patch_resource_limits` and `patch_probe` are pure forward patches — the existing `NamespaceCloneSandbox` (PR A) rehearses them on the clone with **no change** (it clones the Deployment, applies `plan.steps` via `KubernetesRemediator(sandbox_ns, apps_v1=apps_v1).execute(clone_plan)`, watches the clone's pod). That is the payoff of the Deployment-scoped decision.

`rollback_to_revision` is the exception: a freshly-created clone has only revision 1, so `rollback_to_revision N` (N>1) finds no such revision and cannot rehearse. **PR B extends the clone step** to seed history:

- After creating the clone Deployment (and before the rollout-wait / fix-apply), read the **production** Deployment's owned ReplicaSets (`list_namespaced_replica_set` filtered by owner-ref + the `deployment.kubernetes.io/revision` annotation), strip cluster-assigned fields (resourceVersion/uid/creationTimestamp/status/ownerReferences — reuse the existing `_strip_*` helper pattern), retarget them at `sandbox_ns`, re-point their owner to the **clone** Deployment (set the ownerReference uid/name to the clone's), and create them in the sandbox namespace. This gives the clone the same revision history the production Deployment has, so a `rollback_to_revision N` in `plan.steps` finds revision N on the clone and rehearses truthfully.
- This is **best-effort and fail-safe** (matching the existing Service/ConfigMap clone helpers): if history seeding fails, log and continue — for a plan that does NOT use `rollback_to_revision`, seeding is unnecessary and its failure must not fail the rehearsal. Only a `rollback_to_revision` plan against a clone whose seeding failed will (correctly) rehearse as a failure.
- Factor this as a new `_seed_revision_history_best_effort(apps_v1, source_dep, clone_dep, sandbox_ns)` helper alongside `_clone_service_best_effort` / `_clone_config_maps_best_effort`, called from `rehearse` right after the clone Deployment is created.

**Honest limit to document:** seeding copies the ReplicaSet *specs* (pod templates + revision annotations), so `rollback_to_revision` rehearses that "the rollback applies and the resulting pod template is the historical one and the pod comes up healthy." It does not reproduce any production runtime state those historical pods had. For PR B this is sufficient — pod-readiness of the rolled-back clone is the pass signal, consistent with PR A's stance. Note it in the design note + README.

The live rehearsal of all three new actions is part of the documented manual e2e (§8 / README).

### 5. Formatting / frontend (best-effort, likely no change)

`_format_steps` (`remediate.py:49-59`) already handles any action generically (`scale` special-case + `note` + bare `action`). The new actions render as e.g. `patch_resource_limits: <note>` or bare `patch_resource_limits`. Optionally enrich `_format_steps` with a short human phrase per new action (e.g. `patch_resource_limits cpu=500m mem=512Mi`) for a nicer incident-panel line — a small, additive polish, not required for correctness. No `types.ts` change (steps are already `string[]`).

---

## Acceptance criteria

1. **Vocabulary widened, still closed:** `RemediationStep.action` accepts the 7 verbs; `RemediationStep(action="delete")` (and any other out-of-set string) still raises `ValidationError`. Unit test both.
2. **Additive contract:** an existing step `RemediationStep(action="restart")` validates unchanged; all new param fields default `None`. The 443-test base stays green.
3. **Denylist fails closed BEFORE the sandbox:** unit tests over `execute_remediation` — (a) a playbook with an unsafe-scale step → `denied:unsafe-scale`, `remediator.execute` NOT called AND `sandbox.rehearse` NOT called (the gate precedes both); (b) unsafe limits → `denied:unsafe-limits`; (c) unsafe probe → `denied:unsafe-probe`; (d) a safe tier-2 playbook passes the gate and proceeds to the sandbox/normal flow. Assert the gate ordering (denylist before sandbox) explicitly.
4. **Each new action dispatches to the right typed call:** unit tests with a fake `AppsV1Api` (mirroring the existing `k8s_remediator` tests) — `patch_resource_limits` calls `patch_namespaced_deployment` with the expected resources body; `rollback_to_revision` reads ReplicaSets then patches the template; `patch_probe` patches the probe fields. Each never raises on an API error (returns `False`).
5. **Rollback symmetry:** a tier-2 playbook's `rollback_steps` dispatch through the same `_dispatch` (test the rollback path executes the inverse step).
6. **Sandbox rehearses the new actions:** `patch_resource_limits`/`patch_probe` flow through `NamespaceCloneSandbox` unchanged (unit-level assertion a tier-2 plan is accepted by `rehearse`). For `rollback_to_revision`: `_seed_revision_history_best_effort` copies the source Deployment's ReplicaSets into the clone namespace, re-owned by the clone — unit test with a fake client that (a) on a `rollback_to_revision` plan, `list_namespaced_replica_set` is read and the RSes are created in `sandbox_ns` re-owned to the clone, and (b) seeding failure is swallowed (best-effort — a non-rollback plan still rehearses; the whole `rehearse` still never raises).
7. **Gates green:** 443 + new tests; ruff clean; frontend build clean.
8. **(Manual, documented)** live: with the k8s overlay, a playbook using `patch_resource_limits` (or `patch_probe`) is rehearsed in a sandbox namespace and, on approval, applied to the real Deployment; the incident panel shows the step + the pre-flight verdict. Documented in `deploy/k8s/README.md`.

## Suggested task ordering (for the plan)

1. **Contract:** widen the `action` Literal to 7 + add the optional typed params. Unit tests: the 3 new verbs validate; `delete` still rejected; an old step unchanged; suite green. (Keeps everything green — nothing dispatches the new verbs yet.)
2. **Denylist gate:** `_denylist_reason` + the constants + the Gate 2.5 insertion in `execute_remediation` (before plan-build/sandbox). Unit tests for the 3 unsafe cases + the safe pass-through + the ordering assertion (denylist before sandbox.rehearse). (The heart of the safety addition — testable with stubs, no cluster.)
3. **Dispatch:** the three new `_dispatch` branches + the rollback path, with a fake-`AppsV1Api` test each (correct call, never raises) + a rollback-path test. Reuse the existing `k8s_remediator` test's fake-client style.
4. **Sandbox: seed revision history + confirm rehearsal + formatting polish:** add `_seed_revision_history_best_effort` to `NamespaceCloneSandbox` (called from `rehearse` after the clone Deployment is created; best-effort/fail-safe like the Service/ConfigMap helpers); unit-test the seeding (RSes read + re-created re-owned; failure swallowed; `rehearse` never raises); assert a `patch_resource_limits`/`patch_probe` plan flows through unchanged; optionally enrich `_format_steps` for the new actions; frontend build stays clean.
5. **Docs:** `deploy/k8s/README.md` — add the tier-2 actions to the "what each playbook does" material + the manual e2e for a `patch_resource_limits`/`patch_probe`/`rollback_to_revision` rehearsal; document the revision-history seeding + its honest limit (specs seeded, not runtime state); note the permanently-excluded set (delete/exec/scale-to-0/secrets/cluster-scope) and the deferred node/HPA actions. Reference the design note. Final gates.

Rationale: contract first (green, nothing uses the verbs yet), then the denylist (the safety gate, stub-testable), then the real dispatch (the k8s calls, fake-client tested), then the sandbox extension + rehearsal confirmation (the one place PR B touches PR A's adapter), then docs — each task independently testable, the safety invariant provable at every step.

## Note for the plan (pin these concretely)

- **Denylist floor constants must be pinned to real values** in Task 2 (the spec leaves them named): e.g. `_SCALE_FLOOR_GUARD` such that a delta driving replicas to 0 is refused; a CPU floor (e.g. reject `< 10m`) and a mem floor (e.g. reject `< 16Mi`); probe bounds (reject `failure_threshold < 1`, non-positive periods, or values so lax the probe is defeated). The plan states each number + a one-line justification; the implementer uses them verbatim.
- **`timeout_seconds` field name:** `RemediationStep` gains a `timeout_seconds` field (for `patch_probe`). Note it is unrelated to `execute_remediation`'s `timeout_seconds` *parameter* (a function arg, no collision) — the plan should call this out so the implementer doesn't conflate them.
- **`_strip_replica_set` reuse:** the history-seeding strip logic mirrors the existing `_strip_deployment`/`_strip_service` helpers in `sandbox.py`; the plan should point the implementer at those as the pattern (strip resourceVersion/uid/creationTimestamp/status/managedFields/selfLink, retarget namespace, re-point ownerReferences to the clone).
