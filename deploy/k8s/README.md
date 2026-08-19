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

## 2. Export the kubeconfig

The action service needs a kubeconfig to talk to the cluster from inside its
container:

```bash
kind get kubeconfig --name intelliops > /tmp/intelliops.kubeconfig
```

(`deploy/docker-compose.k8s.yml` mounts this exact path into the `action`
container read-only at `/kubeconfig`.)

## 3. Start the stack with the k8s overlay

```bash
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.k8s.yml up --build
```

The overlay (`deploy/docker-compose.k8s.yml`) does three things on top of the
base stack:

- Sets `INTELLIOPS_REMEDIATOR_MODE=k8s` and `INTELLIOPS_HEALTH_CHECK_MODE=k8s`
  on `action`, so it drives the real Kubernetes API instead of the dry-run
  adapters.
- Points both `ingestion` and `action` at the in-cluster Prometheus via
  `INTELLIOPS_PROMETHEUS_URL=http://host.docker.internal:30090`, with
  `extra_hosts: host.docker.internal:host-gateway` so containers can reach the
  host's port-mapped NodePort.
- Mounts `/tmp/intelliops.kubeconfig` into `action` at `/kubeconfig` and sets
  `KUBECONFIG=/kubeconfig` so the Kubernetes client picks it up.

## 4. Drive the incident

Break the **in-cluster** demo-app — not the `demo-app` container the base
compose stack also runs locally. In `k8s` mode, ingestion scrapes the
in-cluster Prometheus (which only sees the in-cluster demo-app), so that's the
instance to break:

```bash
kubectl -n intelliops-demo exec deploy/demo-app -- curl -s -X POST localhost:8080/break
```

(Alternatively, `kubectl -n intelliops-demo port-forward deploy/demo-app 8080:8080`
in one terminal and `curl -X POST localhost:8080/break` in another.)

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
