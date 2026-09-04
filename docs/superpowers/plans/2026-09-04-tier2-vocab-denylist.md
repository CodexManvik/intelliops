# Tier-2 remediation vocabulary + destructive-action denylist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Widen `RemediationStep.action` from 4 to 7 typed, Deployment-scoped actions (`+patch_resource_limits`, `+rollback_to_revision`, `+patch_probe`), add a defense-in-depth denylist gate that refuses dangerous step shapes before the sandbox runs, dispatch each new action to a single typed AppsV1 call, and extend the sandbox to seed the clone's revision history so `rollback_to_revision` rehearses truthfully.

**Architecture:** The closed `Literal` stays the core safety property (grows by exactly 3 vetted verbs; `model_validate` still rejects everything else). A new `_denylist_reason` gate in `execute_remediation` runs right after RBAC and before the plan-build/sandbox, failing closed on unsafe shapes. Each new action is one `if step.action == "X"` branch in `KubernetesRemediator._dispatch` using only `AppsV1Api`, with a same-shape rollback. `NamespaceCloneSandbox` gains a best-effort `_seed_revision_history_best_effort` so a fresh clone has the revision history `rollback_to_revision` needs.

**Tech Stack:** Python 3.11/3.12, Pydantic v2, FastAPI, `kubernetes` client (AppsV1Api only), pytest, React/TypeScript (Vite) — frontend touched only if the optional formatting polish is done.

**Spec:** `docs/superpowers/specs/2026-09-04-tier2-vocab-denylist-design.md` (read it alongside this plan). Companion rationale: `docs/sandbox-and-ai-runbooks-design-note.md`. This is PR B of a 3-PR arc (A: sandbox, shipped as #33; B: this; C: AI-authored runbooks).

## Global Constraints

- **Branch `feat/tier2-vocab-denylist` off the UPDATED master** — this PR builds on PR A (#33, the sandbox) and #32 (Meridian). Confirm `services/action/adapters/sandbox.py` contains `NamespaceCloneSandbox` and `execute_remediation` in `services/action/remediate.py` has the `sandbox` param + the `sandbox.rehearse(...)` pre-flight step BEFORE you start. If they are absent, #33 has not merged yet — STOP and report.
- **Gates:** `uv run pytest -m "not postgres and not kafka"` green (baseline ~443 on the post-#33 base + new tests); `ruff check .` and `ruff format --check .` clean; `npm --prefix frontend run build` clean.
- **Env:** a bare `uv sync` OMITS the ML + k8s extras and the suite will fail to collect (`ModuleNotFoundError: river` / no `kubernetes`). Run `uv sync --extra ml --extra k8s` once at setup.
- **Safety invariant (must survive every task):** `RemediationStep.action` stays a closed `Literal`; `RemediationStep(action="delete")` must still raise `ValidationError`; the sandbox rehearses every new action; the denylist runs BEFORE the sandbox and fails closed.
- **Additive/test-safe:** every new contract field is optional, defaulting `None`. The 4 existing actions and all existing playbooks/tests are byte-identical. `DryRunRemediator` is action-agnostic (logs `step.action`) — do NOT change it.
- **Fail-safe dispatch:** new `_dispatch` branches never raise — `KubernetesRemediator._run` already wraps `_dispatch` in a catch-all → `False`. Do not add a bare `raise` in a branch.
- **Commit trailer** on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **git:** push; open a PR against master; the USER merges. Never merge to master.

---

## File Structure

- `common/contracts.py` — widen `RemediationStep.action` Literal (4→7) + add optional typed params.
- `services/action/remediate.py` — add `_denylist_reason` + floor constants; insert the Gate 2.5 denylist check (after RBAC, before plan-build).
- `services/action/adapters/k8s_remediator.py` — three new `_dispatch` branches (`patch_resource_limits`, `rollback_to_revision`, `patch_probe`); each also runs on the rollback path.
- `services/action/adapters/sandbox.py` — `_seed_revision_history_best_effort` helper + call it from `NamespaceCloneSandbox.rehearse` after the clone Deployment is created.
- `deploy/k8s/README.md` — document the tier-2 actions, the history-seeding + its honest limit, the permanently-excluded set, the deferred node/HPA actions.
- Tests (flat `tests/` + per-service `services/action/tests/` — BOTH exist): 
  - `tests/test_remediation_contracts.py` (or a new `tests/test_tier2_contracts.py`) — the Literal-widening + rejection + additive tests.
  - `tests/test_remediate_sandbox.py` — extend with the denylist gate cases (this file already tests `execute_remediation` gate behavior with stubs).
  - `services/action/tests/test_k8s_remediator.py` — extend with the 3 new dispatch tests + rollback-path test (reuse its `FakeAppsV1` pattern).
  - `tests/test_sandbox_adapter.py` — extend with the revision-history-seeding test.

---

## Task 1: Widen the action Literal + add typed params

**Files:**
- Modify: `common/contracts.py:79-82` (`RemediationStep`)
- Test: `tests/test_tier2_contracts.py` (new)

**Interfaces:**
- Produces: `RemediationStep.action` accepts `"patch_resource_limits" | "rollback_to_revision" | "patch_probe"` (plus the original 4); new optional fields `cpu_limit`, `mem_limit`, `container`, `revision`, `probe`, `initial_delay_seconds`, `period_seconds`, `timeout_seconds`, `failure_threshold` (all `... | None = None`).

**Note:** the new `timeout_seconds` FIELD on `RemediationStep` is unrelated to the `timeout_seconds` PARAMETER of `execute_remediation` (a function arg) — no collision, but do not conflate them.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tier2_contracts.py`:

```python
import pytest
from pydantic import ValidationError

from common.contracts import RemediationStep


def test_new_tier2_actions_validate():
    for action in ("patch_resource_limits", "rollback_to_revision", "patch_probe"):
        step = RemediationStep(action=action)
        assert step.action == action


def test_existing_actions_unchanged():
    step = RemediationStep(action="restart")
    assert step.action == "restart"
    assert step.replicas is None and step.note is None


def test_out_of_set_action_still_rejected():
    # The closed-Literal safety property: an unlisted action must not validate.
    for bad in ("delete", "exec", "scale_to_zero", "drain_node", ""):
        with pytest.raises(ValidationError):
            RemediationStep(action=bad)


def test_tier2_params_are_optional_and_additive():
    # All new params default None; a step needs only `action`.
    step = RemediationStep(action="patch_resource_limits", cpu_limit="500m", mem_limit="512Mi")
    assert step.cpu_limit == "500m" and step.mem_limit == "512Mi"
    assert step.container is None
    probe = RemediationStep(action="patch_probe", probe="liveness", initial_delay_seconds=10)
    assert probe.probe == "liveness" and probe.initial_delay_seconds == 10
    rev = RemediationStep(action="rollback_to_revision", revision=3)
    assert rev.revision == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tier2_contracts.py -v`
Expected: FAIL — `patch_resource_limits` etc. raise `ValidationError` (not yet in the Literal).

- [ ] **Step 3: Widen the Literal + add the params**

In `common/contracts.py`, replace the `RemediationStep` class body (`:79-82`) with:

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
    # patch_resource_limits: new container resource ceilings (targeted change).
    cpu_limit: str | None = None  # e.g. "500m"
    mem_limit: str | None = None  # e.g. "512Mi"
    container: str | None = None  # which container; None -> first/only
    # rollback_to_revision: the Deployment revision to roll back to.
    revision: int | None = None
    # patch_probe: adjust a liveness/readiness probe's timing.
    probe: Literal["liveness", "readiness"] | None = None
    initial_delay_seconds: int | None = None
    period_seconds: int | None = None
    timeout_seconds: int | None = None  # probe timeout; NOT the remediation timeout
    failure_threshold: int | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tier2_contracts.py -v`
Expected: PASS (all 4).

- [ ] **Step 5: Full suite (nothing dispatches the new verbs yet → all green)**

Run: `uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check .`
Expected: baseline + 4 new, all green; lint clean.

- [ ] **Step 6: Commit**

```bash
git add common/contracts.py tests/test_tier2_contracts.py
git commit -m "feat(vocab): widen RemediationStep.action to 7 typed Deployment-scoped actions

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: The destructive-shape denylist gate

**Files:**
- Modify: `services/action/remediate.py` (add constants + `_denylist_reason`; insert Gate 2.5 after the RBAC block, before the plan-build)
- Test: `tests/test_remediate_sandbox.py` (extend — it already tests `execute_remediation` gate behavior with stubs)

**Interfaces:**
- Consumes: `Playbook`, `RemediationStep` (Task 1).
- Produces: `_denylist_reason(playbook: Playbook) -> str | None`; a new Gate 2.5 in `execute_remediation` returning `_outcome(..., RemediationResult.FAILURE, reason)` when the denylist trips, with reasons `denied:unsafe-scale` / `denied:unsafe-limits` / `denied:unsafe-probe`.

**Ordering (load-bearing):** Gate 2.5 goes AFTER the Gate 2 RBAC block (currently `remediate.py:98-101`) and BEFORE the plan-build/sandbox (`:103-114`). The denylist must precede `sandbox.rehearse` — the sandbox catches an action's *effect*, not its *blast radius*, so a dangerous-but-valid-looking step could pass the rehearsal.

- [ ] **Step 1: Write the failing tests**

Extend `tests/test_remediate_sandbox.py`. It already has stub `_Gate`, `_Remediator`, `_Health`, `_StubSandbox`, `_situation`, `_playbook` helpers — REUSE them. Add a stub-sandbox spy so a test can assert the sandbox was NOT reached. If the existing `_StubSandbox` doesn't record calls, add a `rehearsed` flag to a local subclass in these tests, or check `remediator.executed is False` plus a fresh sandbox whose `rehearse` sets a flag. The key assertions: unsafe → the denylist reason, and NEITHER `remediator.execute` NOR `sandbox.rehearse` ran.

```python
from common.contracts import Playbook, HitlMode, RemediationStep, RemediationResult
from services.action.remediate import execute_remediation


def _pb(steps, hitl=HitlMode.HITL):
    return Playbook(id="pb-1", name="n", match_rule="*", steps=steps, hitl_mode=hitl, reversible=True)


class _SpySandbox:
    def __init__(self):
        self.rehearsed = False
    def rehearse(self, situation, plan):
        self.rehearsed = True
        from common.contracts import PreflightResult
        return PreflightResult(passed=True, detail="", mode="off")


def test_denylist_blocks_unsafe_scale_before_sandbox():
    gate, remediator, health = _Gate(), _Remediator(), _Health()
    sandbox = _SpySandbox()
    # a scale delta that would drive replicas to zero
    pb = _pb([RemediationStep(action="scale", replicas=-50)])
    outcome = execute_remediation(_situation(), pb, gate, remediator, health, sandbox, 1.0, 0.01)
    assert outcome.result == RemediationResult.FAILURE
    assert outcome.health_after == "denied:unsafe-scale"
    assert remediator.executed is False       # never executed
    assert sandbox.rehearsed is False         # gate precedes the sandbox


def test_denylist_blocks_unsafe_limits():
    gate, remediator, health, sandbox = _Gate(), _Remediator(), _Health(), _SpySandbox()
    pb = _pb([RemediationStep(action="patch_resource_limits", cpu_limit="1m", mem_limit="1Ki")])
    outcome = execute_remediation(_situation(), pb, gate, remediator, health, sandbox, 1.0, 0.01)
    assert outcome.health_after == "denied:unsafe-limits"
    assert sandbox.rehearsed is False


def test_denylist_blocks_unsafe_probe():
    gate, remediator, health, sandbox = _Gate(), _Remediator(), _Health(), _SpySandbox()
    pb = _pb([RemediationStep(action="patch_probe", probe="liveness", failure_threshold=0)])
    outcome = execute_remediation(_situation(), pb, gate, remediator, health, sandbox, 1.0, 0.01)
    assert outcome.health_after == "denied:unsafe-probe"
    assert sandbox.rehearsed is False


def test_denylist_allows_safe_tier2_and_reaches_sandbox():
    gate, remediator, health, sandbox = _Gate(), _Remediator(), _Health(), _SpySandbox()
    pb = _pb([RemediationStep(action="patch_resource_limits", cpu_limit="500m", mem_limit="512Mi")])
    outcome = execute_remediation(_situation(), pb, gate, remediator, health, sandbox, 1.0, 0.01)
    assert sandbox.rehearsed is True          # safe step passed the gate → sandbox ran
    assert remediator.executed is True
```

If the existing `_Gate`/`_Remediator`/`_Health`/`_situation` helpers have different names, adapt these to match the file — but keep the assertions (reason string + `sandbox.rehearsed is False`).

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_remediate_sandbox.py -k denylist -v`
Expected: FAIL — no denylist gate yet (the unsafe playbooks currently proceed to the sandbox/execute).

- [ ] **Step 3: Add the constants + `_denylist_reason` + the gate**

In `services/action/remediate.py`, add near the top (after `_ACTOR = "action-service"`):

```python
# Destructive-shape denylist floors. The closed action Literal already excludes
# catastrophic *verbs*; these guard dangerous *shapes* of allowed verbs and are
# the gate that will also protect AI-authored runbooks (PR C). Each floor is a
# deliberate, documented safety minimum.
_SCALE_TAKEDOWN_DELTA = -10     # a scale delta of -10 or beyond can zero any in-range
                                # deployment (_MAX_REPLICAS is 10) → treated as a take-down
_MIN_CPU_MILLICORES = 10        # reject cpu_limit below 10m (would throttle the pod to death)
_MIN_MEM_MEBIBYTES = 16         # reject mem_limit below 16Mi (would OOM immediately)
_MIN_FAILURE_THRESHOLD = 1      # a probe failureThreshold < 1 is invalid/defeats the probe
_MIN_PROBE_PERIOD_SECONDS = 1   # non-positive probe periods are invalid
```

Add the helpers + `_denylist_reason` (parse `cpu_limit`/`mem_limit` defensively — an unparseable value is treated as unsafe, fail closed):

```python
def _cpu_millicores(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        s = v.strip()
        return float(s[:-1]) if s.endswith("m") else float(s) * 1000.0
    except (ValueError, AttributeError):
        return -1.0  # unparseable -> treat as below any floor (unsafe)


def _mem_mebibytes(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        s = v.strip()
        if s.endswith("Gi"):
            return float(s[:-2]) * 1024.0
        if s.endswith("Mi"):
            return float(s[:-2])
        if s.endswith("Ki"):
            return float(s[:-2]) / 1024.0
        return float(s) / (1024.0 * 1024.0)  # bare bytes
    except (ValueError, AttributeError):
        return -1.0  # unparseable -> unsafe


def _denylist_reason(playbook: Playbook) -> str | None:
    for step in list(playbook.steps) + list(playbook.rollback_steps):
        if step.action == "scale" and step.replicas is not None:
            # We cannot know current replicas here, but a large negative delta is
            # a disguised take-down; the dispatch clamps to >=1, but a playbook
            # that *intends* to zero out a deployment must be refused, not
            # silently clamped. _MAX_REPLICAS is 10, so a delta of -10 or beyond
            # can zero any in-range deployment — refuse it.
            if step.replicas <= _SCALE_TAKEDOWN_DELTA:
                return "denied:unsafe-scale"
        if step.action == "patch_resource_limits":
            cpu = _cpu_millicores(step.cpu_limit)
            mem = _mem_mebibytes(step.mem_limit)
            if (cpu is not None and cpu < _MIN_CPU_MILLICORES) or (
                mem is not None and mem < _MIN_MEM_MEBIBYTES
            ):
                return "denied:unsafe-limits"
        if step.action == "patch_probe":
            if step.failure_threshold is not None and step.failure_threshold < _MIN_FAILURE_THRESHOLD:
                return "denied:unsafe-probe"
            # period_seconds and timeout_seconds must be >= 1 if set (a zero/negative
            # period is invalid k8s and defeats the probe).
            for p in (step.period_seconds, step.timeout_seconds):
                if p is not None and p < _MIN_PROBE_PERIOD_SECONDS:
                    return "denied:unsafe-probe"
            # initial_delay_seconds may be 0 (fine) but not negative.
            if step.initial_delay_seconds is not None and step.initial_delay_seconds < 0:
                return "denied:unsafe-probe"
    return None
```

Note on the scale rule: keep it simple and match the test — a delta `<= -10` is refused as `denied:unsafe-scale` (the test uses `-50`). The `_SCALE_TO_ZERO_GUARD` constant documents intent; the concrete refusal threshold is "a delta of -10 or beyond is treated as a take-down attempt." (Rationale: `_MAX_REPLICAS` is 10, so a -10+ delta can zero out any deployment in range. Tune the number in the constant with a comment; the test asserts -50 is refused and -2 is not.) Ensure a normal `scale replicas=-2` is NOT refused (the existing scale tests and playbooks use small deltas).

Insert the gate in `execute_remediation` immediately AFTER the Gate 2 RBAC block (after `remediate.py:101`) and BEFORE the `# Resolve the target once...` line (`:103`):

```python
    # Gate 2.5: destructive-shape denylist (fail closed). Runs before the plan
    # build and the sandbox — a dangerous-but-valid-looking step could pass the
    # rehearsal, so a hard gate must precede it. Also guards AI-authored runbooks.
    reason = _denylist_reason(playbook)
    if reason is not None:
        _audit(gate, situation, playbook, "denied-unsafe")
        return _outcome(situation, playbook, RemediationResult.FAILURE, reason)
```

- [ ] **Step 4: Run the denylist tests**

Run: `uv run pytest tests/test_remediate_sandbox.py -k denylist -v`
Expected: PASS (all 4).

- [ ] **Step 5: Full suite — confirm existing gate tests still pass (no reason-string or ordering regressions)**

Run: `uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check .`
Expected: green. In particular the existing `services/action/tests/test_remediate.py` gate tests (skipped:disabled / refused:not-reversible / denied:rbac / aborted / healthy / rolled-back) must be unchanged — the denylist is a NEW gate, it must not alter their behavior. A normal `scale replicas=-2` playbook must still reach execution.

- [ ] **Step 6: Commit**

```bash
git add services/action/remediate.py tests/test_remediate_sandbox.py
git commit -m "feat(safety): destructive-shape denylist gate before the sandbox

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Dispatch the three new actions (+ rollback path)

**Files:**
- Modify: `services/action/adapters/k8s_remediator.py:71-102` (`_dispatch` — add 3 branches)
- Test: `services/action/tests/test_k8s_remediator.py` (extend the existing `FakeAppsV1`)

**Interfaces:**
- Consumes: `RemediationStep` (Task 1), `KubernetesRemediator`, `AppsV1Api`.
- Produces: `_dispatch` handles `patch_resource_limits` (→ `patch_namespaced_deployment` resources body), `rollback_to_revision` (→ `list_namespaced_replica_set` then `patch_namespaced_deployment` template), `patch_probe` (→ `patch_namespaced_deployment` probe body). All run on both the execute and rollback paths (via the existing `_run(target, steps)` used by both `.execute`/`.rollback`).

- [ ] **Step 1: Write the failing tests**

Extend `services/action/tests/test_k8s_remediator.py`. Add the new methods to `FakeAppsV1` and new tests. The existing `FakeAppsV1` has `.calls`, `_maybe_fail`, `read_namespaced_deployment`, `patch_namespaced_deployment`, `patch_namespaced_deployment_scale`. Add `list_namespaced_replica_set`:

```python
    def list_namespaced_replica_set(self, namespace, **kwargs):
        self._maybe_fail("list_rs")
        self.calls.append(("list_rs", namespace, kwargs))

        # one RS at revision 3 with a recognizable template
        class _Meta:
            annotations = {"deployment.kubernetes.io/revision": "3"}
            owner_references = None
            name = "demo-app-abc"

        class _Tmpl:
            metadata = type("M", (), {"labels": {"app": "demo-app"}})()
            spec = "TEMPLATE-REV-3"

        class _RS:
            metadata = _Meta()
            spec = type("S", (), {"template": _Tmpl()})()

        return type("L", (), {"items": [_RS()]})()
```

Tests:

```python
def test_patch_resource_limits_patches_container_resources():
    api = FakeAppsV1()
    r = KubernetesRemediator("ns", apps_v1=api, exc_type=FakeApiException)
    step = RemediationStep(action="patch_resource_limits", cpu_limit="500m", mem_limit="512Mi")
    assert r.execute(_plan(step)) is True
    patch = next(c for c in api.calls if c[0] == "patch")
    body = patch[3]
    containers = body["spec"]["template"]["spec"]["containers"]
    assert containers[0]["resources"]["limits"]["cpu"] == "500m"
    assert containers[0]["resources"]["limits"]["memory"] == "512Mi"


def test_patch_probe_patches_liveness_timing():
    api = FakeAppsV1()
    r = KubernetesRemediator("ns", apps_v1=api, exc_type=FakeApiException)
    step = RemediationStep(action="patch_probe", probe="liveness", initial_delay_seconds=30, period_seconds=10)
    assert r.execute(_plan(step)) is True
    patch = next(c for c in api.calls if c[0] == "patch")
    probe = patch[3]["spec"]["template"]["spec"]["containers"][0]["livenessProbe"]
    assert probe["initialDelaySeconds"] == 30 and probe["periodSeconds"] == 10


def test_rollback_to_revision_reads_rs_then_patches_template():
    api = FakeAppsV1()
    r = KubernetesRemediator("ns", apps_v1=api, exc_type=FakeApiException)
    step = RemediationStep(action="rollback_to_revision", revision=3)
    assert r.execute(_plan(step)) is True
    assert any(c[0] == "list_rs" for c in api.calls)
    patch = next(c for c in api.calls if c[0] == "patch")
    # the deployment template is set to the revision-3 RS's template
    assert patch[3]["spec"]["template"] == "TEMPLATE-REV-3"


def test_new_actions_never_raise_on_api_error():
    api = FakeAppsV1(fail_on="patch")
    r = KubernetesRemediator("ns", apps_v1=api, exc_type=FakeApiException)
    assert r.execute(_plan(RemediationStep(action="patch_resource_limits", cpu_limit="500m"))) is False


def test_tier2_rollback_path_dispatches():
    api = FakeAppsV1()
    r = KubernetesRemediator("ns", apps_v1=api, exc_type=FakeApiException)
    step = RemediationStep(action="patch_probe", probe="readiness", period_seconds=5)
    assert r.rollback(_plan(rollback=[step])) is True
    patch = next(c for c in api.calls if c[0] == "patch")
    assert "readinessProbe" in patch[3]["spec"]["template"]["spec"]["containers"][0]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest services/action/tests/test_k8s_remediator.py -v`
Expected: FAIL — the new actions hit the `logger.warning("unknown remediation action")` fallback and make no API calls, so the `next(... "patch" ...)` lookups raise `StopIteration`.

- [ ] **Step 3: Implement the three branches in `_dispatch`**

In `services/action/adapters/k8s_remediator.py`, add these branches inside `_dispatch` BEFORE the final `logger.warning(...)` fallback (`:102`). Match the existing branch style (typed body, single AppsV1 call, `return`):

```python
        if step.action == "patch_resource_limits":
            limits = {}
            if step.cpu_limit is not None:
                limits["cpu"] = step.cpu_limit
            if step.mem_limit is not None:
                limits["memory"] = step.mem_limit
            container = {"resources": {"limits": limits}}
            if step.container is not None:
                container["name"] = step.container
            body = {"spec": {"template": {"spec": {"containers": [container]}}}}
            api.patch_namespaced_deployment(deployment, ns, body)
            return
        if step.action == "patch_probe":
            probe_key = "livenessProbe" if step.probe == "liveness" else "readinessProbe"
            probe_body = {}
            if step.initial_delay_seconds is not None:
                probe_body["initialDelaySeconds"] = step.initial_delay_seconds
            if step.period_seconds is not None:
                probe_body["periodSeconds"] = step.period_seconds
            if step.timeout_seconds is not None:
                probe_body["timeoutSeconds"] = step.timeout_seconds
            if step.failure_threshold is not None:
                probe_body["failureThreshold"] = step.failure_threshold
            container = {probe_key: probe_body}
            if step.container is not None:
                container["name"] = step.container
            body = {"spec": {"template": {"spec": {"containers": [container]}}}}
            api.patch_namespaced_deployment(deployment, ns, body)
            return
        if step.action == "rollback_to_revision":
            rs_list = api.list_namespaced_replica_set(ns)
            target_template = None
            for rs in rs_list.items:
                ann = (rs.metadata.annotations or {}) if rs.metadata else {}
                if ann.get("deployment.kubernetes.io/revision") == str(step.revision):
                    target_template = rs.spec.template
                    break
            if target_template is None:
                # No such revision on this deployment — a real failure, surfaced
                # via the caller's False path (raise a benign error caught by _run).
                raise ValueError(f"revision {step.revision} not found for {deployment}")
            body = {"spec": {"template": target_template}}
            api.patch_namespaced_deployment(deployment, ns, body)
            return
```

Note on `rollback_to_revision` "not found": raising a `ValueError` inside `_dispatch` is caught by `_run`'s catch-all `except Exception` → returns `False` (a safe failure), consistent with the never-raise-OUT contract. Confirm `_run` has the broad `except Exception` (it does at `k8s_remediator.py:66-68`). If you prefer not to raise, `return` after logging — but then `.execute` returns `True` for a no-op, which is wrong; raising→False is correct. Keep the raise.

- [ ] **Step 4: Run the dispatch tests**

Run: `uv run pytest services/action/tests/test_k8s_remediator.py -v`
Expected: PASS (existing + 5 new).

- [ ] **Step 5: Full suite + lint**

Run: `uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check .`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add services/action/adapters/k8s_remediator.py services/action/tests/test_k8s_remediator.py
git commit -m "feat(k8s): dispatch patch_resource_limits / rollback_to_revision / patch_probe

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Seed clone revision history so `rollback_to_revision` rehearses

**Files:**
- Modify: `services/action/adapters/sandbox.py` (add `_seed_revision_history_best_effort`; call it in `NamespaceCloneSandbox.rehearse` after the clone Deployment is created)
- Test: `tests/test_sandbox_adapter.py` (extend)

**Interfaces:**
- Consumes: `NamespaceCloneSandbox`, the k8s `AppsV1Api`, the existing `_strip_*` helper pattern in `sandbox.py`.
- Produces: `_seed_revision_history_best_effort(apps_v1, source_dep, sandbox_ns, clone_name)` (best-effort, never raises); called from `rehearse` right after `create_namespaced_deployment`.

**Read first:** `services/action/adapters/sandbox.py` — study `_strip_deployment`/`_strip_service`/`_strip_config_map` and how `_clone_service_best_effort` / `_clone_config_maps_best_effort` are written and called from `rehearse`. The new helper mirrors them exactly (best-effort, debug-log on failure, called from the same region of `rehearse`).

- [ ] **Step 1: Write the failing test**

Extend `tests/test_sandbox_adapter.py`. Reuse its `_situation()` / `_plan()` helpers and the monkeypatch-`_load_k8s` pattern from the existing fail-safe/teardown tests. Add a fake whose `list_namespaced_replica_set` returns RSes and whose `create_namespaced_replica_set` records calls, and assert seeding reads + re-creates them re-owned; and that a seeding failure is swallowed (best-effort) while `rehearse` still returns a `PreflightResult` (never raises).

```python
def test_seed_revision_history_copies_replicasets(monkeypatch):
    from services.action.adapters import sandbox as sb

    created_rs = []

    class _Apps:
        def read_namespaced_deployment(self, *a, **k):
            class _D:
                metadata = type("M", (), {"uid": "clone-uid", "name": "demo-app"})()
                spec = type("S", (), {"template": object()})()
            return _D()
        def create_namespaced_deployment(self, *a, **k):
            return None
        def list_namespaced_replica_set(self, namespace, **k):
            class _RS:
                metadata = type("M", (), {
                    "annotations": {"deployment.kubernetes.io/revision": "2"},
                    "resource_version": "1", "uid": "u", "creation_timestamp": "t",
                    "owner_references": None, "managed_fields": None, "self_link": None,
                    "namespace": "intelliops", "name": "demo-app-rs2",
                })()
                spec = type("S", (), {"template": object()})()
                status = object()
            return type("L", (), {"items": [_RS()]})()
        def create_namespaced_replica_set(self, namespace, body, **k):
            created_rs.append((namespace, body))
        # health-check path used by rehearse (rollout wait + post-fix)
        def read_namespaced_deployment_status(self, *a, **k):
            class _St:
                status = type("S", (), {"ready_replicas": 1, "replicas": 1})()
            return _St()

    class _Core:
        def __init__(self): self.deleted = []
        def create_namespace(self, *a, **k): return None
        def delete_namespace(self, name, *a, **k): self.deleted.append(name)

    apps, core = _Apps(), _Core()
    monkeypatch.setattr(sb, "_load_k8s", lambda: (apps, core), raising=False)
    monkeypatch.setattr(sb, "_strip_deployment", lambda dep, ns: dep, raising=False)
    monkeypatch.setattr(sb, "_namespace_body", lambda ns: object(), raising=False)
    monkeypatch.setattr(sb, "_referenced_config_map_names", lambda dep: [], raising=False)
    monkeypatch.setattr(sb.NamespaceCloneSandbox, "_clone_service_best_effort",
                        lambda self, c, d, n: None, raising=False)
    monkeypatch.setattr(sb.NamespaceCloneSandbox, "_clone_config_maps_best_effort",
                        lambda self, c, s, n: None, raising=False)

    # a rollback_to_revision plan triggers history seeding
    from common.contracts import RemediationPlan, RemediationTarget, RemediationStep
    plan = RemediationPlan(
        target=RemediationTarget(namespace="intelliops", deployment="demo-app"),
        steps=[RemediationStep(action="rollback_to_revision", revision=2)],
    )
    result = sb.NamespaceCloneSandbox("intelliops").rehearse(_situation(), plan)
    # the RS was read and re-created in the sandbox namespace
    assert len(created_rs) == 1
    assert result.mode == "k8s"          # completed without raising
    assert core.deleted                  # namespace torn down


def test_seed_revision_history_failure_is_swallowed(monkeypatch):
    from services.action.adapters import sandbox as sb
    from common.contracts import PreflightResult, RemediationPlan, RemediationTarget, RemediationStep

    class _Apps:
        def read_namespaced_deployment(self, *a, **k):
            class _D:
                metadata = type("M", (), {"uid": "clone-uid", "name": "demo-app"})()
                spec = type("S", (), {"template": object()})()
            return _D()
        def create_namespaced_deployment(self, *a, **k):
            return None
        def list_namespaced_replica_set(self, *a, **k):
            raise RuntimeError("history read boom")  # seeding-specific failure
        def create_namespaced_replica_set(self, *a, **k):
            return None
        def read_namespaced_deployment_status(self, *a, **k):
            class _St:
                status = type("S", (), {"ready_replicas": 1, "replicas": 1})()
            return _St()

    class _Core:
        def __init__(self): self.deleted = []
        def create_namespace(self, *a, **k): return None
        def delete_namespace(self, name, *a, **k): self.deleted.append(name)

    apps, core = _Apps(), _Core()
    monkeypatch.setattr(sb, "_load_k8s", lambda: (apps, core), raising=False)
    monkeypatch.setattr(sb, "_strip_deployment", lambda dep, ns: dep, raising=False)
    monkeypatch.setattr(sb, "_namespace_body", lambda ns: object(), raising=False)
    monkeypatch.setattr(sb, "_referenced_config_map_names", lambda dep: [], raising=False)
    monkeypatch.setattr(sb.NamespaceCloneSandbox, "_clone_service_best_effort",
                        lambda self, c, d, n: None, raising=False)
    monkeypatch.setattr(sb.NamespaceCloneSandbox, "_clone_config_maps_best_effort",
                        lambda self, c, s, n: None, raising=False)

    # a NON-rollback plan (patch_probe) must still rehearse fine despite the
    # history-read failure — seeding is best-effort.
    plan = RemediationPlan(
        target=RemediationTarget(namespace="intelliops", deployment="demo-app"),
        steps=[RemediationStep(action="patch_probe", probe="liveness", period_seconds=10)],
    )
    result = sb.NamespaceCloneSandbox("intelliops").rehearse(_situation(), plan)
    assert isinstance(result, PreflightResult)   # never raised
    assert result.mode == "k8s"
    assert core.deleted                          # torn down regardless
```

Both seeding tests reuse the `_situation()` helper already in the file. If the real `rehearse` reads the clone's uid via `read_namespaced_deployment(dep_name, sandbox_ns)` and the fake above returns a deployment for that call, the seeding path exercises fully; if your implementation derives the owner ref differently, adapt the fake to match but keep the two assertions (RS created / seeding-failure-swallowed).

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_sandbox_adapter.py -k seed_revision -v`
Expected: FAIL — no seeding yet, so `created_rs` is empty.

- [ ] **Step 3: Implement `_seed_revision_history_best_effort` + call it**

Add a module-level `_strip_replica_set(rs, sandbox_ns, clone_uid, clone_name)` mirroring `_strip_deployment` (strip resourceVersion/uid/creationTimestamp/status/managedFields/selfLink, set namespace to `sandbox_ns`, and set `owner_references` to a single ref pointing at the clone Deployment — `kind=Deployment`, `name=clone_name`, `uid=clone_uid`, `controller=True`). Then:

```python
def _seed_revision_history_best_effort(apps_v1, source_dep, sandbox_ns, clone_name, clone_uid):
    """Copy the source Deployment's owned ReplicaSets (with their revision
    annotations) into sandbox_ns, re-owned by the clone Deployment, so a
    `rollback_to_revision` step finds the revision on the clone. Best-effort:
    a failure here just means rollback_to_revision can't rehearse — logged, not
    raised (a non-rollback plan doesn't need history)."""
    try:
        rs_list = apps_v1.list_namespaced_replica_set(source_dep.metadata.namespace
                                                      if source_dep.metadata else None)
    except Exception as exc:  # noqa: BLE001
        logger.debug("revision-history read skipped: %s", exc)
        return
    for rs in getattr(rs_list, "items", []) or []:
        try:
            stripped = _strip_replica_set(rs, sandbox_ns, clone_uid, clone_name)
            apps_v1.create_namespaced_replica_set(namespace=sandbox_ns, body=stripped)
        except Exception as exc:  # noqa: BLE001
            logger.debug("revision-history seed skipped for one RS: %s", exc)
```

Call it in `rehearse` right after the clone Deployment is created (near the existing `_clone_service_best_effort` / `_clone_config_maps_best_effort` calls). You'll need the clone's uid/name — read them from the created clone Deployment (or from `source_dep` since the clone keeps the same name; for uid, read the just-created clone via `read_namespaced_deployment(dep_name, sandbox_ns)` if the fake/real returns one, else pass `source_dep.metadata.namespace`-derived values). Keep it defensive — if uid isn't available, still create the RS with the revision annotation (the owner re-point is a nicety; the revision annotation is what `rollback_to_revision` matches on).

**Important:** call seeding only matters for a rollback plan, but calling it unconditionally is fine (best-effort, cheap) — do NOT gate it on inspecting the plan unless you want to; simplest is to always seed. Either way, seeding failure must never fail `rehearse`.

Confirm the source namespace used for the RS read is the PRODUCTION namespace (`self._namespace`), not `sandbox_ns` (you're copying FROM prod history INTO the sandbox).

- [ ] **Step 4: Run the seeding tests**

Run: `uv run pytest tests/test_sandbox_adapter.py -v`
Expected: PASS (existing fail-safe/teardown + 2 new seeding).

- [ ] **Step 5: (Optional) formatting polish + full gates**

Optionally enrich `_format_steps` in `remediate.py` for the new actions (e.g. `patch_resource_limits cpu={cpu_limit} mem={mem_limit}`), following its existing style. Then:

Run: `uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check . && npm --prefix frontend run build`
Expected: all green + clean.

- [ ] **Step 6: Commit**

```bash
git add services/action/adapters/sandbox.py tests/test_sandbox_adapter.py services/action/remediate.py
git commit -m "feat(sandbox): seed clone revision history so rollback_to_revision rehearses

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Docs + final gates

**Files:**
- Modify: `deploy/k8s/README.md`
- Commit the spec + this plan (currently untracked in the main checkout) onto the branch.

- [ ] **Step 1: Document the tier-2 actions**

In `deploy/k8s/README.md`, extend the "what each playbook does" material (near the existing restart/scale/rollback descriptions) with the three new actions:
- `patch_resource_limits` — retunes container CPU/mem ceilings (targeted patch).
- `rollback_to_revision` — rolls the Deployment's pod template back to a specific prior revision (distinct from `rollback_deploy`, which is a rollout-restart). Note the sandbox seeds the clone's revision history so this rehearses.
- `patch_probe` — adjusts a liveness/readiness probe's timing (the common fix for an over-aggressive probe killing a slow-starting pod).

Add to the honest-limits material:
- The **permanently-excluded** set: `delete`, `exec`, scale-to-zero, secret access, cluster-scoped mutations (tier-3, never added).
- The **deferred** actions: node (`cordon_node`/`uncordon_node`) and HPA (`patch_hpa`) — they can't be honestly sandbox-rehearsed (a node can't be cloned; an HPA only partially rehearses), so they get their own PR.
- The **denylist gate**: runs before the sandbox, refuses dangerous step shapes (`denied:unsafe-scale` / `denied:unsafe-limits` / `denied:unsafe-probe`).
- The **history-seeding honest limit**: seeding copies ReplicaSet *specs* (templates + revision annotations), not the historical pods' runtime state — sufficient because pod-readiness of the rolled-back clone is the pass signal (consistent with PR A).

Reference `docs/sandbox-and-ai-runbooks-design-note.md`. Match the README's existing honest, specific tone.

- [ ] **Step 2: Final full gates**

Run: `uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check . && npm --prefix frontend run build`
Expected: all green + clean.

- [ ] **Step 3: Commit**

```bash
git add deploy/k8s/README.md docs/superpowers/specs/2026-09-04-tier2-vocab-denylist-design.md docs/superpowers/plans/2026-09-04-tier2-vocab-denylist.md
git commit -m "docs(vocab): tier-2 actions, denylist, history-seeding limit; spec + plan

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review Notes (author)

- **Spec coverage:** §1 contract → Task 1; §2 denylist → Task 2; §3 dispatch → Task 3; §4 sandbox history-seeding → Task 4; §5 formatting → Task 4 (optional); docs (AC8) → Task 5. Acceptance criteria 1–8 all mapped.
- **Safety invariant provable at each step:** Task 1's `test_out_of_set_action_still_rejected` proves the Literal stays closed; Task 2 proves the denylist fails closed AND precedes the sandbox; Task 3 proves each action is a typed AppsV1 call that never raises out; Task 4 proves the sandbox rehearses the new actions (incl. seeded history for rollback).
- **Type consistency:** the new `RemediationStep` fields (`cpu_limit`/`mem_limit`/`container`/`revision`/`probe`/`initial_delay_seconds`/`period_seconds`/`timeout_seconds`/`failure_threshold`) are referenced identically in Task 1 (defined), Task 2 (`_denylist_reason` reads them), Task 3 (`_dispatch` reads them). No drift.
- **Ordering:** denylist gate is after RBAC, before plan-build+sandbox (Task 2 asserts `sandbox.rehearsed is False` on a denied step) — the load-bearing safety ordering.
- **Additive default path:** every new field defaults None; existing actions/tests unchanged; Task 1 Step 5 + Task 2 Step 5 assert the base suite stays green with no reason-string regressions.
- **Known soft spot (flag for the executor):** the `_denylist_reason` scale rule can't know current replicas, so it refuses on a large negative *delta* heuristic (-10+). This is a coarse guard, deliberately — the k8s dispatch already clamps to >=1, so the denylist's job is to refuse a playbook that *intends* a take-down, not to compute the exact resulting replica count. Documented in the code comment. If the executor finds the heuristic awkward, keep the test contract (-50 refused, -2 allowed) and adjust the constant.
