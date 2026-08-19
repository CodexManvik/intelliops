# Real Kubernetes remediation demo

This is the "real remediation" path: a live kind cluster running the demo-app
and Prometheus, with the action service's remediator flipped from its default
`dry_run` mode to `k8s` mode, so approving a remediation restarts an actual
pod. This path needs a real cluster and is the demo/PPO story — it is **not**
part of CI. Everywhere else (compose without this overlay, tests, CI),
`REMEDIATOR_MODE` defaults to `dry_run` and nothing in a real cluster is ever
touched.

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

## 5. Tear down

```bash
./scripts/kind-down.sh
```

Deletes the kind cluster (same `CLUSTER` env var override as `kind-up.sh`).
Stop the compose stack separately with `docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.k8s.yml down`.

## The honest note

Real pod remediation only happens on this path, against a real kind cluster,
started by hand. It is the demo/PPO story, not a CI-covered path — CI and the
default compose stack never set `INTELLIOPS_REMEDIATOR_MODE=k8s`, so
`REMEDIATOR_MODE` stays `dry_run` (log-only, never touches infrastructure)
everywhere except when you deliberately layer this overlay on top of a
cluster you brought up yourself.
