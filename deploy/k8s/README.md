# Real Kubernetes remediation demo

This is the "real remediation" path: a live kind cluster running the demo-app
and Prometheus, with the action service's remediator flipped from its default
`dry_run` mode to `k8s` mode, so approving a remediation restarts an actual
pod. This path needs a real cluster and is the demo/PPO story — it is **not**
part of CI. Everywhere else (compose without this overlay, tests, CI),
`REMEDIATOR_MODE` defaults to `dry_run` and nothing in a real cluster is ever
touched.

## Probes for a real app-service deployment

The manifests in this directory cover the **demo-app** and **Prometheus** (the workloads this
kind story drives). If you deploy the seven IntelliOps app services themselves
(`ingestion`, `correlation`, `rca`, `action`, `governance`, `feedback`, `read`) to a real
cluster, wire both probes on each Deployment — they map directly to the two endpoints
`create_app` exposes on every service (internal port `8000`):

```yaml
    livenessProbe:
      httpGet: { path: /health, port: 8000 }
    readinessProbe:
      httpGet: { path: /ready, port: 8000 }
```

- **`livenessProbe -> /health`** — `/health` returns `200` whenever the process can serve a
  request and checks nothing external, so a Redis/Postgres outage never triggers a restart loop;
  a non-answering `/health` means the process is wedged and should be restarted.
- **`readinessProbe -> /ready`** — `/ready` actively pings the bus (and Postgres for the
  DB-backed services), returning `200 {"ready":true}` or `503 {"ready":false,"failed":[...]}`.
  Kubernetes pulls a pod out of the Service endpoints while a dependency is unreachable, without
  killing it, and re-adds it on recovery. Both endpoints are auth-exempt, so no token is needed.

The compose stack wires the same `/ready` check as a container `healthcheck` (see
`deploy/docker-compose.yml`). Details and the JSON log schema are in
[docs/OBSERVABILITY.md](../../docs/OBSERVABILITY.md).

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/)
- [kind](https://kind.sigs.k8s.io/) (Kubernetes-in-Docker)
- [kubectl](https://kubernetes.io/docs/tasks/tools/#kubectl)

## 1. Bring up the cluster

```bash
./scripts/kind-up.sh
```

This creates a kind cluster named `intelliops` (override with `CLUSTER=...`),
builds `intelliops-demo-app:local` from the shared `deploy/Dockerfile` and
loads it into the cluster, applies the `intelliops-demo` namespace plus the
`demo-app` and `prometheus` manifests under `deploy/k8s/`, and waits for both
rollouts to finish. When it's done, Prometheus is reachable at
`http://localhost:30090` (kind maps NodePort 30090 to the host — see
`deploy/k8s/kind-config.yaml`).

## 2. Export the kubeconfig (rewritten for the container)

The action service talks to the cluster from *inside* its container, so its
kubeconfig cannot use kind's default `https://127.0.0.1:<port>` server address —
`127.0.0.1` inside a container is the container itself. Instead point it at the
API server by the cert-valid name **`intelliops-control-plane`** (kind's API
cert includes `DNS:intelliops-control-plane` in its SANs, so TLS verification
succeeds — no `insecure-skip-tls-verify` needed), reachable on the `kind` docker
network at the internal port `6443`. Write it to a **repo-local** path (a bare
`/tmp/...` mount is silently turned into an empty *directory* by Docker Desktop
on Windows):

```bash
kind get kubeconfig --name intelliops \
  | sed 's#https://127.0.0.1:[0-9]*#https://intelliops-control-plane:6443#' \
  > deploy/.kubeconfig
```

`deploy/.kubeconfig` is gitignored (local cluster creds). The overlay mounts
`./.kubeconfig` (relative to `deploy/`) into `action` at `/kubeconfig`.

## 3. Start the stack with the k8s overlay

```bash
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.k8s.yml up --build
```

The overlay (`deploy/docker-compose.k8s.yml`) does four things on top of the
base stack:

- Sets `INTELLIOPS_REMEDIATOR_MODE=k8s` and `INTELLIOPS_HEALTH_CHECK_MODE=k8s`
  on `action`, so it drives the real Kubernetes API instead of the dry-run
  adapters.
- Points both `ingestion` and `action` at the in-cluster Prometheus via
  `INTELLIOPS_PROMETHEUS_URL=http://host.docker.internal:30090`, with
  `extra_hosts: host.docker.internal:host-gateway` so containers can reach the
  host's port-mapped NodePort.
- Joins `action` to the external **`kind`** docker network (alongside the
  default one) so it can resolve `intelliops-control-plane` and reach the API
  server with a valid TLS cert.
- Mounts `./.kubeconfig` into `action` at `/kubeconfig` and sets
  `KUBECONFIG=/kubeconfig` so the Kubernetes client picks it up.

Sanity check once it's up — the action container should reach the cluster:

```bash
docker exec intelliops-action-1 python -c \
  "from kubernetes import client, config; config.load_kube_config('/kubeconfig'); \
   print([n.metadata.name for n in client.CoreV1Api().list_namespace().items])"
```

You should see `intelliops-demo` in the printed namespace list.

## 4. Drive the incident

Break the **in-cluster** demo-app — not the `demo-app` container the base
compose stack also runs locally. In `k8s` mode, ingestion scrapes the
in-cluster Prometheus (which only sees the in-cluster demo-app), so that's the
instance to break. The demo-app image is slim and has no `curl`, so drive its
endpoints with Python:

```bash
kubectl -n intelliops-demo exec deploy/demo-app -- \
  python -c "import urllib.request; urllib.request.urlopen(urllib.request.Request('http://localhost:8080/break', method='POST'))"
```

(To recover it later, the same command with `/fix`.)

Then:

1. Watch the console (`http://localhost:5173`, `VITE_DATA_MODE=live`) as the
   stack detects the anomaly and diagnoses it.
2. The situation animates to the HITL gate and waits for a human.
3. Click **Approve**. The action service now calls the real Kubernetes API.
4. Watch the pod actually recreate:
   ```bash
   kubectl -n intelliops-demo get pods -w
   ```
   You'll see the `demo-app` pod terminate and a fresh one come up.
5. The situation resolves once the health check (also running in `k8s` mode)
   confirms the new pod is healthy.

### What each playbook does on the real cluster

RCA picks the playbook from the top hypothesis. Our in-cluster Prometheus
scrapes `cpu_usage`, so the "resource saturation" rule fires and
`scale-service` is selected. Two things worth knowing for the demo:

- **`restart-pod`** is the clean-success path: a real `rollout restart`
  recreates the pod, which clears the demo-app's in-memory `broken` flag (the
  fault lives in the process). The fresh pod reports healthy `cpu_usage`, the
  health check passes, and the outcome is `success / healthy`.
- **`scale-service`** scales the deployment out for real, but scaling does not
  clear the fault on the original pod, and `cpu_usage` is a per-endpoint gauge —
  so the health check may still see it elevated, and the action service then
  **rolls the scale back** and reports `rolled_back`. This is the reversible-only,
  health-verified safety property (ADR-007) working: it undoes its own action
  rather than declaring a false success. To force the clean-success story, drive
  the `restart-pod` path.

### Tier-2 extended-vocabulary actions

In addition to the base playbooks, the action vocabulary includes three **tier-2
remediation actions** that are sandbox-rehearsable and reversible:

- **`patch_resource_limits`** — retunes a container's CPU and memory resource
  ceilings. Applied as a targeted patch to the Deployment's pod spec, not a full
  spec rewrite. The sandbox detects failure modes like OOMKill (out-of-memory
  kill) by observing whether the clone pod reaches `Ready`. Pre-flight runs before
  the human sees an approval prompt.
- **`rollback_to_revision`** — rolls a Deployment's pod template back to a
  specific prior revision, distinct from `rollback_deploy` (which is a
  `rollout restart`). The sandbox validates that the revision exists in the
  Deployment's ReplicaSet history and that the rolled-back clone pod becomes
  `Ready` — this is possible because the sandbox pre-seeds the clone's revision
  history from the production Deployment's ReplicaSets.
- **`patch_probe`** — adjusts a pod's liveness or readiness probe timing:
  failure threshold, initial delay, period, or timeout. A common and effective
  fix for over-aggressive probes killing slow-starting or slow-recovering pods.
  The sandbox rehearsal confirms the probe settings allow the clone pod to
  stabilize and reach `Ready`.

### Actions permanently excluded

The following actions are **never added to the AI-authored vocabulary** because
their failure modes are not observable by pod-readiness checking or their blast
radius is uncontrollable:

- **`delete` (namespace, Deployment, PVC, Secret, or any resource)** — a deleted
  resource cannot be recovered by health-checking the survivors; "no pods running"
  is indistinguishable to the sandbox from a successful scale-down, but it is
  catastrophic in production.
- **`scale_to(N)` / unbounded absolute scale** — similar to delete: scaling to
  zero has the same observable-passing / actually-destructive trap. (Existing
  delta-based scale `+N/-N` remains; absolute `scale_to` is not added.)
- **`exec` or arbitrary command in a pod** — reintroduces the untyped-string
  surface that ADR-008 was written to reject. Sandboxing a command proves only
  that it ran without error in the copy; it says nothing about side effects
  (external API calls, database writes, secrets access).
- **Secret create/patch/read** — either the copy shares real production
  credentials (undermining isolation) or it uses fake ones (the rehearsal proves
  nothing). Secrets require direct human authorization, never AI-authored
  playbooks.
- **Cluster-scoped mutations** (ClusterRole, CRD, admission webhook, or
  namespace-lifecycle operations) — a same-cluster namespace copy cannot
  replicate cluster-scoped state, so rehearsal tests only a fragment of the
  actual blast radius.

### Deferred actions

Two action families are acknowledged as valuable but deferred to a follow-up PR
because they cannot be honestly sandbox-rehearsed in their current form:

- **Node operations (`cordon_node`, `uncordon_node`)** — a node cannot be cloned
  (it is cluster-global state); testing node cordon in a sandbox namespace proves
  nothing about whether pods actually reschedule on a real cluster.
- **HPA patch (`patch_hpa`)** — patching an HPA's min/max/target is partially
  rehearsable (the sandbox can apply the patch), but a single clone pod under
  load *in the sandbox* does not tell you whether the HPA's scaling thresholds
  will react correctly under production's concurrent load. A second PR will
  define a better rehearsal story for HPA patches.

## 5. Tear down

```bash
./scripts/kind-down.sh
```

Deletes the kind cluster (same `CLUSTER` env var override as `kind-up.sh`).
Stop the compose stack separately with `docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.k8s.yml down`.

## 6. Pre-flight sandbox rehearsal (`SANDBOX_MODE=k8s`)

On top of real remediation, the action service can **rehearse a fix before
anyone approves it**. When `sandbox_mode` is `"k8s"`, every remediation —
before the human sees the approval prompt, and before an `auto` playbook is
allowed to execute — clones the target Deployment (plus, best-effort, its
Service and any referenced ConfigMaps) into a throwaway namespace named
`intelliops-sandbox-<8 hex chars>`, waits for that clone's initial rollout,
applies the *exact same* fix to the clone, polls the clone's pod back to
`ready == desired`, tears the namespace down, and attaches a pass/fail
verdict to the outcome. The verdict rides along everywhere the outcome does
and shows up in the incident panel as a `🧪 pre-flight:` row — "rehearsed in
sandbox — passed" or "failed — `<detail>`" — visible **before** the approval
decision is made.

The verdict changes what happens next differently depending on the
playbook's mode:

- **`auto` playbooks:** a failed rehearsal **blocks** — the remediation
  never executes and the outcome comes back `preflight-failed`.
- **`hitl` playbooks:** a failed rehearsal **advises** — it's attached to the
  `ApprovalRequest` so the human sees it, but the human still decides.

When `sandbox_mode` is `"off"` (the default everywhere — base compose,
tests, CI), the sandbox is a no-op (`NullSandbox`): it rehearses nothing and
reports an honest `"not rehearsed (sandbox off)"` verdict, so the base demo
and the existing suite are unaffected.

### Enabling it

`sandbox_mode` is a config-switched setting sourced from the environment the
same way `remediator_mode` is (`common/config.py`, `Settings` with
`env_prefix="INTELLIOPS_"`), so the corresponding environment variable is
`INTELLIOPS_SANDBOX_MODE=k8s`. The overlay (`deploy/docker-compose.k8s.yml`)
sets it on the `action` service alongside `INTELLIOPS_REMEDIATOR_MODE=k8s`
and `INTELLIOPS_HEALTH_CHECK_MODE=k8s` (see step 3 above), so bringing the
overlay up turns rehearsal on for that run. To rehearse without executing the
real fix, drop `INTELLIOPS_REMEDIATOR_MODE`/`INTELLIOPS_HEALTH_CHECK_MODE`
back to their defaults and keep `INTELLIOPS_SANDBOX_MODE=k8s`.

The sandbox needs nothing beyond what real remediation already needs: the
same kubeconfig, the same `kind` docker-network membership, the same RBAC
the action service already has to read/create/delete Deployments — it
additionally creates and deletes its own `intelliops-sandbox-*` namespaces,
so the service account needs namespace create/delete in the cluster you run
this against (the default kind setup grants this).

### The honest limits

- **Denylist gate runs before the sandbox.** Before any remediation plan is
  built or sandboxed, a static denylist gate checks whether a step's shape is
  dangerously misconfigured, independent of runtime outcome. It refuses:
  - `denied:unsafe-scale` — scale delta ≤ -10 (take-down intent heuristic; the
    gate uses this coarse guard because it has no access to current replica
    counts, but this is intentional: if a playbook intends a large negative
    delta, it is flagged regardless of the actual resulting replica count).
  - `denied:unsafe-limits` — CPU limit < 10m or memory limit < 16Mi (resource
    ceilings too small to sustain any meaningful workload).
  - `denied:unsafe-probe` — failure threshold < 1, or probe period/timeout ≤ 0,
    or initial delay < 0 (malformed probe timing that would cause immediate or
    permanent pod restart loops).
  A denied step is blocked outright; it never enters plan-build or sandbox.
- **Revision history seeding is honest-limited to specs, not runtime state.**
  When rehearsing a `rollback_to_revision` step, the sandbox pre-populates the
  clone Deployment with ReplicaSet copies from the production Deployment,
  including their `deployment.kubernetes.io/revision` annotations. This allows
  the Deployment rollback-to-revision machinery to find the target revision. The
  limit: we seed the ReplicaSet *specs* (pod template + revision annotation),
  not the historical pods' runtime state (logs, in-process memory, metric
  timeseries). The pass signal is still pod-readiness of the rolled-back clone
  — the same data-independent signal as other sandbox passes — so the rehearsal
  is honest: it proves *"does the rollback succeed and produce a Ready pod"*, not
  *"does it recover the exact same state the production pod had."* See
  [docs/sandbox-and-ai-runbooks-design-note.md](../../docs/sandbox-and-ai-runbooks-design-note.md)
  for the full rationale.
- **Shared node, not production-isolated.** The clone runs in the *same*
  kind cluster, on the *same* node, as everything else. It proves the fix
  produces a healthy pod under real scheduling and real probes — it does
  **not** prove the fix is safe under production's concurrent load, and it
  cannot catch noisy-neighbor contention between the clone and the real
  workload.
- **Pod readiness is the pass signal, not a metric.** For this PR, `passed`
  is driven entirely by the clone pod reaching `ready == desired` after the
  fix is applied. The health checker's metric predicate is left at its
  default (`lambda: True`) rather than wired to Prometheus, because the
  demo's `cpu_usage` series is keyed per metric name, not per namespace — a
  clone's series isn't reliably distinguishable from production's without a
  per-namespace query, which is deferred to a later PR.
- **A clean-but-wrong action still passes.** The rehearsal catches *does
  the fix work* (crash, OOMKill, a rollback target that doesn't exist, a pod
  that never becomes ready) — it does not catch *is this the right fix* or
  *is this action's blast radius acceptable*. A destructive action that
  completes without error reads as a pass. The sandbox is one gate among
  several (typed action vocabulary, human approval, denylist), never a
  substitute for the others.
- **Not exercised by CI or the test suite.** Like the rest of this page,
  the live `NamespaceCloneSandbox` path only runs against a real kind
  cluster you bring up yourself. `sandbox_mode` defaults to `"off"`
  everywhere else, so CI, the base compose stack, and the ~440-test suite
  never touch a real cluster.

See
[`docs/sandbox-and-ai-runbooks-design-note.md`](../../docs/sandbox-and-ai-runbooks-design-note.md)
for the full rationale — why k8s server-side dry-run doesn't count as a
rehearsal, why a same-cluster namespace clone was chosen over a second
cluster or a dedicated sandbox platform, and what a sandboxed action-pass
does and doesn't tell you.

### Driving it manually

With `INTELLIOPS_SANDBOX_MODE=k8s` set alongside `INTELLIOPS_REMEDIATOR_MODE=k8s`
and the stack up (steps 1–3 above), repeat the incident from step 4 with one
extra thing to watch for between diagnosis and approval:

```bash
kubectl get ns -w
```

After RCA picks a playbook but **before** you click Approve, you should see
an `intelliops-sandbox-<8 hex chars>` namespace appear, hold briefly while
the clone rolls out and the fix is rehearsed against it, and then disappear
again — all while the incident panel already shows the `🧪 pre-flight:` row
with its verdict. Only after that has settled does the approval decision
apply the fix to the real `intelliops-demo` namespace, exactly as in step 4.

## The honest note

Real pod remediation only happens on this path, against a real kind cluster,
started by hand. It is the demo/PPO story, not a CI-covered path — CI and the
default compose stack never set `INTELLIOPS_REMEDIATOR_MODE=k8s`, so
`REMEDIATOR_MODE` stays `dry_run` (log-only, never touches infrastructure)
everywhere except when you deliberately layer this overlay on top of a
cluster you brought up yourself. The same is true of `SANDBOX_MODE=k8s` —
CI and the default compose stack never set it, so `sandbox_mode` stays
`"off"` (no-op, rehearses nothing) everywhere except this deliberate,
by-hand path.

## Real remediation on Meridian

The same kind cluster also runs **Meridian** — the 4-service demo
(`gateway`, `validation`, `aggregation`, `reporting`) that ships its own UI at
`http://localhost:8008`. `./scripts/kind-up.sh` deploys all four Meridian
services alongside `demo-app` and Prometheus, so the flow above extends
directly to a real pod restart triggered from the Meridian Operations panel
instead of `curl`-ing a demo-app endpoint.

### 1. Bring up the cluster

Same command as above — `kind-up.sh` now applies `deploy/k8s/meridian/` too
and waits on all four rollouts:

```bash
./scripts/kind-up.sh
```

Each Meridian Deployment reuses the same `intelliops-demo-app:local` image as
`demo-app` (`SERVICE_MODULE` picks the entrypoint — e.g.
`services.meridian.aggregation.app:app`), so there's no separate image build
step. When it's done you have four in-cluster Meridian pods, each fronted by
a NodePort Service named to match the gateway's fault-routing target
(`meridian-gateway`, `meridian-validation`, `meridian-aggregation`,
`meridian-reporting`):

| Service               | NodePort |
| ---------------------- | -------- |
| `meridian-gateway`     | 30808    |
| `meridian-validation`  | 30811    |
| `meridian-aggregation` | 30812    |
| `meridian-reporting`   | 30813    |

### 2. Export the kubeconfig

Same step as §2 above — one kubeconfig, reused by both the demo-app and
Meridian stories:

```bash
kind get kubeconfig --name intelliops \
  | sed 's#https://127.0.0.1:[0-9]*#https://intelliops-control-plane:6443#' \
  > deploy/.kubeconfig
```

### 3. Start the stack with the k8s overlay

```bash
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.k8s.yml up --build
```

The overlay does everything from §3 above (action → real k8s API, ingestion →
in-cluster Prometheus) **and** flips the Meridian gateway's fault-injection
target: it sets `INTELLIOPS_MERIDIAN_OPS_TARGET_MODE=k8s` on
`meridian-gateway` (plus `host.docker.internal:host-gateway`), so the
compose-hosted gateway — still the one serving the Meridian UI at
`http://localhost:8008` — proxies `/api/ops/fault` and `/api/ops/clear` to
the in-cluster pods' NodePorts (`http://host.docker.internal:<nodeport>`)
instead of compose service DNS. Nothing about the UI or the gateway's own
container changes — only where its Operations panel's fault injection lands.

### 4. Drive the incident

Break a **Meridian** service. Either works — both land on the same in-cluster
pod:

**From the Meridian Operations UI** (`http://localhost:8008` → Operations):
click the **"Aggregation saturated"** preset. In k8s mode this POSTs through
the gateway's ops-proxy to the in-cluster `meridian-aggregation` pod via its
NodePort (30812), not the compose container.

**Or via kubectl**, hitting the pod directly:

```bash
kubectl -n intelliops-demo exec deploy/meridian-aggregation -- \
  python -c "import urllib.request; urllib.request.urlopen(urllib.request.Request('http://localhost:8000/admin/fault', method='POST', data=b'{\"type\":\"saturation\"}', headers={'content-type':'application/json'}))"
```

Then:

1. Watch the console (`http://localhost:5173`, `VITE_DATA_MODE=live`) as it
   detects the anomaly and diagnoses it — same detect → diagnose → gate flow
   as the demo-app story, now fed by real in-cluster Meridian metrics.
2. The situation animates to the HITL gate and waits for a human.
3. Click **Approve**. The action service calls the real Kubernetes API
   against the `meridian-aggregation` Deployment.
4. Watch the real pod act:
   ```bash
   kubectl -n intelliops-demo get pods -w
   ```

### What each playbook does to a Meridian pod

The restart-heals invariant from §4 above holds for Meridian too, for the
same reason: the fault lives in per-process state, not on disk or in an
external store. Meridian's `MeridianState` (`services/meridian/common.py`) is
held in-process by each service, so recreating the process clears it.

- **`restart-pod`** is the clean-success path: `rollout restart` recreates
  the `meridian-aggregation` pod, the fresh process starts with a clean
  `MeridianState` (fault cleared), Prometheus scrapes healthy `cpu_usage` /
  `meridian_error_rate` off the new pod, the health check passes, and the
  outcome is `success / healthy`.
- **`scale-service`** may still roll back — scaling out doesn't touch the
  faulted pod's in-process state, so if the health check still sees it
  degraded, the action service reverses the scale and reports `rolled_back`.
  This is the same reversible-only, health-verified safety property (ADR-007)
  as the demo-app story in §4 — it undoes its own action rather than
  declaring a false success. Drive `restart-pod` for the clean-success demo.

### 5. Tear down

Same as §5 above — `./scripts/kind-down.sh` deletes the whole cluster,
Meridian pods included.
