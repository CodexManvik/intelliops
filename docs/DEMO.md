# IntelliOps — Demo Walkthrough

A guided, two-act demo from a cold checkout to "a real pod was remediated and verified." Act 1
runs the whole closed loop on docker-compose with **auth on** and **Postgres persistence** — no
cluster needed. Act 2 swaps in a real kind cluster so approving a fix restarts a **real pod**.

Act 1's commands below were run end-to-end and verified; use them verbatim.

## What you'll see

| Stage | What happens | Where you see it |
|-------|--------------|------------------|
| Telemetry | Prometheus scrapes the demo-app; ingestion normalizes it onto the bus | (background) |
| Situation | Correlation collapses the anomaly into one `Situation` | console · `GET /situations` |
| Diagnosis | RCA attaches a hypothesis + a suggested playbook | console · the situation's `hypotheses` |
| HITL gate | Action requests approval; nothing runs until a human decides | console · `GET /approvals` |
| Remediation | On approve, the playbook runs (dry-run in Act 1, **real pod** in Act 2) | console · `kubectl` (Act 2) |
| Verified | A health check confirms recovery; the decision is written to the audit trail | `GET /audit` (persisted in Postgres) |

## Prerequisites

- **Act 1:** Docker + Docker Compose, Node (for the console), `curl` + `python` (for the CLI checks).
- **Act 2 (adds):** [kind](https://kind.sigs.k8s.io/) + `kubectl`.

---

## Act 1 — the closed loop on compose (fast, no cluster)

This runs the hardened configuration: **`AUTH_MODE=token`** (edge auth on) and
**`STORE_BACKEND=postgres`** (durable state). The base compose already sets `STORE_BACKEND=postgres`;
the `deploy/docker-compose.auth.yml` overlay turns auth on with a shared demo token
(`intelliops-demo-token`).

### 1. Bring up the stack

```bash
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.auth.yml up -d --build
```

Postgres and Redis come up first (health-gated), the one-shot `migrate` job applies the schema and
exits, then the seven services start. Give them ~20s to connect, then confirm readiness (the
`/health` liveness and `/ready` readiness probes are always reachable without a token):

```bash
curl -s -o /dev/null -w "governance /ready -> %{http_code}\n" http://localhost:8005/ready
curl -s -o /dev/null -w "read       /ready -> %{http_code}\n" http://localhost:8007/ready
```

Both return `200`.

### 2. Prove auth is enforced

Set the token once:

```bash
export TOKEN=intelliops-demo-token
```

Without it, the console's data endpoints are locked:

```bash
curl -s -o /dev/null -w "situations (no token)    -> %{http_code}\n" http://localhost:8007/situations
curl -s -o /dev/null -w "situations (wrong token) -> %{http_code}\n" -H "Authorization: Bearer nope" http://localhost:8007/situations
```

Both return `401`. With the correct token they return `200`:

```bash
curl -s -o /dev/null -w "situations (token) -> %{http_code}\n" -H "Authorization: Bearer $TOKEN" http://localhost:8007/situations
```

This is why the console must send the token — see step 3.

### 3. Start the console (authenticated)

```bash
cd frontend
cp .env.example .env.local
# edit .env.local:
#   VITE_DATA_MODE=live
#   VITE_AUTH_TOKEN=intelliops-demo-token
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). It loads because the frontend now attaches
`Authorization: Bearer <VITE_AUTH_TOKEN>` on every request (without the token set, the console would
get 401s — that's the auth-coverage fix).

### 4. Drive an incident

```bash
curl -s -X POST http://localhost:8080/break
```

Detection takes **~15-30 seconds** — that's expected, not a hang: a real Prometheus scrape (every
5s) + an ingestion poll (every 5s) + River needing a few samples to flag the anomaly. Watch the
console, or poll:

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8007/situations | python -m json.tool
```

The `Situation` appears with `status: diagnosed` and a hypothesis (e.g. "resource saturation" ->
`scale-service`), then an approval shows up at the HITL gate:

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8005/approvals | python -m json.tool
```

### 5. Approve the fix

In the console, click **Approve** on the situation. (From the CLI, the equivalent — note the
authorized decider and the token:)

```bash
APPR=appr-<situation-id>   # from the /approvals response above
curl -s -H "Authorization: Bearer $TOKEN" -H "content-type: application/json" \
  -X POST "http://localhost:8005/approvals/$APPR/decide" \
  -d '{"decision":"approved","decided_by":"oncall-alice"}'
```

Action runs the (dry-run) remediation, publishes the outcome, and the console's KPIs update.

### 6. Show durability

Every decision is written to the **Postgres**-backed audit trail, threaded by `correlation_id`:

```bash
curl -s -H "Authorization: Bearer $TOKEN" "http://localhost:8005/audit?correlation_id=<situation-id>" | python -m json.tool
```

You'll see the `rca-service diagnose` and `action-service execute` records — persisted, queryable,
and surviving a service restart (that's the Tier-1b durability payoff).

### 7. Reset for a clean re-run

```bash
AUTH_TOKEN=$TOKEN ./scripts/reset.sh
```

This recovers the demo-app, clears the detector baseline + read-model, and drops pending approvals —
**but the audit trail and training records are preserved** (they're the compliance/learning record).
Confirm:

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8005/audit | python -c "import sys,json; print(len(json.load(sys.stdin)), 'audit records still here')"
```

The prior run's decisions are still there. Break it again for a fresh incident.

### Tear down

```bash
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.auth.yml down
# add -v to also drop the Postgres volume for a truly fresh database
```

---

## Act 2 — real remediation on a kind cluster (the climax)

The same loop, but approving restarts a **real pod** and a **real health check** verifies recovery.
This is the PPO centerpiece. The cluster mechanics (kubeconfig rewrite, the Windows `/tmp` gotcha,
the network join) are covered in **[deploy/k8s/README.md](../deploy/k8s/README.md)** — follow it for
the exact setup; the narrative below is the demo flow.

### 1. Bring up the cluster

```bash
./scripts/kind-up.sh
```

Creates the `intelliops` kind cluster, builds + loads the demo-app image, applies the demo
namespace + Prometheus, and waits for the rollouts. Then export the container-facing kubeconfig
(the exact `sed` rewrite is in the k8s README — the in-container kubeconfig can't use kind's
`127.0.0.1` server address).

### 2. Start the stack with the k8s overlay

```bash
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.k8s.yml up --build
```

The overlay sets `REMEDIATOR_MODE=k8s` + `HEALTH_CHECK_MODE=k8s` on the action service, points
ingestion + action at the in-cluster Prometheus, joins action to the `kind` network, and mounts the
kubeconfig. (You can layer the auth overlay from Act 1 on top too, if you want auth on for this run.)

### 3. Break the in-cluster workload

Break the **in-cluster** demo-app (not the compose one) — in `k8s` mode ingestion scrapes the
in-cluster Prometheus:

```bash
kubectl -n intelliops-demo exec deploy/demo-app -- \
  python -c "import urllib.request; urllib.request.urlopen(urllib.request.Request('http://localhost:8080/break', method='POST'))"
```

### 4. Approve → watch a real pod remediate

Watch the console detect and diagnose it, then **Approve** at the HITL gate. In another terminal:

```bash
kubectl -n intelliops-demo get pods -w
```

You'll see the `demo-app` pod terminate and a fresh one come up — a real `rollout restart`. The
in-cluster `cpu_usage` recovers, the health check (also in `k8s` mode) confirms, and the outcome is
`success / healthy` — a **real** recovery, not a simulated one.

> **The reversible-only safety property (ADR-007):** if the fix doesn't restore health, the action
> service runs the real `rollback_steps` and reports `rolled_back` rather than a false success. The
> `restart-pod` playbook is the clean-success path; `scale-service` may roll back (scaling doesn't
> clear the in-process fault). See [deploy/k8s/README.md](../deploy/k8s/README.md) for which
> playbook does what on the real cluster.

### 5. Tear down

```bash
./scripts/kind-down.sh
# stop the compose stack separately:
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.k8s.yml down
```

---

## Troubleshooting

- **Detection takes 15-30s.** Expected — real scrape + poll intervals + River warm-up, not a hang.
- **A service isn't answering.** Check `/ready` (not just `/health`): `/health` says the process is
  up, `/ready` says it can reach Redis + Postgres (503 with a `failed` list until it can). The
  `migrate` job must finish before the store services are ready.
- **The console shows 401s / no data.** Under `AUTH_MODE=token` the console must send the token —
  set `VITE_AUTH_TOKEN` in `frontend/.env.local` to match `INTELLIOPS_AUTH_TOKEN`.
- **`reset.sh` returns 401.** Under token mode the reset endpoints are gated — run it with
  `AUTH_TOKEN=<token> ./scripts/reset.sh`.

## Honest notes

- **Dry-run vs real.** Act 1's remediation is dry-run (logged + a simulated health check) — nothing
  real is touched. Real pod remediation only happens on Act 2's kind path.
- **Simulation controls.** `/break`, `/fix`, `/reset`, `/reset-baseline`, `/reset-approvals` are
  simulation controls, not production endpoints. Under `AUTH_MODE=token` they're gated like
  everything else, and they must be removed or gated when pointed at a real system.
- **The demo token isn't a real secret.** `VITE_AUTH_TOKEN` is compiled into the frontend bundle, so
  it's a shared demo token, not a per-user credential. A real deployment would use per-user tokens
  or an identity provider — see [docs/OPERATIONS.md](OPERATIONS.md).
