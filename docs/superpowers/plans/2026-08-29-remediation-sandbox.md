# Remediation Sandbox (pre-flight rehearsal) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pre-flight sandbox rehearsal that clones a target Deployment into a throwaway namespace, applies the same remediation steps, watches real pod health, tears the clone down, and surfaces the pass/fail verdict to the approving human and the incident timeline — before the fix touches production.

**Architecture:** A new `Sandbox` protocol with two config-switched adapters (`NullSandbox` off-default, `NamespaceCloneSandbox` for k8s), a pre-flight gate inserted into `execute_remediation` *before* the HITL approval wait (block auto on failure / advise human on HITL), and additive `preflight` fields on `RemediationOutcome` + `ApprovalRequest` that flow through the read projection to the console — following the shipped `mode`/`steps` additive precedent.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI, `kubernetes` client, pytest, React/TypeScript (Vite).

**Spec:** `docs/superpowers/specs/2026-08-29-remediation-sandbox-design.md` (read it alongside this plan). Companion rationale: `docs/sandbox-and-ai-runbooks-design-note.md`.

## Global Constraints

- **Gates:** `uv run pytest -m "not postgres and not kafka"` green (~433 existing + new tests); `ruff check .` and `ruff format --check .` clean; `npm --prefix frontend run build` clean.
- **Test-safe default:** `sandbox_mode` defaults to `"off"`; `NullSandbox` passes through; the base compose + the existing suite + CI must be byte-identical on the default path (no new behavior when off). The real sandbox is opt-in via the k8s overlay, exactly like `REMEDIATOR_MODE=k8s`.
- **Fail-safe:** the sandbox never raises out of `execute_remediation` — any error becomes `PreflightResult(passed=False, ...)`, mirroring the never-raise pattern in `k8s_remediator.py` / `k8s_health.py`.
- **Additive contracts:** new fields are optional and default `None`; the projection reads them via `getattr`; frontend fields are optional. Never reorder or retype existing contract fields.
- **No fabricated data:** the UI shows the real sandbox verdict or an honest "not rehearsed" when `off`.
- **No change to the remediation LOGIC or the k8s action set** — that is PR B. This PR adds a rehearsal step and surfaces its result. The 4-action vocabulary (`restart`/`scale`/`rollback_deploy`/`wait`) is unchanged.
- **Commit trailer** on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **git:** branch `feat/remediation-sandbox` (off master). Push; open a PR; the USER merges. Never merge to master.

---

## File Structure

- `common/contracts.py` — add `PreflightResult`; add optional `preflight` field to `RemediationOutcome` and `ApprovalRequest`.
- `common/interfaces.py` — add the `Sandbox` protocol.
- `common/config.py` — add `sandbox_mode: str = "off"`.
- `services/action/adapters/sandbox.py` (new) — `NullSandbox` + `NamespaceCloneSandbox`.
- `services/action/remediate.py` — add the `sandbox` param + the pre-flight gate (reorder plan-build before Gate 3; block-auto/advise-hitl; thread `preflight` onto outcomes + the approval request).
- `services/action/consumer.py` — add the `sandbox` positional param, pass it through.
- `services/action/app.py` — add `_make_sandbox(settings)` + thread it into `run_consumer`.
- `services/read/projection.py` — add `preflight` to the `ReadModel.apply_outcome` drill-down `"outcome"` dict (`:176-183`) via `getattr`.
- `frontend/src/data/types.ts` — add optional `preflight` on `SituationOutcome` (`:41-46`).
- `frontend/src/views/Incidents.tsx` — render the pre-flight row after the steps row (`:384`); add `preflight` to the inline mock-mode drill-down outcome (`:158`).
- `deploy/k8s/README.md` — document the live sandbox flow.
- Tests (the `tests/` dir is FLAT — there is no `tests/action/` or `tests/read/`): `tests/test_remediate_sandbox.py` (new, gate cases), `tests/test_sandbox_adapter.py` (new, NullSandbox + NamespaceCloneSandbox fail-safe), `tests/test_read_projection.py` (new — `ReadModel.apply_outcome` has NO existing test to extend). The existing `run_consumer` callers that break on the signature change are `tests/test_slice3_acceptance.py:122` and `:158`.

---

## Task 1: Contract + protocol + config + NullSandbox

**Files:**
- Modify: `common/contracts.py` (add `PreflightResult` after `RemediationTarget`/`RemediationPlan`; add `preflight` to `ApprovalRequest` and `RemediationOutcome`)
- Modify: `common/interfaces.py` (add `Sandbox` protocol)
- Modify: `common/config.py` (add `sandbox_mode`)
- Create: `services/action/adapters/sandbox.py` (`NullSandbox` only in this task)
- Test: `tests/test_sandbox_adapter.py` (NullSandbox portion)

**Interfaces:**
- Produces:
  - `common.contracts.PreflightResult(passed: bool, detail: str, mode: str, sandbox_namespace: str | None = None)`
  - `RemediationOutcome.preflight: PreflightResult | None = None`
  - `ApprovalRequest.preflight: PreflightResult | None = None`
  - `common.interfaces.Sandbox` protocol with `rehearse(self, situation: Situation, plan: RemediationPlan) -> PreflightResult`
  - `services.action.adapters.sandbox.NullSandbox` with `rehearse(...) -> PreflightResult(passed=True, detail="not rehearsed (sandbox off)", mode="off")`
  - `Settings.sandbox_mode: str = "off"`

- [ ] **Step 1: Write the failing test**

Create `tests/test_sandbox_adapter.py`:

```python
from datetime import UTC, datetime

from common.contracts import (
    PreflightResult,
    RemediationOutcome,
    RemediationPlan,
    RemediationResult,
    RemediationTarget,
    Situation,
    SituationStatus,
)
from services.action.adapters.sandbox import NullSandbox


def _situation() -> Situation:
    now = datetime.now(UTC)
    return Situation(
        id="sit-1",
        status=SituationStatus.DIAGNOSED,
        severity="high",
        first_seen=now,
        last_seen=now,
        signature="sig-1",
    )


def _plan() -> RemediationPlan:
    return RemediationPlan(target=RemediationTarget(namespace="intelliops", deployment="demo-app"))


def test_null_sandbox_passes_through():
    result = NullSandbox().rehearse(_situation(), _plan())
    assert isinstance(result, PreflightResult)
    assert result.passed is True
    assert result.mode == "off"
    assert result.sandbox_namespace is None


def test_preflight_is_additive_and_optional():
    # Existing constructions must still work with no preflight supplied.
    outcome = RemediationOutcome(
        situation_id="sit-1",
        playbook_id="pb-1",
        result=RemediationResult.SUCCESS,
        health_after="healthy",
        ts=datetime.now(UTC),
    )
    assert outcome.preflight is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sandbox_adapter.py -v`
Expected: FAIL — `ImportError` (no `PreflightResult`, no `services.action.adapters.sandbox`).

- [ ] **Step 3: Add `PreflightResult` + the two optional fields in `common/contracts.py`**

Add this class immediately after `RemediationPlan` (around line 94):

```python
class PreflightResult(BaseModel):
    passed: bool
    detail: str  # e.g. "sandbox: pod healthy in 8s" / "not rehearsed (sandbox off)"
    mode: str  # "off" | "k8s"
    sandbox_namespace: str | None = None  # the throwaway ns, for audit
```

Then add the optional field to `ApprovalRequest` (after `decided_by`, line 112):

```python
    preflight: PreflightResult | None = None
```

And to `RemediationOutcome` (after `mode`, line 123):

```python
    preflight: PreflightResult | None = None
```

`PreflightResult` is defined before both models, so no forward-ref is needed.

- [ ] **Step 4: Add the `Sandbox` protocol in `common/interfaces.py`**

Match the existing protocol style in that file (`@runtime_checkable`, `Protocol`). Import `Situation`, `RemediationPlan`, and `PreflightResult` from `common.contracts` if not already imported. Add:

```python
@runtime_checkable
class Sandbox(Protocol):
    """Rehearses a remediation plan on an isolated copy and reports a verdict."""

    def rehearse(self, situation: Situation, plan: RemediationPlan) -> PreflightResult: ...
```

- [ ] **Step 5: Add `sandbox_mode` to `common/config.py`**

Add next to `remediator_mode` / `health_check_mode` on the settings class:

```python
    sandbox_mode: str = "off"  # "off" | "k8s"
```

- [ ] **Step 6: Create `services/action/adapters/sandbox.py` with `NullSandbox`**

```python
"""Sandbox adapters: rehearse a remediation plan on an isolated copy.

NullSandbox is the config-switched, test-safe default (sandbox_mode="off"):
it passes through so the base demo and the existing suite are unchanged. The
real NamespaceCloneSandbox (sandbox_mode="k8s") is added in a later task."""

from __future__ import annotations

from common.contracts import PreflightResult, RemediationPlan, Situation


class NullSandbox:
    """No-op sandbox. Rehearses nothing; reports an honest 'not rehearsed'."""

    def rehearse(self, situation: Situation, plan: RemediationPlan) -> PreflightResult:
        return PreflightResult(
            passed=True,
            detail="not rehearsed (sandbox off)",
            mode="off",
        )
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_sandbox_adapter.py -v`
Expected: PASS (both tests).

- [ ] **Step 8: Run the full suite + lint to confirm the default path is unchanged**

Run: `uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check .`
Expected: PASS — the ~433 existing tests still green (nothing reads `preflight` or `sandbox_mode` yet), lint clean.

- [ ] **Step 9: Commit**

```bash
git add common/contracts.py common/interfaces.py common/config.py services/action/adapters/sandbox.py tests/test_sandbox_adapter.py
git commit -m "feat(sandbox): PreflightResult contract, Sandbox protocol, sandbox_mode config, NullSandbox

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: The pre-flight gate + consumer + app wiring

**Files:**
- Modify: `services/action/remediate.py:75-146` (add `sandbox` param; move plan-build before Gate 3; insert the rehearsal; block-auto/advise-hitl; thread `preflight`)
- Modify: `services/action/consumer.py:19-51` (add `sandbox` positional param after `health`; pass into `execute_remediation`)
- Modify: `services/action/app.py:39-88` (add `_make_sandbox`; thread into `run_consumer` args)
- Modify: `tests/test_slice3_acceptance.py:122` and `:158` (the two existing `run_consumer` callers — insert a `NullSandbox()` in the new `sandbox` position)
- Test: `tests/test_remediate_sandbox.py` (new)

**Interfaces:**
- Consumes: `PreflightResult`, `NullSandbox`, `Sandbox` (Task 1).
- Produces:
  - `execute_remediation(situation, playbook, gate, remediator, health, sandbox, timeout_seconds, poll_interval_seconds)` — **`sandbox` is inserted after `health`, before `timeout_seconds`**.
  - `run_consumer(bus, store, gate, remediator, health, sandbox, timeout_seconds, poll_interval_seconds, stop_event)` — **`sandbox` after `health`**.
  - `services.action.app._make_sandbox(settings) -> Sandbox`.

**Ruling on signature ordering:** `sandbox` goes positionally after `health` in both `execute_remediation` and `run_consumer` (it is the object rehearsing before health-check, so it reads naturally beside `remediator`/`health`). All call sites are updated in this task; there are no external callers. Costs nothing if wrong — it is internal wiring — but keep it consistent across all three files.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_remediate_sandbox.py`. These use lightweight stubs (no real k8s). `tests/` is flat; there is no shared action-stub helper — define stubs inline as below.

```python
from datetime import UTC, datetime

from common.contracts import (
    ApprovalRequest,
    HitlMode,
    Playbook,
    PreflightResult,
    RemediationOutcome,
    RemediationResult,
    RemediationStep,
    Situation,
    SituationStatus,
)
from services.action.remediate import execute_remediation


def _situation() -> Situation:
    now = datetime.now(UTC)
    return Situation(
        id="sit-1",
        status=SituationStatus.DIAGNOSED,
        severity="high",
        first_seen=now,
        last_seen=now,
        signature="sig-1",
    )


def _playbook(hitl: HitlMode) -> Playbook:
    return Playbook(
        id="pb-1",
        name="restart demo-app",
        match_rule="*",
        steps=[RemediationStep(action="restart")],
        hitl_mode=hitl,
        reversible=True,
    )


class _Gate:
    """Records audit decisions; approves HITL; captures the approval request."""

    def __init__(self, approve: bool = True):
        self._approve = approve
        self.audits: list[str] = []
        self.approval_request: ApprovalRequest | None = None

    def write_audit(self, record):
        self.audits.append(record.decision)

    def check_rbac(self, actor, action, resource):
        return True

    def request_approval(self, request: ApprovalRequest) -> ApprovalRequest:
        self.approval_request = request
        return request

    def await_decision(self, request_id, timeout):
        status = "approved" if self._approve else "rejected"
        return ApprovalRequest(
            id=request_id,
            situation_id="sit-1",
            playbook_id="pb-1",
            requested_by="action-service",
            status=status,
        )


class _Remediator:
    def __init__(self):
        self.executed = False

    def execute(self, plan):
        self.executed = True
        return True

    def rollback(self, plan):
        return True


class _Health:
    def check(self, situation, target):
        return True


class _StubSandbox:
    """Returns a fixed verdict; records the plan it was handed."""

    def __init__(self, passed: bool):
        self._passed = passed
        self.rehearsed_plan = None

    def rehearse(self, situation, plan) -> PreflightResult:
        self.rehearsed_plan = plan
        return PreflightResult(
            passed=self._passed,
            detail="sandbox: pod healthy" if self._passed else "sandbox: clone crashlooped",
            mode="k8s",
            sandbox_namespace="intelliops-sandbox-deadbeef",
        )


def test_auto_blocks_when_sandbox_fails():
    gate, remediator, health = _Gate(), _Remediator(), _Health()
    sandbox = _StubSandbox(passed=False)
    outcome = execute_remediation(
        _situation(), _playbook(HitlMode.AUTO), gate, remediator, health, sandbox, 1.0, 0.01
    )
    assert isinstance(outcome, RemediationOutcome)
    assert outcome.health_after == "preflight-failed"
    assert outcome.result == RemediationResult.FAILURE
    assert remediator.executed is False  # blocked — never touched the live target
    assert outcome.preflight is not None and outcome.preflight.passed is False


def test_hitl_proceeds_with_verdict_attached_when_sandbox_fails():
    gate, remediator, health = _Gate(approve=True), _Remediator(), _Health()
    sandbox = _StubSandbox(passed=False)
    outcome = execute_remediation(
        _situation(), _playbook(HitlMode.HITL), gate, remediator, health, sandbox, 1.0, 0.01
    )
    # HITL advises, not blocks: the human approved, so it executed.
    assert gate.approval_request is not None
    assert gate.approval_request.preflight is not None
    assert gate.approval_request.preflight.passed is False
    assert remediator.executed is True
    assert outcome.preflight is not None


def test_sandbox_pass_flows_to_outcome():
    gate, remediator, health = _Gate(), _Remediator(), _Health()
    sandbox = _StubSandbox(passed=True)
    outcome = execute_remediation(
        _situation(), _playbook(HitlMode.AUTO), gate, remediator, health, sandbox, 1.0, 0.01
    )
    assert remediator.executed is True
    assert outcome.result == RemediationResult.SUCCESS
    assert outcome.preflight is not None and outcome.preflight.passed is True
    assert sandbox.rehearsed_plan is not None  # the sandbox got the built plan
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_remediate_sandbox.py -v`
Expected: FAIL — `execute_remediation` does not yet accept a `sandbox` argument (TypeError).

- [ ] **Step 3: Add the `preflight` kwarg to the `_outcome` helper in `remediate.py`**

Change `_outcome` (lines 29-46) to thread `preflight`:

```python
def _outcome(
    situation: Situation,
    playbook: Playbook,
    result: RemediationResult,
    health_after: str,
    steps: list[str] | None = None,
    mode: str = "dry_run",
    preflight: PreflightResult | None = None,
) -> RemediationOutcome:
    return RemediationOutcome(
        situation_id=situation.id,
        playbook_id=playbook.id,
        result=result,
        health_after=health_after,
        ts=datetime.now(UTC),
        hitl_mode=playbook.hitl_mode,
        steps=steps or [],
        mode=mode,
        preflight=preflight,
    )
```

Add `PreflightResult` to the `common.contracts` import block at the top of the file.

- [ ] **Step 4: Rewrite `execute_remediation` to add the sandbox param and the pre-flight gate**

Replace the signature and body from line 75 onward. The changes: add `sandbox` after `health`; **move the `resolve_target` + `RemediationPlan` build + `_format_steps` + `mode` up to before Gate 3**; insert the rehearsal; block auto on failure; attach `preflight` to the `ApprovalRequest` and to every terminal `_outcome`.

```python
def execute_remediation(
    situation: Situation,
    playbook: Playbook,
    gate,
    remediator,
    health,
    sandbox,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> RemediationOutcome:
    # Gate 0: disabled playbooks never run.
    if playbook.hitl_mode == HitlMode.DISABLED:
        _audit(gate, situation, playbook, "skipped")
        return _outcome(situation, playbook, RemediationResult.FAILURE, "skipped:disabled")

    # Gate 1: reversible-only (ADR-007) — a non-reversible playbook is refused.
    if not playbook.reversible:
        _audit(gate, situation, playbook, "refused")
        return _outcome(situation, playbook, RemediationResult.FAILURE, "refused:not-reversible")

    # Gate 2: RBAC, fail closed (ADR-003).
    if not gate.check_rbac(_ACTOR, "execute", f"playbook:{playbook.id}"):
        _audit(gate, situation, playbook, "deny")
        return _outcome(situation, playbook, RemediationResult.FAILURE, "denied:rbac")

    # Resolve the target once and build a typed plan (needed by the sandbox below).
    target = resolve_target(situation, get_settings().k8s_namespace)
    plan = RemediationPlan(
        target=target, steps=playbook.steps, rollback_steps=playbook.rollback_steps
    )
    steps = _format_steps(plan)
    mode = get_settings().remediator_mode

    # Pre-flight rehearsal: try the fix on an isolated clone before the human
    # approves (and before an auto playbook executes). Fail-safe — the sandbox
    # never raises; a failure is a PreflightResult(passed=False).
    preflight = sandbox.rehearse(situation, plan)
    if not preflight.passed and playbook.hitl_mode == HitlMode.AUTO:
        # Auto has no human to advise — block.
        _audit(gate, situation, playbook, "preflight-failed")
        return _outcome(
            situation,
            playbook,
            RemediationResult.FAILURE,
            "preflight-failed",
            steps=steps,
            mode=mode,
            preflight=preflight,
        )

    # Gate 3: HITL — wait for an explicit human approval (ADR-008). The human
    # sees the pre-flight verdict on the request.
    if playbook.hitl_mode == HitlMode.HITL:
        request = gate.request_approval(
            ApprovalRequest(
                id=f"appr-{situation.id}",
                situation_id=situation.id,
                playbook_id=playbook.id,
                requested_by=_ACTOR,
                preflight=preflight,
            )
        )
        decided = gate.await_decision(request.id, timeout_seconds)
        if decided.status != "approved":
            reason = "aborted:rejected" if decided.status == "rejected" else "aborted:timeout"
            _audit(gate, situation, playbook, "abort")
            return _outcome(
                situation, playbook, RemediationResult.FAILURE, reason, preflight=preflight
            )

    # Execute.
    if not remediator.execute(plan):
        _audit(gate, situation, playbook, "execute-failed")
        return _outcome(
            situation,
            playbook,
            RemediationResult.FAILURE,
            "execute-failed",
            steps=steps,
            mode=mode,
            preflight=preflight,
        )

    # Verify health; roll back if unhealthy.
    if health.check(situation, target):
        _audit(gate, situation, playbook, "allow")
        return _outcome(
            situation,
            playbook,
            RemediationResult.SUCCESS,
            "healthy",
            steps=steps,
            mode=mode,
            preflight=preflight,
        )

    remediator.rollback(plan)
    _audit(gate, situation, playbook, "rolled-back")
    return _outcome(
        situation,
        playbook,
        RemediationResult.ROLLED_BACK,
        "unhealthy:rolled-back",
        steps=steps,
        mode=mode,
        preflight=preflight,
    )
```

Note the ordering change vs. the original: `target`/`plan`/`steps`/`mode` are now computed *before* Gate 3 (they used to be after the HITL wait). Behavior of every existing gate and its reason string is unchanged; the HITL approval-reject/timeout path now also carries `preflight` (harmless additive).

- [ ] **Step 5: Thread `sandbox` through `run_consumer` in `consumer.py`**

Add `sandbox` to the signature (after `health`) and pass it into `execute_remediation`:

```python
def run_consumer(
    bus,
    store,
    gate,
    remediator,
    health,
    sandbox,
    timeout_seconds: float,
    poll_interval_seconds: float,
    stop_event: threading.Event,
) -> None:
```

And in the `else` branch call:

```python
            outcome = execute_remediation(
                situation,
                playbook,
                gate,
                remediator,
                health,
                sandbox,
                timeout_seconds,
                poll_interval_seconds,
            )
```

- [ ] **Step 6: Add `_make_sandbox` + wire it in `app.py`**

Add the factory next to `_make_remediator` (import `NullSandbox` from `services.action.adapters.sandbox`; the k8s branch is added in Task 3 — for now default to `NullSandbox` and leave a TODO for the k8s branch, OR add the k8s import guarded so this task stays green without `NamespaceCloneSandbox` existing yet):

```python
def _make_sandbox(settings):
    if settings.sandbox_mode == "k8s":
        from services.action.adapters.sandbox import NamespaceCloneSandbox

        return NamespaceCloneSandbox(
            settings.k8s_namespace, prometheus_url=settings.prometheus_url
        )
    from services.action.adapters.sandbox import NullSandbox

    return NullSandbox()
```

The `NamespaceCloneSandbox` import is inside the `k8s` branch so this task does not require it to exist yet (Task 3 adds it). Thread it into the `run_consumer` args tuple (insert after `_make_health_checker(settings)`):

```python
        args=(
            app.state.bus,
            store,
            gate,
            _make_remediator(settings),
            _make_health_checker(settings),
            _make_sandbox(settings),
            settings.hitl_poll_timeout_seconds,
            settings.hitl_poll_interval_seconds,
            stop_event,
        ),
```

- [ ] **Step 7: Run the new gate tests**

Run: `uv run pytest tests/test_remediate_sandbox.py -v`
Expected: PASS (all three cases).

- [ ] **Step 8: Fix the two existing `run_consumer` call sites in `tests/test_slice3_acceptance.py`**

The signature changed, so the two callers at `tests/test_slice3_acceptance.py:122` and `:158` break. Both currently read:

```python
    run_consumer(
        bus,
        _store(),
        gate,
        remediator,
        FixedHealthChecker(True),   # (False) at the :158 call site
        timeout_seconds=3.0,
        poll_interval_seconds=0.01,
        stop_event=threading.Event(),
    )
```

Insert a `NullSandbox()` positionally after the `FixedHealthChecker(...)` line (the new `sandbox` slot sits between `health` and the keyworded `timeout_seconds`), in BOTH calls:

```python
    run_consumer(
        bus,
        _store(),
        gate,
        remediator,
        FixedHealthChecker(True),
        NullSandbox(),
        timeout_seconds=3.0,
        poll_interval_seconds=0.01,
        stop_event=threading.Event(),
    )
```

Add the import at the top of `tests/test_slice3_acceptance.py`:

```python
from services.action.adapters.sandbox import NullSandbox
```

These two tests must still pass unchanged in behavior: `NullSandbox` passes through (`passed=True`), so the HITL flow proceeds to approval and executes exactly as before. Then run the full suite:

Run: `uv run pytest -m "not postgres and not kafka" -q`
Expected: PASS. If `grep -rn "execute_remediation\|run_consumer" tests/` surfaces any OTHER caller, fix it the same way (there should be none beyond these two — `execute_remediation` is called only inside `consumer.py`).

- [ ] **Step 9: Lint**

Run: `ruff check . && ruff format --check .`
Expected: clean.

- [ ] **Step 10: Commit**

```bash
git add services/action/remediate.py services/action/consumer.py services/action/app.py tests/test_remediate_sandbox.py tests/test_slice3_acceptance.py
git commit -m "feat(sandbox): pre-flight gate — rehearse before approval, block auto / advise hitl

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: NamespaceCloneSandbox (the real k8s rehearsal)

**Files:**
- Modify: `services/action/adapters/sandbox.py` (add `NamespaceCloneSandbox`)
- Read for reuse: `services/action/adapters/k8s_remediator.py` (`_dispatch` / `KubernetesRemediator`), `services/action/adapters/k8s_health.py` (`KubernetesHealthChecker`)
- Test: `tests/test_sandbox_adapter.py` (add the fail-safe + teardown cases with a fake k8s client)

**Interfaces:**
- Consumes: `RemediationPlan`, `PreflightResult`, `Situation`, `KubernetesRemediator`, `KubernetesHealthChecker`.
- Produces: `services.action.adapters.sandbox.NamespaceCloneSandbox(namespace: str, prometheus_url: str | None = None)` implementing `rehearse(situation, plan) -> PreflightResult`.

**Ruling on scope:** the metric-recovery signal for a clone is subtle (the demo's `cpu_usage` series is per-metric-name, not per-namespace), so per the spec **pod-readiness of the clone is the primary pass signal for PR A; the metric predicate is best-effort**. Do not block PR A on perfect metric wiring — document the limitation. Costs: a clone could read "healthy" on pod-ready alone when the metric hasn't recovered; acceptable for a rehearsal whose verdict is advisory to a human. PR B/later sharpens it.

- [ ] **Step 1: Write the failing tests (fake k8s client)**

Add to `tests/test_sandbox_adapter.py`. The fake models just enough of the `kubernetes` client surface `NamespaceCloneSandbox` touches; make one variant raise mid-clone to prove fail-safety + teardown.

```python
class _FakeApiRaises:
    """Every read/create raises — proves the sandbox never propagates."""

    def __getattr__(self, name):
        def _boom(*a, **k):
            raise RuntimeError("k8s down")

        return _boom


def test_namespace_clone_sandbox_is_fail_safe(monkeypatch):
    from services.action.adapters import sandbox as sb

    # Force the adapter's k8s client construction to yield a raising fake.
    monkeypatch.setattr(sb, "_load_k8s", lambda: (_FakeApiRaises(), _FakeApiRaises()), raising=False)
    s = sb.NamespaceCloneSandbox("intelliops")
    result = s.rehearse(_situation(), _plan())
    assert result.passed is False
    assert result.mode == "k8s"
    assert "error" in result.detail.lower()
```

The exact monkeypatch target depends on how the adapter loads the client (see Step 2 — factor client creation into a `_load_k8s()` helper so the test can stub it).

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_sandbox_adapter.py::test_namespace_clone_sandbox_is_fail_safe -v`
Expected: FAIL — no `NamespaceCloneSandbox`.

- [ ] **Step 3: Implement `NamespaceCloneSandbox`**

Read `services/action/adapters/k8s_remediator.py` and `k8s_health.py` first to reuse their client-loading and dispatch/health logic. Add to `services/action/adapters/sandbox.py`. Key requirements from the spec §3:

1. `sandbox_ns = f"intelliops-sandbox-{uuid4().hex[:8]}"`.
2. Read the target Deployment (+ Service + referenced ConfigMap) via `AppsV1Api`/`CoreV1Api`; deep-copy specs into `sandbox_ns`, stripping cluster-assigned fields (`resourceVersion`, `uid`, `status`, `clusterIP`, `nodePort`); keep labels (so the existing Prometheus scrape/relabel discovers the clone) and preserve resource requests/limits.
3. Create the namespace + cloned objects; wait for the clone's initial rollout (bounded timeout).
4. Apply the SAME `plan.steps` to the clone — construct a `KubernetesRemediator(sandbox_ns)` and call `.execute(clone_plan)` where `clone_plan` is the plan retargeted at `sandbox_ns`/clone-deployment.
5. `KubernetesHealthChecker.check(situation, clone_target)` (bounded poll) — pod-ready + best-effort metric predicate.
6. `passed = health result`; build `PreflightResult(passed, detail, mode="k8s", sandbox_namespace=sandbox_ns)`.
7. **Always** delete `sandbox_ns` in a `finally` (best-effort; teardown failure logged, not raised).
8. **Any exception anywhere** → `PreflightResult(passed=False, detail=f"sandbox error: {exc.__class__.__name__}", mode="k8s", sandbox_namespace=sandbox_ns)` + attempt teardown. Never raises.

Structure the client construction behind a module-level `_load_k8s()` returning `(apps_v1, core_v1)` so the test can monkeypatch it. Wrap the entire `rehearse` body in `try/except Exception` with the `finally` teardown. Follow the never-raise idiom already in `k8s_remediator.py`.

```python
from __future__ import annotations

import logging
from uuid import uuid4

from common.contracts import PreflightResult, RemediationPlan, RemediationStep, RemediationTarget, Situation

logger = logging.getLogger(__name__)


def _load_k8s():
    from kubernetes import client, config  # imported lazily so off-mode never needs k8s

    try:
        config.load_incluster_config()
    except Exception:  # noqa: BLE001
        config.load_kube_config()
    return client.AppsV1Api(), client.CoreV1Api()


class NamespaceCloneSandbox:
    def __init__(self, namespace: str, prometheus_url: str | None = None):
        self._namespace = namespace
        self._prometheus_url = prometheus_url

    def rehearse(self, situation: Situation, plan: RemediationPlan) -> PreflightResult:
        sandbox_ns = f"intelliops-sandbox-{uuid4().hex[:8]}"
        apps_v1 = core_v1 = None
        try:
            apps_v1, core_v1 = _load_k8s()
            # ... clone Deployment/Service/ConfigMap into sandbox_ns (strip
            #     cluster-assigned fields, keep labels + resources) ...
            # ... wait for initial rollout (bounded) ...
            # ... apply plan.steps to the clone via KubernetesRemediator(sandbox_ns) ...
            # ... health = KubernetesHealthChecker(...).check(situation, clone_target) ...
            passed = bool(health)
            detail = "sandbox: clone healthy" if passed else "sandbox: clone unhealthy"
            return PreflightResult(
                passed=passed, detail=detail, mode="k8s", sandbox_namespace=sandbox_ns
            )
        except Exception as exc:  # noqa: BLE001 — fail-safe, never propagate
            logger.warning("sandbox rehearsal failed: %s", exc)
            return PreflightResult(
                passed=False,
                detail=f"sandbox error: {exc.__class__.__name__}",
                mode="k8s",
                sandbox_namespace=sandbox_ns,
            )
        finally:
            if core_v1 is not None:
                try:
                    core_v1.delete_namespace(name=sandbox_ns)
                except Exception as exc:  # noqa: BLE001 — best-effort teardown
                    logger.warning("sandbox teardown failed for %s: %s", sandbox_ns, exc)
```

The implementer fills the elided clone/apply/health body using the reused `KubernetesRemediator` + `KubernetesHealthChecker`. The fail-safe shell above is the contract the test locks.

- [ ] **Step 4: Run the fail-safe test**

Run: `uv run pytest tests/test_sandbox_adapter.py -v`
Expected: PASS (NullSandbox tests + the fail-safe test). The fail-safe test forces `_load_k8s` to a raising fake, so the body raises immediately and the `except` returns `passed=False`.

- [ ] **Step 5: Full suite + lint**

Run: `uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check .`
Expected: PASS + clean. `NamespaceCloneSandbox` is only constructed when `sandbox_mode=k8s`, so the default suite never imports `kubernetes` through it.

- [ ] **Step 6: Commit**

```bash
git add services/action/adapters/sandbox.py tests/test_sandbox_adapter.py
git commit -m "feat(sandbox): NamespaceCloneSandbox — clone/apply/health/teardown, fail-safe

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Read projection + frontend row + mock fixture

**Files:**
- Modify: `services/read/projection.py:176-183` (the `ReadModel.apply_outcome` drill-down `"outcome"` dict — add `preflight` beside `"mode"`/`"steps"` via `getattr`)
- Create: `tests/test_read_projection.py` (NEW — `ReadModel.apply_outcome` has no existing test)
- Modify: `frontend/src/data/types.ts:41-46` (`SituationOutcome.preflight?`)
- Modify: `frontend/src/views/Incidents.tsx` (render the pre-flight row after the steps row at :384; add `preflight` to the mock-mode drill-down outcome at :158)

**Interfaces:**
- Consumes: `RemediationOutcome.preflight` (Task 1), `ReadModel`, the `"outcome"` drill-down dict shape.
- Produces: a `preflight` key inside the projected situation drill-down `"outcome"` dict; `SituationOutcome.preflight?: { passed: boolean; detail: string; mode: string } | null` in the frontend.

**The exact frontend anchors** (verified): `SituationOutcome` is at `frontend/src/data/types.ts:41-46` (fields `result`/`health_after`/`mode`/`steps`). In `Incidents.tsx` the drill-down reads `shown.outcome` (NOT a bare `outcome`): the dry-run chip is at :381 (`{shown.outcome?.mode === "dry_run" && ...}`) and the steps row at :383-384. The mock-mode drill-down outcome is constructed inline at `Incidents.tsx:158` (`outcome: { result: "success", health_after: "healthy", mode: "dry_run", steps: [] }`) — that is the mock to extend so the row is visible without a live cluster (the flat `recentOutcomes` in `mock.ts` are a different shape and are NOT the drill-down).

**The exact projection anchor** — `ReadModel.apply_outcome` (`services/read/projection.py:176-183`) builds the drill-down dict like this today:

```python
            self._sits[o.situation_id]["outcome"] = {
                "result": o.result.value
                if isinstance(o.result, RemediationResult)
                else str(o.result),
                "health_after": o.health_after,
                "mode": getattr(o, "mode", "dry_run"),
                "steps": list(getattr(o, "steps", [])),
            }
```

The class is `ReadModel` (not `ReadProjection`). `apply_outcome(self, o)` — `o` is the `RemediationOutcome`. It only writes the `"outcome"` dict when `o.situation_id in self._sits`, so the test MUST first seed the situation via `apply_detected` (and typically `apply_diagnosed`) before `apply_outcome`.

- [ ] **Step 1: Write the failing projection test (NEW FILE)**

Read `services/read/projection.py:107-205` first to get the real `apply_detected` / `apply_diagnosed` / `apply_outcome` signatures and how `_sits[...]["outcome"]` is keyed. Then create `tests/test_read_projection.py`:

```python
from datetime import UTC, datetime

from common.contracts import (
    PreflightResult,
    RemediationOutcome,
    RemediationResult,
    Situation,
    SituationStatus,
)
from services.read.projection import ReadModel


def _situation() -> Situation:
    now = datetime.now(UTC)
    return Situation(
        id="sit-1",
        status=SituationStatus.DETECTED,
        severity="high",
        first_seen=now,
        last_seen=now,
        signature="sig-1",
    )


def test_apply_outcome_projects_preflight_into_drilldown():
    model = ReadModel()
    model.apply_detected(_situation())  # seed _sits so apply_outcome writes "outcome"
    model.apply_outcome(
        RemediationOutcome(
            situation_id="sit-1",
            playbook_id="pb-1",
            result=RemediationResult.SUCCESS,
            health_after="healthy",
            ts=datetime.now(UTC),
            preflight=PreflightResult(passed=True, detail="sandbox: clone healthy", mode="k8s"),
        )
    )
    outcome = model._sits["sit-1"]["outcome"]
    assert outcome["preflight"]["passed"] is True
    assert outcome["preflight"]["mode"] == "k8s"


def test_apply_outcome_without_preflight_projects_none():
    model = ReadModel()
    model.apply_detected(_situation())
    model.apply_outcome(
        RemediationOutcome(
            situation_id="sit-1",
            playbook_id="pb-1",
            result=RemediationResult.SUCCESS,
            health_after="healthy",
            ts=datetime.now(UTC),
        )
    )
    assert model._sits["sit-1"]["outcome"]["preflight"] is None
```

If `apply_detected`'s real signature or the `_sits` key differ from the above (confirm in Step 1's read), adjust the seeding call and the access path to match — the assertion on `outcome["preflight"]` is the invariant.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_read_projection.py -v`
Expected: FAIL — `KeyError: 'preflight'` (the dict has no such key yet).

- [ ] **Step 3: Project `preflight` in `apply_outcome`**

In `services/read/projection.py`, add a `preflight` key to the `"outcome"` dict (after `"steps"`, line 182), using the same `getattr(o, ...)` defensive style so an old outcome projects `None`:

```python
                "preflight": (
                    p.model_dump()
                    if (p := getattr(o, "preflight", None)) is not None
                    else None
                ),
```

- [ ] **Step 4: Run the projection test**

Run: `uv run pytest tests/test_read_projection.py -v`
Expected: PASS (both cases).

- [ ] **Step 5: Add the frontend type**

In `frontend/src/data/types.ts`, extend `SituationOutcome`:

```typescript
  preflight?: { passed: boolean; detail: string; mode: string } | null;
```

- [ ] **Step 6: Render the pre-flight row in `Incidents.tsx`**

Insert the row immediately AFTER the steps row (after `Incidents.tsx:384`, inside the same resolved-outcome block that renders the dry-run chip and steps). The object is `shown.outcome`. Only render when `preflight` exists and its `mode !== "off"`. Match the surrounding Tailwind conventions used by the steps row (`mt-1 font-mono text-2xs`), and use the existing severity color tokens (`text-sev-ok` for pass, `text-sev-warn`/`text-sev-crit` for fail) rather than inventing CSS classes:

```tsx
{shown.outcome?.preflight && shown.outcome.preflight.mode !== "off" && (
  <div className="mt-1 font-mono text-2xs text-ink-3">
    🧪 pre-flight:{" "}
    {shown.outcome.preflight.passed ? (
      <span className="text-sev-ok">rehearsed in sandbox — passed</span>
    ) : (
      <span className="text-sev-warn">failed — {shown.outcome.preflight.detail}</span>
    )}
  </div>
)}
```

Confirm the exact color-token class names against neighboring lines (e.g. the `text-sev-ok` used at :380, `text-sev-warn` at :394) and reuse whatever the file already uses.

- [ ] **Step 7: Add a mock `preflight` so the row is visible in mock mode**

Extend the mock-mode drill-down outcome at `Incidents.tsx:158` — add `preflight` to that inline object so a reviewer sees the row without a live cluster:

```tsx
outcome: { result: "success", health_after: "healthy", mode: "dry_run", steps: [], preflight: { passed: true, detail: "sandbox: clone healthy in 8s", mode: "k8s" } },
```

Note: `mode: "dry_run"` on the outcome stays (that drives the existing dry-run chip); the nested `preflight.mode: "k8s"` is what makes the new row render. Keep it clearly a demo value.

- [ ] **Step 8: Frontend build**

Run: `npm --prefix frontend run build`
Expected: clean (no TS errors).

- [ ] **Step 9: Full suite + lint**

Run: `uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check .`
Expected: PASS + clean.

- [ ] **Step 10: Commit**

```bash
git add services/read/projection.py tests/test_read_projection.py frontend/src/data/types.ts frontend/src/views/Incidents.tsx
git commit -m "feat(sandbox): project preflight verdict + incident pre-flight row

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

(The mock lives inline in `Incidents.tsx:158`, so no separate mock file is staged.)

---

## Task 5: Docs + design note + final gates

**Files:**
- Modify: `deploy/k8s/README.md` (add the sandbox section)
- Add: `docs/sandbox-and-ai-runbooks-design-note.md` (already exists untracked — commit it here)
- No code changes.

- [ ] **Step 1: Document the live sandbox flow in `deploy/k8s/README.md`**

Add a section covering:
- What `sandbox_mode=k8s` does (rehearses on an ephemeral `intelliops-sandbox-*` namespace before approval).
- How to enable it (the k8s overlay sets `SANDBOX_MODE=k8s`, like `REMEDIATOR_MODE=k8s`).
- The honest limit: shared-node isolation, pod-readiness is the primary pass signal, metric predicate best-effort (per Task 3 ruling).
- The manual e2e (acceptance criterion 7): break a Meridian service, watch a `intelliops-sandbox-*` namespace appear + tear down, verdict shows in the UI before approval.

Match the existing README's tone and structure. Reference `docs/sandbox-and-ai-runbooks-design-note.md`.

- [ ] **Step 2: Verify the design note is complete + present**

Confirm `docs/sandbox-and-ai-runbooks-design-note.md` exists and reads coherently (it was authored earlier, currently untracked). No rewrite needed — just confirm and stage it.

- [ ] **Step 3: Run the FULL gate suite one final time**

Run: `uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check . && npm --prefix frontend run build`
Expected: all green + clean.

- [ ] **Step 4: Commit**

```bash
git add deploy/k8s/README.md docs/sandbox-and-ai-runbooks-design-note.md docs/superpowers/specs/2026-08-29-remediation-sandbox-design.md docs/superpowers/plans/2026-08-29-remediation-sandbox.md
git commit -m "docs(sandbox): live rehearsal flow, design note, spec + plan

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review Notes (author)

- **Spec coverage:** §1 contract → Task 1; §2 protocol → Task 1; §3 adapters → Task 1 (Null) + Task 3 (Clone); §4 factory → Task 2; §5 config → Task 1; §6 gate → Task 2; §7 consumer → Task 2; §8 projection → Task 4; §9 frontend → Task 4; docs (AC 7) → Task 5. All acceptance criteria 1–7 mapped.
- **Signature consistency:** `execute_remediation(..., health, sandbox, timeout_seconds, poll_interval_seconds)` and `run_consumer(..., health, sandbox, timeout_seconds, poll_interval_seconds, stop_event)` — `sandbox` after `health` in both; the app.py args tuple matches. Task 2 Step 8 explicitly hunts and fixes any other caller of the changed signatures.
- **Type consistency:** `PreflightResult` fields (`passed`/`detail`/`mode`/`sandbox_namespace`) identical across contract, adapters, projection dict, and the TS type (`passed`/`detail`/`mode`).
- **Additive safety:** every new field defaults `None`; projection uses `getattr`; TS field optional; the default `sandbox_mode="off"` path adds no behavior (Task 1 Step 8 + Task 2 Step 8 assert the full suite stays green).
