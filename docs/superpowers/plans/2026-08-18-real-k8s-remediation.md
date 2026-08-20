# Real Kubernetes Remediation (Stream A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make "resolved" real — a `KubernetesRemediator` that restarts/scales/rolls-back a real pod on a kind cluster (typed K8s API calls), verified by a two-signal `KubernetesHealthChecker`, behind a `REMEDIATOR_MODE` switch defaulting to dry-run.

**Architecture:** Playbook steps become structured `RemediationStep`s; `remediate.py` resolves the target from the situation's `service` label and builds a `RemediationPlan` passed to the remediator, and passes the same target to the health checker. The K8s adapters use the official `kubernetes` Python client with an **injectable `AppsV1Api`** (fake in tests, real via kubeconfig in the compose k8s overlay). Everything is additive behind env switches with test-safe defaults; the demo-app is deployed into kind as the real workload.

**Tech Stack:** Python 3.11, Pydantic v2, FastAPI, the `kubernetes` Python client, kind, Prometheus, Redis Streams.

**Spec:** `docs/superpowers/specs/2026-08-18-real-k8s-remediation-design.md`

## Global Constraints

- Existing suite MUST stay green. `REMEDIATOR_MODE` defaults to `dry_run`, `HEALTH_CHECK_MODE` to `always` — CI/pytest never need a cluster; only fake K8s clients are exercised.
- The `kubernetes` package is a dependency, but tests NEVER make real API calls — the `AppsV1Api` is injected, and tests pass a fake. Real `config.load_kube_config()` is only reached at runtime in `k8s` mode.
- Contract changes are made carefully: `Playbook.steps`/`rollback_steps` become `list[RemediationStep]` (breaking — the named blast radius in the spec must all update together). New contracts are additive.
- The remediator NEVER deletes anything — only restart/scale/rollback (ADR-007 reversible-only). It NEVER raises: any `kubernetes.client.exceptions.ApiException` (or connection error) is caught, logged, and returns `False`. Same fail-safe posture as `PrometheusSource` and `HttpGovernanceGate`.
- The health checker NEVER raises: errors mid-poll are treated as "not yet healthy, keep polling"; it returns a bool within the timeout.
- All bus models use `common/envelope.py`. Injectable-client pattern matches the existing `PrometheusSource(http_client=...)` / `HttpGovernanceGate(http_client=...)`.
- Python: `uv run pytest`, `uv run ruff check`. Commit after each task; messages end with:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

## File Structure

**New (Python):**
- `services/action/adapters/k8s_remediator.py` — `KubernetesRemediator` (+ test)
- `services/action/adapters/k8s_health.py` — `KubernetesHealthChecker` (+ test)
- `services/action/targets.py` — `resolve_target(situation)` (+ test)

**New (infra/docs):**
- `deploy/k8s/kind-config.yaml`, `deploy/k8s/demo-app/{deployment,service}.yaml`,
  `deploy/k8s/prometheus/{configmap,deployment,service}.yaml`, `deploy/k8s/README.md`
- `scripts/kind-up.sh`, `scripts/kind-down.sh`
- `deploy/docker-compose.k8s.yml`

**Modified:**
- `common/contracts.py` — `RemediationStep`, `RemediationTarget`, `RemediationPlan`; `Playbook.steps`/`rollback_steps` → `list[RemediationStep]`
- `common/interfaces.py` — `Remediator.execute/rollback(plan)`, `HealthChecker.check(situation, target)`
- `common/config.py` — `remediator_mode`, `health_check_mode`, `k8s_namespace`
- `services/action/remediate.py` — build the plan + resolve target
- `services/action/app.py` — `_make_remediator()` / `_make_health_checker()`
- `services/action/adapters/remediator.py`, `health.py` — update Dry-run/Recording/Always/Fixed to new signatures
- `services/action/tests/test_remediator.py`, `test_health.py`, `test_remediate.py` — new shapes
- `deploy/playbooks/*.yaml` — structured steps
- `pyproject.toml` — add `kubernetes`
- `flow.md` / `architectural.md` — real remediation path

---

## Task 1: New remediation contracts + structured Playbook steps

**Files:**
- Modify: `common/contracts.py`
- Test: `tests/test_remediation_contracts.py` (Create)

**Interfaces:**
- Produces: `RemediationStep(action: Literal["restart","scale","rollback_deploy","wait"], replicas: int|None=None, note: str|None=None)`; `RemediationTarget(namespace: str, deployment: str)`; `RemediationPlan(target: RemediationTarget, steps: list[RemediationStep], rollback_steps: list[RemediationStep])`. `Playbook.steps` and `Playbook.rollback_steps` become `list[RemediationStep]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_remediation_contracts.py
from common.contracts import HitlMode, Playbook, RemediationPlan, RemediationStep, RemediationTarget


def test_remediation_step_and_plan():
    step = RemediationStep(action="scale", replicas=2)
    assert step.action == "scale" and step.replicas == 2
    plan = RemediationPlan(
        target=RemediationTarget(namespace="ns", deployment="demo-app"),
        steps=[RemediationStep(action="restart")],
        rollback_steps=[RemediationStep(action="restart")],
    )
    assert plan.target.deployment == "demo-app"
    assert plan.steps[0].action == "restart"


def test_playbook_steps_are_structured():
    pb = Playbook(
        id="restart-pod",
        name="Restart",
        match_rule="x",
        steps=[RemediationStep(action="restart"), RemediationStep(action="wait", note="readiness")],
        hitl_mode=HitlMode.HITL,
        reversible=True,
        rollback_steps=[RemediationStep(action="restart")],
    )
    assert pb.steps[0].action == "restart"
    # parses from dicts too (YAML load path)
    pb2 = Playbook.model_validate(
        {
            "id": "scale-service",
            "name": "Scale",
            "match_rule": "x",
            "steps": [{"action": "scale", "replicas": 2}],
            "hitl_mode": "hitl",
            "reversible": True,
            "rollback_steps": [{"action": "scale", "replicas": -2}],
        }
    )
    assert pb2.steps[0].action == "scale" and pb2.steps[0].replicas == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_remediation_contracts.py -v`
Expected: FAIL — `RemediationStep` does not exist.

- [ ] **Step 3: Add the contracts**

In `common/contracts.py`, add `Literal` to the typing imports (`from typing import Literal` if not present), and add these classes (place them just above `class Playbook`):

```python
class RemediationStep(BaseModel):
    action: Literal["restart", "scale", "rollback_deploy", "wait"]
    replicas: int | None = None  # for scale: a delta, e.g. +2 / -2
    note: str | None = None  # human-readable / wait annotation


class RemediationTarget(BaseModel):
    namespace: str
    deployment: str


class RemediationPlan(BaseModel):
    target: RemediationTarget
    steps: list[RemediationStep] = Field(default_factory=list)
    rollback_steps: list[RemediationStep] = Field(default_factory=list)
```

Then change `Playbook`:

```python
class Playbook(BaseModel):
    id: str
    name: str
    match_rule: str
    steps: list[RemediationStep] = Field(default_factory=list)
    hitl_mode: HitlMode
    reversible: bool = False
    rollback_steps: list[RemediationStep] = Field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_remediation_contracts.py -v`
Expected: PASS. (The wider suite will be red until Tasks 2–5 update callers — that's expected; this task's own test is green.)

- [ ] **Step 5: Commit**

```bash
git add common/contracts.py tests/test_remediation_contracts.py
git commit -m "feat(contracts): structured RemediationStep/Target/Plan; Playbook.steps typed"
```

---

## Task 2: Update the Remediator/HealthChecker interfaces + the dry-run/test adapters

**Files:**
- Modify: `common/interfaces.py`, `services/action/adapters/remediator.py`, `services/action/adapters/health.py`
- Test: `services/action/tests/test_remediator.py`, `services/action/tests/test_health.py`

**Interfaces:**
- Consumes: `RemediationPlan`, `RemediationTarget`, `Situation` (Task 1).
- Produces: `Remediator.execute(plan: RemediationPlan) -> bool`, `Remediator.rollback(plan: RemediationPlan) -> bool`; `HealthChecker.check(situation: Situation, target: RemediationTarget) -> bool`. `DryRunRemediator`, `RecordingRemediator`, `AlwaysHealthyChecker`, `FixedHealthChecker` updated to these signatures.

- [ ] **Step 1: Rewrite the adapter tests to the new shapes**

Replace `services/action/tests/test_remediator.py` with:

```python
from common.contracts import RemediationPlan, RemediationStep, RemediationTarget
from services.action.adapters.remediator import DryRunRemediator, RecordingRemediator


def _plan():
    return RemediationPlan(
        target=RemediationTarget(namespace="ns", deployment="demo-app"),
        steps=[RemediationStep(action="restart")],
        rollback_steps=[RemediationStep(action="restart")],
    )


def test_dry_run_always_succeeds():
    r = DryRunRemediator()
    assert r.execute(_plan()) is True
    assert r.rollback(_plan()) is True


def test_recording_captures_plan():
    r = RecordingRemediator()
    p = _plan()
    r.execute(p)
    r.rollback(p)
    assert r.executed_plan is p
    assert r.rolled_back_plan is p


def test_recording_execute_result_configurable():
    r = RecordingRemediator(execute_result=False)
    assert r.execute(_plan()) is False
    assert r.rollback(_plan()) is True
```

In `services/action/tests/test_health.py`, update the fixed-checker test to pass a target:

```python
def test_fixed_health_checker():
    from common.contracts import RemediationTarget, Situation, SituationStatus
    from datetime import UTC, datetime
    from services.action.adapters.health import FixedHealthChecker

    now = datetime(2026, 8, 18, tzinfo=UTC)
    sit = Situation(
        id="s",
        status=SituationStatus.ACTING,
        member_events=[],
        severity="high",
        first_seen=now,
        last_seen=now,
        signature="sig",
    )
    tgt = RemediationTarget(namespace="ns", deployment="demo-app")
    assert FixedHealthChecker(True).check(sit, tgt) is True
    assert FixedHealthChecker(False).check(sit, tgt) is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest services/action/tests/test_remediator.py services/action/tests/test_health.py -v`
Expected: FAIL — old signatures / `executed_plan` attr missing.

- [ ] **Step 3: Update the interfaces**

In `common/interfaces.py`, add `RemediationPlan`, `RemediationTarget` to the `common.contracts` import, and change:

```python
class Remediator(Protocol):
    """Executes and reverses remediation (Kubernetes API, Ansible)."""

    def execute(self, plan: RemediationPlan) -> bool: ...

    def rollback(self, plan: RemediationPlan) -> bool: ...


class HealthChecker(Protocol):
    """Post-remediation health signal (ADR-007 verify step)."""

    def check(self, situation: Situation, target: RemediationTarget) -> bool: ...
```

- [ ] **Step 4: Update `remediator.py`**

Rewrite `services/action/adapters/remediator.py`:

```python
"""Remediator implementations (non-K8s).

DryRunRemediator is the safe default: logs the plan and succeeds without
touching infrastructure. RecordingRemediator is the test double capturing the
plan passed to execute/rollback. The real KubernetesRemediator lives in
k8s_remediator.py."""

from __future__ import annotations

import logging

from common.contracts import RemediationPlan

logger = logging.getLogger("intelliops.action.remediator")


class DryRunRemediator:
    def execute(self, plan: RemediationPlan) -> bool:
        for step in plan.steps:
            logger.info(
                "DRY-RUN execute on %s/%s: %s",
                plan.target.namespace,
                plan.target.deployment,
                step.action,
            )
        return True

    def rollback(self, plan: RemediationPlan) -> bool:
        for step in plan.rollback_steps:
            logger.info(
                "DRY-RUN rollback on %s/%s: %s",
                plan.target.namespace,
                plan.target.deployment,
                step.action,
            )
        return True


class RecordingRemediator:
    def __init__(self, execute_result: bool = True, rollback_result: bool = True) -> None:
        self._execute_result = execute_result
        self._rollback_result = rollback_result
        self.executed_plan: RemediationPlan | None = None
        self.rolled_back_plan: RemediationPlan | None = None

    def execute(self, plan: RemediationPlan) -> bool:
        self.executed_plan = plan
        return self._execute_result

    def rollback(self, plan: RemediationPlan) -> bool:
        self.rolled_back_plan = plan
        return self._rollback_result
```

- [ ] **Step 5: Update `health.py`**

Rewrite `services/action/adapters/health.py`:

```python
"""HealthChecker implementations (non-K8s).

AlwaysHealthyChecker pairs with the dry-run remediator. FixedHealthChecker is
the test double. The real KubernetesHealthChecker lives in k8s_health.py."""

from __future__ import annotations

from common.contracts import RemediationTarget, Situation


class AlwaysHealthyChecker:
    def check(self, situation: Situation, target: RemediationTarget) -> bool:
        return True


class FixedHealthChecker:
    def __init__(self, healthy: bool) -> None:
        self._healthy = healthy

    def check(self, situation: Situation, target: RemediationTarget) -> bool:
        return self._healthy
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest services/action/tests/test_remediator.py services/action/tests/test_health.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add common/interfaces.py services/action/adapters/remediator.py services/action/adapters/health.py services/action/tests/test_remediator.py services/action/tests/test_health.py
git commit -m "feat(action): Remediator/HealthChecker take RemediationPlan/target; update dry-run adapters"
```

---

## Task 3: Target resolution + `remediate.py` builds the plan

**Files:**
- Create: `services/action/targets.py`
- Modify: `services/action/remediate.py`, `common/config.py`
- Test: `services/action/tests/test_targets.py` (Create), and update `services/action/tests/test_remediate.py`

**Interfaces:**
- Consumes: `Situation`, `RemediationTarget`, `RemediationPlan`, `Playbook` (Task 1); the updated remediator/health signatures (Task 2).
- Produces: `resolve_target(situation: Situation, namespace: str) -> RemediationTarget` (deployment = the situation's `service` label, or `"unknown"`); `remediate.py` now builds a `RemediationPlan` and calls `remediator.execute(plan)` / `remediator.rollback(plan)` / `health.check(situation, target)`.

- [ ] **Step 1: Add config settings**

In `common/config.py`, after the read-model settings:

```python
remediator_mode: str = "dry_run"  # "dry_run" | "k8s"
health_check_mode: str = "always"  # "always" | "k8s"
k8s_namespace: str = "intelliops-demo"
```

- [ ] **Step 2: Write the failing target test**

```python
# services/action/tests/test_targets.py
from datetime import UTC, datetime
from common.contracts import Situation, SituationStatus, TelemetryEvent, TelemetryKind
from services.action.targets import resolve_target

NOW = datetime(2026, 8, 18, tzinfo=UTC)


def _sit(service):
    labels = {"service": service} if service else {}
    return Situation(
        id="s",
        status=SituationStatus.DIAGNOSED,
        member_events=[
            TelemetryEvent(
                source="p",
                kind=TelemetryKind.METRIC,
                name="cpu_usage",
                value=90.0,
                labels=labels,
                ts=NOW,
                fingerprint="f",
            )
        ],
        severity="high",
        first_seen=NOW,
        last_seen=NOW,
        signature="sig",
    )


def test_resolve_target_from_service_label():
    t = resolve_target(_sit("demo-app"), namespace="intelliops-demo")
    assert t.namespace == "intelliops-demo" and t.deployment == "demo-app"


def test_resolve_target_unknown_when_no_label():
    t = resolve_target(_sit(None), namespace="intelliops-demo")
    assert t.deployment == "unknown"
```

- [ ] **Step 3: Run to verify fail**

Run: `uv run pytest services/action/tests/test_targets.py -v`
Expected: FAIL — module missing.

- [ ] **Step 4: Implement `resolve_target`**

```python
# services/action/targets.py
"""Resolve a Situation to the K8s deployment it targets.

The situation's member events carry a `service` label (from the telemetry
relabel); the deployment name is that label, in the configured namespace. This
mirrors how real AIOps derives the remediation target from the incident's own
telemetry rather than hard-coding it per playbook."""

from __future__ import annotations

from common.contracts import RemediationTarget, Situation


def resolve_target(situation: Situation, namespace: str) -> RemediationTarget:
    deployment = "unknown"
    for ev in situation.member_events:
        svc = ev.labels.get("service") or ev.labels.get("job")
        if svc:
            deployment = svc
            break
    return RemediationTarget(namespace=namespace, deployment=deployment)
```

- [ ] **Step 5: Wire `remediate.py`**

In `services/action/remediate.py`:
- Add imports: `from common.contracts import RemediationPlan` and `from common.config import get_settings` and `from services.action.targets import resolve_target`.
- Build the plan and thread the target. Replace the execute/health/rollback block (currently lines ~71–83) with:

```python
# Resolve the target once and build a typed plan.
target = resolve_target(situation, get_settings().k8s_namespace)
plan = RemediationPlan(target=target, steps=playbook.steps, rollback_steps=playbook.rollback_steps)

# Execute.
if not remediator.execute(plan):
    _audit(gate, situation, playbook, "execute-failed")
    return _outcome(situation, playbook, RemediationResult.FAILURE, "execute-failed")

# Verify health; roll back if unhealthy.
if health.check(situation, target):
    _audit(gate, situation, playbook, "allow")
    return _outcome(situation, playbook, RemediationResult.SUCCESS, "healthy")

remediator.rollback(plan)
_audit(gate, situation, playbook, "rolled-back")
return _outcome(situation, playbook, RemediationResult.ROLLED_BACK, "unhealthy:rolled-back")
```

- [ ] **Step 6: Update `test_remediate.py`'s `_playbook` helper**

In `services/action/tests/test_remediate.py`, change the `_playbook` helper to structured steps and import them:

```python
# add to the imports from common.contracts: RemediationStep
def _playbook(hitl=HitlMode.AUTO, reversible=True):
    return Playbook(
        id="restart-pod",
        name="Restart",
        match_rule="x",
        steps=[RemediationStep(action="restart")],
        hitl_mode=hitl,
        reversible=reversible,
        rollback_steps=[RemediationStep(action="restart")],
    )
```

The `RecordingRemediator` assertions in that file that checked `executed_steps` (if any) become `executed_plan is not None` / `executed_plan is None`. Scan the file for `executed_steps`/`rolled_back_steps` and update to `executed_plan`/`rolled_back_plan` (the safety tests that assert execute was NOT called become `assert r.executed_plan is None`).

- [ ] **Step 7: Sweep ALL other `Playbook(steps=[...strings...])` constructions repo-wide**

The `steps`/`rollback_steps` type change breaks every test that builds a `Playbook` with string steps — not just the action tests. Find them:

Run: `grep -rn 'Playbook(' --include='*.py' . | grep -v __pycache__ | grep -v '\.venv'`

Update each of these to structured steps (`steps=[RemediationStep(action="restart")]`, `rollback_steps=[RemediationStep(action="restart")]`), importing `RemediationStep` from `common.contracts` where needed. The known list (confirm none are missed):
- `services/action/tests/test_consumer.py`
- `services/action/tests/test_select.py`
- `services/governance/tests/test_governance_api.py`
- `services/governance/tests/test_graduate_endpoint.py`
- `services/governance/tests/test_playbook_store.py`
- `services/rca/tests/test_rank.py`
- `tests/test_contracts.py` (two `Playbook(...)` sites — both need structured steps; the contract test may also assert the field type, update its expectation)

These are mechanical: replace `steps=["..."]` → `steps=[RemediationStep(action="restart")]` and `rollback_steps=["..."]` → `rollback_steps=[RemediationStep(action="restart")]`. The exact action doesn't matter for these tests (they don't execute against K8s); pick `restart`.

- [ ] **Step 8: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS. This is where the contract change fully lands — with Step 7's sweep done, the entire suite (action, governance, rca, contracts) should be green again. Any remaining red is a missed `Playbook(steps=[...strings...])` — fix that specific caller.

- [ ] **Step 9: Commit**

```bash
git add services/action/targets.py services/action/remediate.py common/config.py services/action/tests/ services/governance/tests/ services/rca/tests/ tests/test_contracts.py
git commit -m "feat(action): resolve target from service label; remediate builds RemediationPlan; migrate all playbook-step callers"
```

---

## Task 4: Rewrite seeded playbooks to structured steps + verify the store loads them

**Files:**
- Modify: `deploy/playbooks/restart-pod.yaml`, `scale-service.yaml`, `rollback-deploy.yaml`
- Test: `tests/test_seeded_playbooks_load.py` (Create)

**Interfaces:**
- Consumes: `Playbook` structured-steps contract (Task 1), `FilePlaybookStore.load_seed_playbooks` (existing).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_seeded_playbooks_load.py
from services.governance.adapters.playbook_store import load_seed_playbooks


def test_seeded_playbooks_parse_as_structured():
    pbs = {p.id: p for p in load_seed_playbooks("deploy/playbooks")}
    assert "restart-pod" in pbs and "scale-service" in pbs
    rp = pbs["restart-pod"]
    assert rp.steps[0].action == "restart"  # structured, not a string
    ss = pbs["scale-service"]
    assert any(s.action == "scale" and s.replicas for s in ss.steps)
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/test_seeded_playbooks_load.py -v`
Expected: FAIL — the current YAMLs have string steps; `RemediationStep` validation errors on load.

- [ ] **Step 3: Rewrite the three playbook YAMLs**

`deploy/playbooks/restart-pod.yaml`:
```yaml
id: restart-pod
name: Restart affected pod
match_rule: "error spike in service logs or metrics"
steps:
  - action: restart
  - action: wait
    note: "readiness probe to pass"
hitl_mode: hitl
reversible: true
rollback_steps:
  - action: restart
```

`deploy/playbooks/scale-service.yaml`:
```yaml
id: scale-service
name: Scale out affected service
match_rule: "resource saturation (cpu/mem/disk)"
steps:
  - action: scale
    replicas: 2
  - action: wait
    note: "new replicas ready"
hitl_mode: hitl
reversible: true
rollback_steps:
  - action: scale
    replicas: -2
```

`deploy/playbooks/rollback-deploy.yaml`:
```yaml
id: rollback-deploy
name: Roll back recent deployment
match_rule: "recent deploy preceded the incident"
steps:
  - action: rollback_deploy
  - action: wait
    note: "verify health after rollback"
hitl_mode: hitl
reversible: true
rollback_steps:
  - action: rollback_deploy
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_seeded_playbooks_load.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add deploy/playbooks/ tests/test_seeded_playbooks_load.py
git commit -m "feat(playbooks): structured steps (restart/scale/rollback_deploy/wait)"
```

---

## Task 5: Add the `kubernetes` dependency + KubernetesRemediator

**Files:**
- Modify: `pyproject.toml`
- Create: `services/action/adapters/k8s_remediator.py`, `services/action/tests/test_k8s_remediator.py`

**Interfaces:**
- Consumes: `RemediationPlan`, `RemediationStep` (Task 1).
- Produces: `KubernetesRemediator(namespace_default: str, apps_v1=None)` implementing `execute(plan)` / `rollback(plan)`. `apps_v1` is an injected `AppsV1Api`-like object; when `None`, the real client is loaded lazily (only reached at runtime, never in tests).

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, add `"kubernetes>=29"` to `dependencies`. Run `uv sync`. Commit `pyproject.toml` + `uv.lock` at the end of this task.

- [ ] **Step 2: Write the failing test (fake AppsV1Api — no cluster)**

```python
# services/action/tests/test_k8s_remediator.py
from common.contracts import RemediationPlan, RemediationStep, RemediationTarget
from services.action.adapters.k8s_remediator import KubernetesRemediator


class FakeApiException(Exception):
    pass


class FakeAppsV1:
    def __init__(self, replicas=1, fail_on=None):
        self.calls = []
        self._replicas = replicas
        self._fail_on = fail_on  # method name to raise on

    def _maybe_fail(self, name):
        if self._fail_on == name:
            raise FakeApiException("boom")

    def read_namespaced_deployment(self, name, namespace):
        self._maybe_fail("read")
        self.calls.append(("read", name, namespace))

        class _Scale:
            spec = type("S", (), {"replicas": self._replicas})()

        return _Scale()

    def patch_namespaced_deployment(self, name, namespace, body):
        self._maybe_fail("patch")
        self.calls.append(("patch", name, namespace, body))

    def patch_namespaced_deployment_scale(self, name, namespace, body):
        self._maybe_fail("scale")
        self.calls.append(("scale", name, namespace, body))


def _plan(*steps, rollback=()):
    return RemediationPlan(
        target=RemediationTarget(namespace="ns", deployment="demo-app"),
        steps=list(steps),
        rollback_steps=list(rollback),
    )


def test_restart_patches_restartedat_annotation():
    api = FakeAppsV1()
    r = KubernetesRemediator("ns", apps_v1=api, exc_type=FakeApiException)
    assert r.execute(_plan(RemediationStep(action="restart"))) is True
    patch = next(c for c in api.calls if c[0] == "patch")
    body = patch[3]
    # the restartedAt annotation is set in the pod template
    ann = body["spec"]["template"]["metadata"]["annotations"]
    assert "kubectl.kubernetes.io/restartedAt" in ann


def test_scale_adds_replicas_to_current():
    api = FakeAppsV1(replicas=1)
    r = KubernetesRemediator("ns", apps_v1=api, exc_type=FakeApiException)
    assert r.execute(_plan(RemediationStep(action="scale", replicas=2))) is True
    scale = next(c for c in api.calls if c[0] == "scale")
    assert scale[3]["spec"]["replicas"] == 3  # 1 + 2


def test_wait_is_a_noop():
    api = FakeAppsV1()
    r = KubernetesRemediator("ns", apps_v1=api, exc_type=FakeApiException)
    assert r.execute(_plan(RemediationStep(action="wait", note="x"))) is True
    assert api.calls == []  # nothing hit the API


def test_api_error_returns_false_never_raises():
    api = FakeAppsV1(fail_on="patch")
    r = KubernetesRemediator("ns", apps_v1=api, exc_type=FakeApiException)
    assert r.execute(_plan(RemediationStep(action="restart"))) is False


def test_rollback_runs_rollback_steps():
    api = FakeAppsV1(replicas=3)
    r = KubernetesRemediator("ns", apps_v1=api, exc_type=FakeApiException)
    assert r.rollback(_plan(rollback=[RemediationStep(action="scale", replicas=-2)])) is True
    scale = next(c for c in api.calls if c[0] == "scale")
    assert scale[3]["spec"]["replicas"] == 1  # 3 + (-2)
```

- [ ] **Step 3: Run to verify fail**

Run: `uv run pytest services/action/tests/test_k8s_remediator.py -v`
Expected: FAIL — module missing.

- [ ] **Step 4: Implement `KubernetesRemediator`**

```python
# services/action/adapters/k8s_remediator.py
"""A Remediator that acts on a real Kubernetes cluster via the API.

Maps structured RemediationSteps to typed AppsV1Api calls — no shell, no string
parsing. Defensive by construction: any API error is caught and turns into a
False result (never an escaped exception), so a remediation that can't reach the
cluster fails safely (ADR-007). Only restart/scale/rollback — never delete."""

from __future__ import annotations

import datetime as _dt
import logging

from common.contracts import RemediationPlan, RemediationStep, RemediationTarget

logger = logging.getLogger("intelliops.action.k8s")

_MAX_REPLICAS = 10
_MIN_REPLICAS = 1


def _default_apps_v1():
    from kubernetes import client, config

    config.load_kube_config()
    return client.AppsV1Api()


def _default_exc_type():
    from kubernetes.client.exceptions import ApiException

    return ApiException


class KubernetesRemediator:
    def __init__(self, namespace_default: str, apps_v1=None, exc_type=None) -> None:
        self._ns_default = namespace_default
        self._apps_v1 = apps_v1  # injected in tests; loaded lazily if None
        self._exc_type = exc_type  # the exception class to treat as a safe failure

    def _api(self):
        if self._apps_v1 is None:
            self._apps_v1 = _default_apps_v1()
        return self._apps_v1

    def _exc(self):
        if self._exc_type is None:
            self._exc_type = _default_exc_type()
        return self._exc_type

    def execute(self, plan: RemediationPlan) -> bool:
        return self._run(plan.target, plan.steps)

    def rollback(self, plan: RemediationPlan) -> bool:
        return self._run(plan.target, plan.rollback_steps)

    def _run(self, target: RemediationTarget, steps: list[RemediationStep]) -> bool:
        api = self._api()
        ns = target.namespace or self._ns_default
        try:
            for step in steps:
                self._dispatch(api, ns, target.deployment, step)
        except self._exc() as exc:  # any K8s API error → safe failure
            logger.warning("k8s remediation failed on %s/%s: %s", ns, target.deployment, exc)
            return False
        except Exception as exc:  # noqa: BLE001 — fail closed on any client/connection error
            logger.warning("k8s remediation errored on %s/%s: %s", ns, target.deployment, exc)
            return False
        return True

    def _dispatch(self, api, ns: str, deployment: str, step: RemediationStep) -> None:
        if step.action == "wait":
            return  # readiness is the health checker's job
        if step.action == "restart":
            stamp = _dt.datetime.now(_dt.UTC).isoformat()
            body = {
                "spec": {
                    "template": {
                        "metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": stamp}}
                    }
                }
            }
            api.patch_namespaced_deployment(deployment, ns, body)
            return
        if step.action == "scale":
            current = api.read_namespaced_deployment(deployment, ns).spec.replicas or 1
            desired = max(_MIN_REPLICAS, min(_MAX_REPLICAS, current + (step.replicas or 0)))
            api.patch_namespaced_deployment_scale(deployment, ns, {"spec": {"replicas": desired}})
            return
        if step.action == "rollback_deploy":
            # A rollback is a restart against the prior revision annotation; for the
            # demo, a rolling restart recreates the pod (the fault is in-memory), so
            # we implement rollback_deploy as a rollout restart of the deployment.
            stamp = _dt.datetime.now(_dt.UTC).isoformat()
            body = {
                "spec": {
                    "template": {"metadata": {"annotations": {"intelliops.io/rolledBackAt": stamp}}}
                }
            }
            api.patch_namespaced_deployment(deployment, ns, body)
            return
        logger.warning("unknown remediation action: %s", step.action)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest services/action/tests/test_k8s_remediator.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock services/action/adapters/k8s_remediator.py services/action/tests/test_k8s_remediator.py
git commit -m "feat(action): KubernetesRemediator — typed API calls, fail-safe, never deletes"
```

---

## Task 6: KubernetesHealthChecker (pod readiness + metric recovery, polled)

**Files:**
- Create: `services/action/adapters/k8s_health.py`, `services/action/tests/test_k8s_health.py`

**Interfaces:**
- Consumes: `Situation`, `RemediationTarget` (Task 1); a metric-check callable.
- Produces: `KubernetesHealthChecker(apps_v1=None, metric_healthy=None, timeout_seconds=30.0, poll_interval_seconds=2.0, exc_type=None)` implementing `check(situation, target) -> bool`. `metric_healthy` is a `() -> bool` callable (injected; the real one queries Prometheus). Both signals must be True within the timeout.

- [ ] **Step 1: Write the failing test**

```python
# services/action/tests/test_k8s_health.py
from datetime import UTC, datetime
from common.contracts import RemediationTarget, Situation, SituationStatus
from services.action.adapters.k8s_health import KubernetesHealthChecker

NOW = datetime(2026, 8, 18, tzinfo=UTC)


def _sit():
    return Situation(
        id="s",
        status=SituationStatus.ACTING,
        member_events=[],
        severity="high",
        first_seen=NOW,
        last_seen=NOW,
        signature="sig",
    )


def _tgt():
    return RemediationTarget(namespace="ns", deployment="demo-app")


class FakeExc(Exception):
    pass


class FakeApps:
    def __init__(self, ready, desired=1, fail=False):
        self._ready, self._desired, self._fail = ready, desired, fail

    def read_namespaced_deployment_status(self, name, namespace):
        if self._fail:
            raise FakeExc("boom")

        class _S:
            status = type("St", (), {"ready_replicas": self._ready, "replicas": self._desired})()

        return _S()


def _hc(apps, metric_ok, timeout=0.2):
    return KubernetesHealthChecker(
        apps_v1=apps,
        metric_healthy=lambda: metric_ok,
        timeout_seconds=timeout,
        poll_interval_seconds=0.0,
        exc_type=FakeExc,
    )


def test_both_signals_green_returns_true():
    assert _hc(FakeApps(ready=1, desired=1), metric_ok=True).check(_sit(), _tgt()) is True


def test_pod_ready_but_metric_bad_times_out_false():
    assert _hc(FakeApps(ready=1, desired=1), metric_ok=False).check(_sit(), _tgt()) is False


def test_pod_not_ready_times_out_false():
    assert _hc(FakeApps(ready=0, desired=1), metric_ok=True).check(_sit(), _tgt()) is False


def test_api_error_does_not_raise_times_out_false():
    assert _hc(FakeApps(ready=1, fail=True), metric_ok=True).check(_sit(), _tgt()) is False
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest services/action/tests/test_k8s_health.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `KubernetesHealthChecker`**

```python
# services/action/adapters/k8s_health.py
"""Post-remediation health check against a real cluster.

Two signals, both required: pod readiness (readyReplicas == desiredReplicas from
the deployment status) AND metric recovery (a caller-supplied predicate that
re-queries Prometheus). Polls both up to a timeout — a rolling restart plus a
scrape cycle aren't instant. Never raises: an error mid-poll is treated as
'not yet healthy', so an unreachable cluster times out to False and the caller
rolls back (ADR-007)."""

from __future__ import annotations

import logging
import time

from common.contracts import RemediationTarget, Situation

logger = logging.getLogger("intelliops.action.k8s_health")


def _default_apps_v1():
    from kubernetes import client, config

    config.load_kube_config()
    return client.AppsV1Api()


def _default_exc_type():
    from kubernetes.client.exceptions import ApiException

    return ApiException


class KubernetesHealthChecker:
    def __init__(
        self,
        apps_v1=None,
        metric_healthy=None,
        timeout_seconds: float = 30.0,
        poll_interval_seconds: float = 2.0,
        exc_type=None,
    ) -> None:
        self._apps_v1 = apps_v1
        self._metric_healthy = metric_healthy or (lambda: True)
        self._timeout = timeout_seconds
        self._poll = poll_interval_seconds
        self._exc_type = exc_type

    def _api(self):
        if self._apps_v1 is None:
            self._apps_v1 = _default_apps_v1()
        return self._apps_v1

    def _exc(self):
        if self._exc_type is None:
            self._exc_type = _default_exc_type()
        return self._exc_type

    def check(self, situation: Situation, target: RemediationTarget) -> bool:
        deadline = time.monotonic() + self._timeout
        while True:
            if self._pod_ready(target) and self._safe_metric():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(self._poll)

    def _pod_ready(self, target: RemediationTarget) -> bool:
        try:
            st = (
                self._api()
                .read_namespaced_deployment_status(target.deployment, target.namespace)
                .status
            )
        except self._exc():
            return False
        except Exception:  # noqa: BLE001 — treat any client error as not-yet-ready
            return False
        ready = st.ready_replicas or 0
        desired = st.replicas or 0
        return desired > 0 and ready == desired

    def _safe_metric(self) -> bool:
        try:
            return bool(self._metric_healthy())
        except Exception:  # noqa: BLE001 — a failed metric query is 'not yet healthy'
            return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest services/action/tests/test_k8s_health.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add services/action/adapters/k8s_health.py services/action/tests/test_k8s_health.py
git commit -m "feat(action): KubernetesHealthChecker — pod readiness + metric recovery, polled, fail-safe"
```

---

## Task 7: Wire the mode switches into action/app.py

**Files:**
- Modify: `services/action/app.py`
- Test: `services/action/tests/test_adapter_selection.py` (Create)

**Interfaces:**
- Consumes: `remediator_mode`, `health_check_mode`, `k8s_namespace` (Task 3); `KubernetesRemediator` (Task 5), `KubernetesHealthChecker` (Task 6), `DryRunRemediator`/`AlwaysHealthyChecker`.
- Produces: `_make_remediator(settings)` → `KubernetesRemediator` when `remediator_mode == "k8s"` else `DryRunRemediator`; `_make_health_checker(settings)` → `KubernetesHealthChecker` when `health_check_mode == "k8s"` else `AlwaysHealthyChecker`. Lifespan uses them.

- [ ] **Step 1: Write the failing test**

```python
# services/action/tests/test_adapter_selection.py
from services.action.app import _make_remediator, _make_health_checker
from services.action.adapters.remediator import DryRunRemediator
from services.action.adapters.health import AlwaysHealthyChecker
from services.action.adapters.k8s_remediator import KubernetesRemediator
from services.action.adapters.k8s_health import KubernetesHealthChecker


class _S:
    remediator_mode = "dry_run"
    health_check_mode = "always"
    k8s_namespace = "intelliops-demo"
    prometheus_url = "http://localhost:9090"


def test_dry_run_defaults():
    assert isinstance(_make_remediator(_S()), DryRunRemediator)
    assert isinstance(_make_health_checker(_S()), AlwaysHealthyChecker)


def test_k8s_mode_selects_k8s_adapters():
    s = _S()
    s.remediator_mode = "k8s"
    s.health_check_mode = "k8s"
    assert isinstance(_make_remediator(s), KubernetesRemediator)
    assert isinstance(_make_health_checker(s), KubernetesHealthChecker)
```

Note: `_make_remediator`/`_make_health_checker` must construct the K8s adapters WITHOUT touching a cluster — the real client is loaded lazily only on first API call (Tasks 5/6), so merely constructing them is safe in this test.

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest services/action/tests/test_adapter_selection.py -v`
Expected: FAIL — factories don't exist.

- [ ] **Step 3: Add the factories + wire the lifespan**

In `services/action/app.py`, add imports for the K8s adapters and a metric-healthy helper, add the two factories, and use them in the lifespan (replacing the inline `DryRunRemediator()` / `AlwaysHealthyChecker()`):

```python
from services.action.adapters.k8s_remediator import KubernetesRemediator
from services.action.adapters.k8s_health import KubernetesHealthChecker
from services.action.adapters.remediator import DryRunRemediator
from services.action.adapters.health import AlwaysHealthyChecker


def _make_remediator(settings):
    if settings.remediator_mode == "k8s":
        return KubernetesRemediator(settings.k8s_namespace)
    return DryRunRemediator()


def _make_health_checker(settings):
    if settings.health_check_mode == "k8s":
        # metric_healthy re-queries Prometheus for the demo-app error rate; a low
        # value means recovered. Built lazily so dry-run mode never imports httpx here.
        import httpx

        def metric_healthy() -> bool:
            try:
                r = httpx.get(
                    f"{settings.prometheus_url}/api/v1/query",
                    params={"query": "cpu_usage"},
                    timeout=5.0,
                )
                results = r.json().get("data", {}).get("result", [])
                return all(float(v["value"][1]) < 50 for v in results) if results else False
            except Exception:  # noqa: BLE001
                return False

        return KubernetesHealthChecker(metric_healthy=metric_healthy)
    return AlwaysHealthyChecker()
```

Then in `lifespan`, replace `DryRunRemediator(), AlwaysHealthyChecker()` in the `run_consumer` args with `_make_remediator(settings), _make_health_checker(settings)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest services/action/tests/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/action/app.py services/action/tests/test_adapter_selection.py
git commit -m "feat(action): REMEDIATOR_MODE/HEALTH_CHECK_MODE switches select K8s adapters"
```

---

## Task 8: Full-suite green checkpoint

**Files:** none (verification only)

- [ ] **Step 1: Run the whole suite**

Run: `uv run pytest -q`
Expected: all pass. If red, the contract change missed a caller — most likely a test still constructing `Playbook(steps=[...strings...])` or calling `execute(steps)`/`check(situation)`. Fix the specific caller (do not change behavior).

- [ ] **Step 2: Ruff**

Run: `uv run ruff check` (then `uv run ruff check --fix` for import-order nits in new files; re-run to confirm clean).

- [ ] **Step 3: Commit any lint fixes**

```bash
git add -A && git commit -m "chore: lint + full-suite green after K8s remediation adapters" || echo "nothing to commit"
```

---

## Task 9: kind cluster config + demo-app + Prometheus manifests

**Files:**
- Create: `deploy/k8s/kind-config.yaml`, `deploy/k8s/demo-app/deployment.yaml`, `deploy/k8s/demo-app/service.yaml`, `deploy/k8s/prometheus/configmap.yaml`, `deploy/k8s/prometheus/deployment.yaml`, `deploy/k8s/prometheus/service.yaml`

**Interfaces:** produces the cluster manifests the scripts (Task 10) apply. No pytest — validated by `kubectl apply --dry-run=client` in Task 10.

- [ ] **Step 1: kind config (NodePort mapping for in-cluster Prometheus)**

```yaml
# deploy/k8s/kind-config.yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    extraPortMappings:
      - containerPort: 30090   # prometheus NodePort
        hostPort: 30090
        protocol: TCP
```

- [ ] **Step 2: demo-app Deployment + Service**

```yaml
# deploy/k8s/demo-app/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: demo-app
  namespace: intelliops-demo
spec:
  replicas: 1
  selector:
    matchLabels: { app: demo-app }
  template:
    metadata:
      labels: { app: demo-app }
    spec:
      containers:
        - name: demo-app
          image: intelliops-demo-app:local
          imagePullPolicy: IfNotPresent
          # The shared image's CMD launches $SERVICE_MODULE on $PORT (see
          # deploy/Dockerfile). Point it at the demo-app app + its port.
          env:
            - { name: SERVICE_MODULE, value: "services.demo_app.app:app" }
            - { name: PORT, value: "8080" }
          ports: [{ containerPort: 8080 }]
          readinessProbe:
            httpGet: { path: /health, port: 8080 }
            initialDelaySeconds: 2
            periodSeconds: 3
          resources:
            requests: { cpu: "50m", memory: "64Mi" }
            limits: { cpu: "250m", memory: "128Mi" }
```

```yaml
# deploy/k8s/demo-app/service.yaml
apiVersion: v1
kind: Service
metadata:
  name: demo-app
  namespace: intelliops-demo
  labels: { app: demo-app }
spec:
  selector: { app: demo-app }
  ports: [{ port: 8080, targetPort: 8080 }]
```

- [ ] **Step 3: Prometheus configmap + deployment + NodePort service**

```yaml
# deploy/k8s/prometheus/configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: prometheus-config
  namespace: intelliops-demo
data:
  prometheus.yml: |
    global:
      scrape_interval: 5s
    scrape_configs:
      - job_name: demo-app
        static_configs:
          - targets: ["demo-app.intelliops-demo:8080"]
            labels:
              service: demo-app
```

```yaml
# deploy/k8s/prometheus/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: prometheus
  namespace: intelliops-demo
spec:
  replicas: 1
  selector:
    matchLabels: { app: prometheus }
  template:
    metadata:
      labels: { app: prometheus }
    spec:
      containers:
        - name: prometheus
          image: prom/prometheus
          args: ["--config.file=/etc/prometheus/prometheus.yml"]
          ports: [{ containerPort: 9090 }]
          volumeMounts:
            - name: config
              mountPath: /etc/prometheus
      volumes:
        - name: config
          configMap: { name: prometheus-config }
```

```yaml
# deploy/k8s/prometheus/service.yaml
apiVersion: v1
kind: Service
metadata:
  name: prometheus
  namespace: intelliops-demo
spec:
  type: NodePort
  selector: { app: prometheus }
  ports:
    - port: 9090
      targetPort: 9090
      nodePort: 30090
```

- [ ] **Step 4: Commit**

```bash
git add deploy/k8s/
git commit -m "feat(k8s): kind config + demo-app + Prometheus manifests (intelliops-demo ns)"
```

---

## Task 10: kind-up/down scripts + compose k8s overlay + runbook

**Files:**
- Create: `scripts/kind-up.sh`, `scripts/kind-down.sh`, `deploy/docker-compose.k8s.yml`, `deploy/k8s/README.md`

**Interfaces:** produces the one-command cluster bring-up + the compose overlay that flips action into `k8s` mode.

- [ ] **Step 1: kind-up.sh**

```bash
#!/usr/bin/env bash
# Bring up a kind cluster with the demo-app + Prometheus so real remediation
# has a real workload to act on. Requires: docker, kind, kubectl.
set -euo pipefail
CLUSTER=${CLUSTER:-intelliops}
HERE="$(cd "$(dirname "$0")/.." && pwd)"

echo "→ Creating kind cluster '$CLUSTER'…"
kind create cluster --name "$CLUSTER" --config "$HERE/deploy/k8s/kind-config.yaml"

echo "→ Building + loading the demo-app image…"
# The shared image runs whatever $SERVICE_MODULE the container env sets (the
# demo-app deployment sets it to services.demo_app.app:app), so one plain build
# of deploy/Dockerfile is all that's needed.
docker build -t intelliops-demo-app:local -f "$HERE/deploy/Dockerfile" "$HERE"
kind load docker-image intelliops-demo-app:local --name "$CLUSTER"

echo "→ Applying manifests…"
kubectl create namespace intelliops-demo --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f "$HERE/deploy/k8s/demo-app/"
kubectl apply -f "$HERE/deploy/k8s/prometheus/"

echo "→ Waiting for rollouts…"
kubectl -n intelliops-demo rollout status deploy/demo-app --timeout=120s
kubectl -n intelliops-demo rollout status deploy/prometheus --timeout=120s

echo "✓ Cluster up."
echo "  Prometheus: http://localhost:30090"
echo "  kubeconfig: run 'kind get kubeconfig --name $CLUSTER > /tmp/intelliops.kubeconfig'"
echo "  Then start the stack with the k8s overlay (see deploy/k8s/README.md)."
```

Make executable + syntax-check: `chmod +x scripts/kind-up.sh && bash -n scripts/kind-up.sh`.
Note: the demo-app image build assumes the existing `deploy/Dockerfile` launches `$SERVICE_MODULE`; if the Dockerfile can't be parameterized cleanly at build time, the script sets `SERVICE_MODULE` via the manifest's env instead — the manifest's container already runs the shared image; adjust the deployment to set `SERVICE_MODULE=services.demo_app.app:app` and `PORT=8080` as env if the single-image approach is used. (Confirm against `deploy/Dockerfile`'s CMD when implementing.)

- [ ] **Step 2: kind-down.sh**

```bash
#!/usr/bin/env bash
set -euo pipefail
kind delete cluster --name "${CLUSTER:-intelliops}"
```

`chmod +x scripts/kind-down.sh && bash -n scripts/kind-down.sh`.

- [ ] **Step 3: compose k8s overlay**

```yaml
# deploy/docker-compose.k8s.yml
# Overlay that points action at a real kind cluster and ingestion at the
# in-cluster Prometheus. Use with:
#   docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.k8s.yml up
services:
  action:
    environment:
      INTELLIOPS_REMEDIATOR_MODE: k8s
      INTELLIOPS_HEALTH_CHECK_MODE: k8s
      INTELLIOPS_PROMETHEUS_URL: http://host.docker.internal:30090
      KUBECONFIG: /kubeconfig
    volumes:
      - /tmp/intelliops.kubeconfig:/kubeconfig:ro
    extra_hosts:
      - "host.docker.internal:host-gateway"
  ingestion:
    environment:
      INTELLIOPS_PROMETHEUS_URL: http://host.docker.internal:30090
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

- [ ] **Step 4: runbook**

Write `deploy/k8s/README.md` documenting the full flow:
- prerequisites (docker, kind, kubectl)
- `./scripts/kind-up.sh` → note the Prometheus URL + kubeconfig export step
- export kubeconfig: `kind get kubeconfig --name intelliops > /tmp/intelliops.kubeconfig`
- start stack: `docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.k8s.yml up --build`
- drive the incident: break the in-cluster demo-app (`kubectl -n intelliops-demo exec deploy/demo-app -- curl -s -X POST localhost:8080/break`, or via a port-forward), watch it detect → diagnose → the console's HITL gate → approve → **real pod restart** (`kubectl -n intelliops-demo get pods -w` shows the pod recreate) → resolved.
- teardown: `./scripts/kind-down.sh`
- **the honest note:** this path needs a real cluster; it is the demo/PPO story, not part of CI. `REMEDIATOR_MODE` defaults to `dry_run` everywhere else.

- [ ] **Step 5: Validate + commit**

Run: `bash -n scripts/kind-up.sh scripts/kind-down.sh`
If `docker` + `kind` are available, optionally validate manifests: `kubectl apply --dry-run=client -f deploy/k8s/demo-app/ -f deploy/k8s/prometheus/` (skip if no cluster tooling; note it).

```bash
git add scripts/kind-up.sh scripts/kind-down.sh deploy/docker-compose.k8s.yml deploy/k8s/README.md
git commit -m "feat(k8s): kind-up/down scripts + compose k8s overlay + demo runbook"
```

---

## Task 11: Docs — real remediation path in flow.md + architectural.md

**Files:**
- Modify: `flow.md`, `architectural.md`

- [ ] **Step 1: flow.md**

In the `Remediator` / `HealthChecker` rows of the interfaces table (§4), move `KubernetesRemediator` and the real health checker from "deferred/planned" to "implementations that exist today" (behind `REMEDIATOR_MODE=k8s`). In §8 "Current status", update the "deliberately still simulated" list: remediation is now **real on an opt-in kind cluster** (dry-run stays the default); note the K8s demo runbook.

- [ ] **Step 2: architectural.md**

Update ADR-007's consequences / the "deferred" section (§6): real Kubernetes remediation + real health checks are **built** (behind the mode switch, kind-targeted), no longer just the next milestone. Add a short note that the structured `RemediationPlan` (typed steps + resolved target) is what made the real remediator safe (no shell, no string parsing) — reference it from ADR-007 or add a one-paragraph ADR-013 if it reads better as its own decision.

- [ ] **Step 3: Commit**

```bash
git add flow.md architectural.md
git commit -m "docs: real K8s remediation now built (behind REMEDIATOR_MODE); update ADR-007"
```

---

## Self-Review Notes (for the executor)

- **The contract change lands across Tasks 1–4.** Between Task 1 and Task 3 the full suite is intentionally red (callers not yet updated). Task 3 Step 7 sweeps the FULL repo-wide blast radius (7 test files beyond the action tests build `Playbook(steps=[...strings...])`); Task 3 Step 8 is the green checkpoint. Task 8 re-verifies after the K8s adapters land. Don't panic at red mid-sequence; each task's OWN test is green.
- **`kubernetes` client API** (verified current via the official client docs): `AppsV1Api.patch_namespaced_deployment(name, ns, body)` for the restart annotation; `read_namespaced_deployment(name, ns).spec.replicas` for current scale; `patch_namespaced_deployment_scale(name, ns, {"spec":{"replicas":N}})` to scale; `read_namespaced_deployment_status(name, ns).status.ready_replicas / .replicas` for health; `ApiException` from `kubernetes.client.exceptions`. The adapters inject these so tests never call them for real.
- **`RemediationResult.ROLLED_BACK`** already exists — the rollback outcome path in `remediate.py` is unchanged except it now passes `plan` instead of `rollback_steps`.
- **demo-app image in kind:** the manifest uses `intelliops-demo-app:local` loaded via `kind load`. If the shared `deploy/Dockerfile` runs `$SERVICE_MODULE`, the demo-app deployment must set `SERVICE_MODULE=services.demo_app.app:app` + `PORT=8080` as container env (confirm against the Dockerfile CMD; Task 10 Step 1 flags this).
- **No cluster in CI:** every K8s test uses a fake `AppsV1Api` and an injected `exc_type`/`metric_healthy`. The real `config.load_kube_config()` is only reached when the adapter's `_api()` is first called at runtime in `k8s` mode.
