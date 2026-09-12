# Remediation Sandbox (pre-flight rehearsal) — Design Spec (PR A)

**Date:** 2026-08-29
**Owner:** Manvik
**Status:** design (architectural — new Sandbox adapter + a pre-flight gate in the execution flow + additive contract/read/UI). First of a 3-PR arc (A: sandbox · B: denylist + tier-2 vocab · C: AI-authored runbooks).

**Companion design note:** `docs/sandbox-and-ai-runbooks-design-note.md` (the honest, code-grounded rationale — why k8s dry-run is not a rehearsal, why Daytona is the wrong tool here, what the sandbox catches vs doesn't).

## The problem

Today `execute_remediation` (`services/action/remediate.py`) runs: gates (disabled / reversible / RBAC / HITL-approval) → execute on the LIVE target → health-check → rollback-if-unhealthy. There is **no trial step** — the first time a fix touches a real pod is production. `dry_run` mode is log-only (`DryRunRemediator.execute` literally `return True`), so it rehearses nothing. The user wants a real "try it safely first, then the human confirms" flow: rehearse the fix on an isolated copy, observe real health, and show the human the verdict *before* they approve.

## Goal

Add a **pre-flight sandbox rehearsal**: before the human approves (and before an auto-playbook executes), clone the target Deployment into a throwaway namespace, apply the *same* remediation steps to the clone, watch the clone's pod become healthy via the existing `KubernetesHealthChecker`, tear the clone down, and surface the pass/fail verdict — to the approving human and in the incident timeline.

## Key decisions (locked with the user)

1. **Mechanism:** ephemeral-namespace clone in the SAME kind cluster (a real pod, real probes, real Prometheus-observed health — the honest limit is shared-node, not production-isolated). NOT Daytona (wrong tool — it sandboxes code, not k8s workloads), NOT a second kind cluster.
2. **Modes:** `sandbox_mode = "off"` (default, test-safe — `NullSandbox`, passes through, base demo/tests unchanged, config-switch per ADR-012) and `"k8s"` (the real `NamespaceCloneSandbox`). No middle `dry_run` tier (low value).
3. **Timing:** the sandbox runs **BEFORE** the HITL approval wait, so the human approves with the verdict in hand; for `auto` playbooks (no human), a failed sandbox blocks.
4. **Fail policy:** sandbox FAIL → **block** an `auto` playbook (return a `preflight-failed` outcome); for `hitl`, **advise** — attach the verdict to the approval request and proceed to the human, who decides.

## Non-goals / constraints

- **No change to the remediation LOGIC or the k8s remediator's action set** — that's PR B. This PR adds a rehearsal step + surfaces its result. The 4-action vocabulary is unchanged here.
- **Test-safe default.** `sandbox_mode` defaults to `"off"`; the base compose + the existing ~433 suite + CI are byte-identical. The real sandbox is opt-in via the k8s overlay (like `REMEDIATOR_MODE=k8s`).
- **Fail-safe.** The sandbox never raises out of `execute_remediation` — any error → `PreflightResult(passed=False, ...)`, mirroring `k8s_remediator`/`k8s_health`'s never-raise pattern.
- **Additive contracts** (the shipped `mode`/`steps` precedent): new optional fields default None; projection reads via `getattr`; frontend fields optional.
- **Live k8s e2e is a documented manual step** (needs a running kind cluster — user's machine). Everything else (the gate logic, `NullSandbox` path, the contract/projection/UI plumbing, the config switch) is unit-tested + verified here.

## Global Constraints

- **Gates:** `uv run pytest -m "not postgres and not kafka"` green (~433 + new tests); `ruff check .` + `ruff format --check .` clean; `npm --prefix frontend run build` clean.
- **No fabricated data:** the UI shows the real sandbox verdict or an honest "not rehearsed" when `off`.
- **Commit trailer** on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **git:** branch `feat/remediation-sandbox` (off master). PR; user merges.
- **Shared files:** `common/contracts.py`, `common/interfaces.py`, `common/config.py`, `services/action/remediate.py`, `services/action/consumer.py`, `services/action/app.py`, `services/action/adapters/`, `services/read/projection.py`, `frontend/src/data/types.ts`, `frontend/src/views/Incidents.tsx`.

---

## Design

### 1. Contract: `PreflightResult` + fields (`common/contracts.py`)

```python
class PreflightResult(BaseModel):
    passed: bool
    detail: str            # human-readable: "sandbox: pod healthy in 8s" / "sandbox: clone crashlooped" / "not rehearsed (sandbox off)"
    mode: str              # "off" | "k8s"
    sandbox_namespace: str | None = None  # the throwaway ns, for audit
```

Add (additive, optional):
- `RemediationOutcome.preflight: PreflightResult | None = None`
- `ApprovalRequest.preflight: PreflightResult | None = None`  (so the HITL human sees the verdict)

### 2. Protocol: `Sandbox` (`common/interfaces.py`)

```python
@runtime_checkable
class Sandbox(Protocol):
    def rehearse(self, situation: Situation, plan: RemediationPlan) -> PreflightResult: ...
```

### 3. Adapters (`services/action/adapters/sandbox.py`, new)

- **`NullSandbox`** (default): `rehearse(...) -> PreflightResult(passed=True, detail="not rehearsed (sandbox off)", mode="off")`. No cluster access, no-op — keeps base demo/tests unchanged.
- **`NamespaceCloneSandbox`** (`mode="k8s"`): given the `RemediationPlan` (which carries `target.namespace`/`target.deployment` + the `steps`):
  1. Generate `sandbox_ns = f"intelliops-sandbox-{uuid4().hex[:8]}"`.
  2. Read the target Deployment (+ its Service + referenced ConfigMap) via the k8s API; deep-copy the specs into `sandbox_ns` (strip cluster-assigned fields: resourceVersion, uid, status, clusterIP, nodePort; rename to avoid collisions but KEEP labels so the existing Prometheus scrape/relabel can discover the clone — OR add the scrape annotation). Preserve resource requests/limits so the rehearsal is representative.
  3. Create the namespace + the cloned objects; wait for the clone's initial rollout (bounded timeout).
  4. Apply the SAME `plan.steps` to the clone (reuse the `KubernetesRemediator._dispatch` logic against `sandbox_ns`/clone-deployment — factor the dispatch so both the real remediator and the sandbox share it, OR the sandbox constructs a `KubernetesRemediator(sandbox_ns)` and calls `.execute(clone_plan)`).
  5. Run `KubernetesHealthChecker.check(situation, clone_target)` (bounded poll) — pod-ready + metric predicate against the clone.
  6. `passed = health result`; build `PreflightResult(passed, detail, mode="k8s", sandbox_namespace=sandbox_ns)`.
  7. **Always** delete `sandbox_ns` in a `finally` (best-effort teardown; a teardown failure is logged, not raised).
  8. **Any exception** anywhere → `PreflightResult(passed=False, detail=f"sandbox error: {exc.__class__.__name__}", ...)` + attempt teardown. Never raises.

  (Implementation note: the metric-recovery check for a clone is subtle — the demo's `cpu_usage` is per-metric-name, so the clone's series must be distinguishable/scraped. For PR A, pod-readiness of the clone is the primary signal; the metric predicate is best-effort. Document this honestly; PR B/later can sharpen the clone's metric wiring.)

### 4. Factory + wiring (`services/action/app.py`)

Add `_make_sandbox(settings)`:
```python
def _make_sandbox(settings):
    if settings.sandbox_mode == "k8s":
        return NamespaceCloneSandbox(settings.k8s_namespace, prometheus_url=settings.prometheus_url, ...)
    return NullSandbox()
```
Thread it into `run_consumer` (one new positional arg) → `execute_remediation`.

### 5. Config (`common/config.py`)

```python
    sandbox_mode: str = "off"  # "off" | "k8s"
```

### 6. The pre-flight gate (`services/action/remediate.py`)

`execute_remediation` gains a `sandbox` parameter. **Reorder so the sandbox runs before the HITL wait.** The current order is: disabled → reversible → RBAC → HITL-approval → resolve target/plan → execute. New order:
- disabled → reversible → RBAC (unchanged)
- **resolve target + build plan EARLIER** (move the `resolve_target`/`RemediationPlan` build to before Gate 3, since the sandbox needs the plan).
- **Pre-flight rehearsal:** `preflight = sandbox.rehearse(situation, plan)`.
  - If `not preflight.passed` AND `playbook.hitl_mode == HitlMode.AUTO`: audit `"preflight-failed"`, return `_outcome(..., "preflight-failed", preflight=preflight, ...)` — **block, do not execute**.
  - (If passed, or if HITL, continue — the verdict rides along.)
- **Gate 3 (HITL):** build the `ApprovalRequest` WITH `preflight=preflight` attached, so the human sees it. `await_decision` as before; reject/timeout unchanged.
- **Execute → health → rollback** (unchanged), and every terminal `_outcome(...)` now also passes `preflight=preflight` so it reaches the UI.

Keep every existing gate's behavior and reason string identical; only ADD the pre-flight step + thread `preflight` onto the outcomes and the approval request.

### 7. Consumer (`services/action/consumer.py`)

`run_consumer` gains a `sandbox` parameter (positional, after `health`), passed into `execute_remediation`. The `select_playbook is None` skip-path is unchanged.

### 8. Read projection (`services/read/projection.py`)

In `apply_outcome`, copy the preflight onto the projected outcome via `getattr(o, "preflight", None)` (like `mode`/`steps`). Include it in the `outcome` dict the situation drill-down reads. Zero read-route change.

### 9. Frontend (`frontend/src/data/types.ts` + `views/Incidents.tsx`)

- `SituationOutcome.preflight?: { passed: boolean; detail: string; mode: string } | null`.
- In the incident result/timeline panel (near the `dry-run` chip), render a **pre-flight row**: "🧪 Pre-flight: rehearsed in sandbox — **passed**" (green) / "**failed** — {detail}" (red) / hide or show "not rehearsed" when `mode === "off"`. Honest, real data only.

---

## Acceptance criteria

1. **Config-switched, test-safe:** `sandbox_mode` defaults to `"off"`; `NullSandbox` passes through; the base compose + existing suite are byte-identical (no new behavior on the default path). Unit test: `NullSandbox.rehearse` returns `passed=True, mode="off"`.
2. **The gate blocks auto on failure, advises HITL:** unit tests over `execute_remediation` with a stub sandbox — (a) auto playbook + sandbox fail → `preflight-failed` outcome, `remediator.execute` NOT called; (b) hitl playbook + sandbox fail → proceeds to approval with `preflight` attached to the `ApprovalRequest`; (c) sandbox pass → normal flow, `preflight` on the outcome.
3. **Sandbox runs before approval:** the `ApprovalRequest` carries the `PreflightResult` (verified in the hitl test).
4. **`NamespaceCloneSandbox` is fail-safe:** unit test with a fake k8s client — an exception during clone → `passed=False`, never raises; teardown attempted.
5. **Additive result flow:** `preflight` reaches the projected outcome (projection test) and renders in the UI (build + the row present). Mock mode unaffected.
6. **Gates green:** ~433 + new tests; ruff clean; frontend build clean.
7. **(Manual, documented)** live: with the k8s overlay + `sandbox_mode=k8s`, breaking a Meridian service and approving shows a real sandbox rehearsal (a `intelliops-sandbox-*` namespace appears + is torn down; the verdict shows in the UI before approval). Documented in `deploy/k8s/README.md`.

## Suggested task ordering (for the plan)

1. `PreflightResult` contract + `RemediationOutcome.preflight`/`ApprovalRequest.preflight` fields + `Sandbox` protocol + `sandbox_mode` config + `NullSandbox`. Unit test (Null passes through; contract additive; suite green).
2. The pre-flight gate in `execute_remediation` (reorder plan-build before Gate 3; add rehearsal; block-auto/advise-hitl; thread `preflight` onto outcomes + the approval request) + `consumer.py` + `app.py` `_make_sandbox` wiring. Unit tests for the 3 gate cases (2a/2b/2c above).
3. `NamespaceCloneSandbox` (clone/apply/health/teardown, fail-safe) + a unit test with a fake k8s client (fail-safe + teardown). Reuse/refactor `KubernetesRemediator._dispatch` + `KubernetesHealthChecker`.
4. Read projection (`getattr` preflight) + frontend types + the incident pre-flight row + mock fixture. Build + projection test.
5. Docs: `deploy/k8s/README.md` sandbox section (the live flow) + commit the design note; final gates.

Rationale: contract + Null first (keeps everything green), then the gate logic (the heart, unit-testable with stubs), then the real clone adapter (the k8s build), then the UI, then docs.
