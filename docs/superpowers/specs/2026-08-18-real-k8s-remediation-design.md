# Real Kubernetes Remediation (Stream A) — Design

**Date:** 2026-08-18
**Status:** Approved (pending spec review)
**Owner:** Manvik (integration lead) — the WORKPLAN Stream A centerpiece.
**Depends on:** the live stack + live completeness (running dry-run today).

## Goal

Make "resolved" real. On a local **kind** cluster, IntelliOps detects a real
workload's failure and a real `KubernetesRemediator` restarts the actual pod,
verified by a real `KubernetesHealthChecker`, then rolls back for real if the fix
didn't take. This is the strongest "production engineering" story in the project.

Everything is behind a `REMEDIATOR_MODE` switch defaulting to `dry_run`, so the
existing compose demo, `pytest`, and CI never need a cluster.

## Topology (minimal shift)

The 9 IntelliOps services + the React console **stay in docker-compose, unchanged.**
Only the *target* moves to Kubernetes:

```
docker-compose (unchanged): redis · ingestion · correlation · rca · action ·
                            governance · feedback · read · console
   action-service ── KubernetesRemediator ──(kubeconfig)──┐
                                                          │ K8s API
kind cluster · namespace intelliops-demo ─────────────────▼
   Deployment: demo-app (the breakable FastAPI app, now a real pod)
   Prometheus (scrapes demo-app in-cluster; exposed via NodePort)
```

- **action → kind:** the `KubernetesRemediator` uses a mounted kubeconfig to reach
  the kind API from its container.
- **ingestion → in-cluster Prometheus:** `PrometheusSource` queries the kind
  Prometheus via a NodePort URL.
- An opt-in compose overlay (`deploy/docker-compose.k8s.yml`) flips these env vars,
  so the base compose stack is untouched.

## The interface change (the one structural decision)

`Remediator.execute(steps: list[str])` gets only strings — no target, no situation —
so a real remediator can't work with it. The fix threads a **resolved, typed plan**
through instead. New contracts in `common/contracts.py`:

```python
class RemediationStep(BaseModel):
    action: Literal["restart", "scale", "rollback_deploy", "wait"]
    replicas: int | None = None   # for scale (delta, e.g. +2)
    note: str | None = None       # human-readable / wait

class RemediationTarget(BaseModel):
    namespace: str
    deployment: str               # resolved from the situation's service label

class RemediationPlan(BaseModel):
    target: RemediationTarget
    steps: list[RemediationStep]
    rollback_steps: list[RemediationStep]
```

`common/interfaces.py`:
```python
class Remediator(Protocol):
    def execute(self, plan: RemediationPlan) -> bool: ...
    def rollback(self, plan: RemediationPlan) -> bool: ...

class HealthChecker(Protocol):
    def check(self, situation: Situation, target: RemediationTarget) -> bool: ...
```

**`Playbook.steps` becomes `list[RemediationStep]`** (was `list[str]`) — a breaking
change to the Playbook contract, but contained: only `remediate.py`, the playbook
YAMLs, and action tests touch playbook steps.

**Who builds the plan:** `remediate.py`'s `execute_remediation(situation, playbook,
...)` already has `situation` in scope at all three call sites (execute/check/
rollback). It resolves the target **once** from the situation's `service` label
(mapping `service → {namespace, deployment}`, defaulting
`demo-app → {intelliops-demo, demo-app}`), builds the `RemediationPlan`, and passes:
- the **plan** to `remediator.execute(plan)` / `remediator.rollback(plan)`, and
- the **same resolved target** to `health.check(situation, target)`.

So the two signature changes are threaded consistently from one resolution. The
remediator/health-checker receive fully-resolved typed inputs; no string parsing,
no `${service}` interpolation inside the adapters.

**Target resolution helper:** a small pure function (`resolve_target(situation) →
RemediationTarget`) in `remediate.py` (or a tiny `targets.py`), unit-testable on its
own, so the mapping logic isn't buried in the orchestration.

**Why:** the remediator becomes a pure "given a typed plan, make these K8s API
calls" unit — no situation knowledge, trivially testable with a fake client. The
situation→target resolution lives in `remediate.py` where the context is. Clean
separation, defensible design.

## KubernetesRemediator

Uses the official `kubernetes` Python client (`AppsV1Api`), loaded from the mounted
kubeconfig. Action → typed API call:

| `action` | Real K8s call |
|---|---|
| `restart` | Patch the deployment's pod template with a `kubectl.kubernetes.io/restartedAt` annotation → rolling restart (what `kubectl rollout restart` does) |
| `scale` | `patch_namespaced_deployment_scale` to `current + replicas` (bounded ≥ 1, ≤ a sane cap) |
| `rollback_deploy` | roll the deployment back to its previous revision |
| `wait` | no-op at execute time (readiness is the health checker's job) |

`execute(plan)`: dispatch each step on `plan.target`; if any call raises → log,
return `False`. `rollback(plan)` runs `plan.rollback_steps` the same way (scale
rollback = `current − replicas`; restart rollback = another restart, idempotent).

**Defensive by construction:** every API call is wrapped — a
`kubernetes.client.ApiException` (not found, RBAC denied, unreachable) is caught,
logged, and turns into `execute() → False`, never an escaped exception. A
remediation that can't reach the cluster fails safely (action records
`execute-failed`), never crashes the consumer thread — upholding ADR-007.

**Never deletes.** Only restart/scale/rollback (all reversible, ADR-007); delete is
not a supported action.

**Injectable client:** `KubernetesRemediator(namespace_default, apps_v1=None)` —
tests pass a fake `AppsV1Api` (records calls, no cluster); the real path loads
`config.load_kube_config()`. Same pattern as `PrometheusSource(http_client=...)`.

## KubernetesHealthChecker

Two signals, both must pass, replacing `AlwaysHealthyChecker`:

1. **Pod readiness (K8s truth):** query the deployment status — `readyReplicas ==
   desiredReplicas` and pods past their readiness probe. Confirms the restart
   physically succeeded (a real new pod is serving).
2. **Metric recovery (observability truth):** re-query Prometheus for the demo-app's
   error rate / cpu — has it dropped back to healthy? Confirms the incident actually
   resolved, not just that a pod restarted.

Requiring both is the honest "is it actually fixed?" check a real SRE does.

**Polling:** a rolling restart + a Prometheus scrape cycle aren't instant, so
`check()` polls both signals up to a timeout (~30s). Both green → `True`. Deadline
passes with either bad → `False` → `remediate.py` triggers the real rollback.

**Defensive + injectable:** API/Prometheus errors mid-poll are caught and treated as
"not yet healthy, keep polling" (an unreachable cluster times out and rolls back,
never crashes). Injected fake clients + a small poll interval make tests
deterministic (same shape as the HTTP gate's `await_decision`).

**Config:** `HEALTH_CHECK_MODE=always|k8s` (default `always`, pairs with `dry_run`).

## Cluster setup + demo-app deployment

All new files under `deploy/k8s/` — zero conflict with other streams.

- **`scripts/kind-up.sh`:** `kind create cluster` (config maps a NodePort for
  in-cluster Prometheus) → build demo-app image + `kind load docker-image` → apply
  demo-app + Prometheus manifests → wait for rollouts → print kubeconfig path +
  Prometheus NodePort URL.
- **`scripts/kind-down.sh`:** `kind delete cluster --name intelliops`.
- **`deploy/k8s/demo-app/`:** `deployment.yaml` (namespace `intelliops-demo`,
  `replicas: 1`, readiness probe on `/health`, resource requests) + `service.yaml`
  (ClusterIP for Prometheus scraping).
- **`deploy/k8s/prometheus/`:** `configmap.yaml` (scrape `demo-app.intelliops-demo:8080`
  with the `service=demo-app` relabel — so telemetry carries the label the
  target-resolver needs) + `deployment.yaml` + `service.yaml` (NodePort).
- **`deploy/docker-compose.k8s.yml`:** the opt-in overlay — mounts the kubeconfig
  into action, sets `REMEDIATOR_MODE=k8s` / `HEALTH_CHECK_MODE=k8s`, points
  `INTELLIOPS_PROMETHEUS_URL` at the kind NodePort. Run with
  `-f docker-compose.yml -f docker-compose.k8s.yml`; the base stack is untouched.

**Fix semantics (physically real):** demo-app's `/break` sets an in-memory flag. A
real `rollout restart` recreates the pod → fresh process → healthy again. So the
restart *physically* fixes it: metrics recover because a real new pod is serving.
The health check then confirms real pod readiness + recovered metrics.

**Full demo flow:** `kind-up.sh` → compose with the overlay → `/break` the in-cluster
demo-app → real Prometheus sees bad metrics → detect → diagnose → console shows the
HITL gate → approve → **real pod restart** → **health checker polls until ready +
metrics recovered** → resolved, for real.

## Testing

TDD, no cluster in tests (injectable clients):

- **`KubernetesRemediator`** — fake `AppsV1Api` recording calls: `restart` patches
  the `restartedAt` annotation on the right deployment; `scale` patches to
  `current + replicas`; `rollback_deploy` calls rollback; an `ApiException` → `False`
  (never raises); `wait` is a no-op.
- **`KubernetesHealthChecker`** — fake `AppsV1Api` (ready/not-ready) + fake metric
  source: both-green → `True`; pod-ready-but-metric-bad → `False`; never-recovers →
  times out `False`; API errors mid-poll swallowed. Deterministic via injected
  clients + small poll interval.
- **`resolve_target(situation)`** — a `demo-app` service label → `{intelliops-demo,
  demo-app}`; an unknown label → a documented default or a clear failure. Pure
  function, trivially tested.
- **`remediate.py` plan construction** — target resolves from the situation's
  `service` label; structured steps thread through; the same target reaches both
  `execute(plan)` and `check(situation, target)`; dry-run path unchanged.
- **Contract change — named blast radius:** `remediate.py` (3 call sites:
  `execute(playbook.steps)` → `execute(plan)`, `health.check(situation)` →
  `check(situation, target)`, `rollback(playbook.rollback_steps)` →
  `rollback(plan)`); `services/action/tests/test_remediator.py` (today passes string
  step lists like `["kubectl rollout restart deploy/web"]` → rewrite to
  `RemediationPlan`); `services/action/tests/test_health.py` (add the `target` arg);
  `services/action/tests/test_remediate.py` (the fakes it uses); the seeded
  `deploy/playbooks/*.yaml`. Update `RecordingRemediator`/`DryRunRemediator`/
  `Always`/`FixedHealthChecker` to the new signatures; assert no behavioral
  regression in the default (dry-run) path.
- **Full suite green** with `REMEDIATOR_MODE=dry_run` / `HEALTH_CHECK_MODE=always`
  defaults — CI needs no cluster; only the fake K8s client is exercised.
- **Real end-to-end (kind + real restart)** is a manual/documented runbook
  (`deploy/k8s/README.md`), not an automated test — an 8-container + cluster
  integration isn't a unit test.

## Concrete change list

**New:**
- `services/action/adapters/k8s_remediator.py` (+ test)
- `services/action/adapters/k8s_health.py` (+ test)
- `deploy/k8s/kind-config.yaml`, `deploy/k8s/demo-app/{deployment,service}.yaml`,
  `deploy/k8s/prometheus/{configmap,deployment,service}.yaml`
- `deploy/k8s/README.md` (runbook)
- `scripts/kind-up.sh`, `scripts/kind-down.sh`
- `deploy/docker-compose.k8s.yml`

**Modified:**
- `common/contracts.py` — `RemediationStep`, `RemediationTarget`, `RemediationPlan`;
  `Playbook.steps` → `list[RemediationStep]`
- `common/interfaces.py` — `Remediator.execute/rollback(plan)`,
  `HealthChecker.check(situation, target)`
- `common/config.py` — `remediator_mode`, `health_check_mode`, `k8s_namespace`,
  service→target default
- `services/action/remediate.py` — build the `RemediationPlan`, resolve target
- `services/action/app.py` — `_make_remediator()` / `_make_health_checker()` switches
- `services/action/adapters/remediator.py`, `health.py` — update Dry-run/Recording/
  Always/Fixed to the new signatures
- `deploy/playbooks/*.yaml` — rewrite steps to structured actions
- `pyproject.toml` — add `kubernetes`
- `flow.md` / `architectural.md` — document the real remediation path; update
  ADR-007's "deferred" note.

## Scope discipline (YAGNI)

One namespace; restart + scale + rollback only (no arbitrary kubectl); demo-app as
the sole workload; kind only (no cloud); the service→target map defaulting to just
demo-app. No Helm, no operator, no multi-cluster — those are Stream D or later.
Real end-to-end is a documented runbook, not CI.
