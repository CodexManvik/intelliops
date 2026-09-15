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
This is the PPO centerpiece, and it is a **single self-contained cluster** — all seven services,
the Meridian sample production system, demo-app, Postgres, Redis, Prometheus and the React console
run *inside* kind. There is no compose stack and no kubeconfig plumbing in this path.

> Every command below was run end-to-end against a cluster built from
> `kind delete cluster` on 2026-09-15; the timings quoted are what it actually did.

### 1. Bring the whole platform up

```bash
GROQ_API_KEY=gsk_... ./scripts/kind-up-full.sh
```

That builds the three images, loads them into kind, installs the chart with the **LIVE** posture
(`values-live.yaml`: `robust` correlation, `embedding` runbook selection, **real k8s remediation**,
per-metric health verification, at-least-once delivery), and wires your key into a Secret. Without
`GROQ_API_KEY` the stack still comes up and LLM explanations fall back to template.

A cold run takes a while: it pulls a CPU torch wheel, bakes the embedding model, and pulls
`postgres:16`/`redis:7`/`prom/prometheus` from Docker Hub. The script now **fails loudly** if any
workload never becomes ready rather than printing a checkmark over a broken stack.

When it finishes:

| What | URL |
|---|---|
| Console (the live UI) | http://localhost:30080 |
| Read service | http://localhost:30007 |
| Meridian gateway (inject faults here) | http://localhost:30808 |
| Prometheus | http://localhost:30090 |

Confirm it is genuinely up — the banner is not proof on its own:

```bash
kubectl get pods
curl -s http://localhost:30007/system | python -m json.tool
```

`/system` asks each service what it is *actually* running, so `remediator_mode` reading `k8s` is the
cluster's own answer, not the read service's environment.

### 2. Break a real workload

Meridian's four services are the fault source. The gateway is the one bound to a host port; reach
the others through any pod. `crash` drives `service_up` 1 → 0, which is the cleanest story because
the fix is a pod restart you can watch:

```bash
kubectl exec deploy/read -- python -c "import httpx; print(httpx.post('http://meridian-validation:8000/admin/fault', json={'type':'crash','magnitude':1.0}, timeout=10).text)"
```

Watch the target in another terminal:

```bash
kubectl get pods -l app.kubernetes.io/name=meridian-validation -w
```

### 3. Detect → diagnose → approve

The console shows the incident appear, then a hypothesis and a suggested runbook. From the CLI:

```bash
curl -s http://localhost:30007/situations | python -m json.tool
```

Measured across three verified runs: the Situation appeared **17-30s** after injection (Prometheus
scrape + ingestion poll + correlation window), RCA attached `service is down — the process stopped
serving` → `restart-pod` **8-14s** later once the correlator is warm (the first incident after a
cold start took ~90s while baselines filled), and the approval request followed in **under a
second**. Budget ~45s from injection to the HITL gate, and do not narrate it as instant.

Nothing runs until a human decides. Approve in the console, or:

```bash
kubectl exec deploy/read -- python -c "import httpx; print(httpx.post('http://governance:8000/approvals/appr-<situation-id>/decide', json={'decision':'approved','decided_by':'oncall-alice'}, timeout=20).status_code)"
```

### 4. Watch a real pod remediate, and a real check verify it

The `kubectl get pods -w` window shows the pod **terminate and a new one come up** — a real
`rollout restart`, about **5s** after approval. The pod *name changes*, which is the proof this is
not dry-run. Then:

```bash
curl -s http://localhost:30007/outcomes | python -m json.tool
```

The outcome carries `"mode": "k8s"` — a real cluster was touched — and `"result": "success"` once
the per-metric check confirms `service_up` is back to its baseline **for that service**. The
verification predicate converges in about **8s**.

> **The reversible-only safety property (ADR-007):** if health is not restored, the action service
> runs the real `rollback_steps` and reports `rolled_back` rather than a false success.

### 5. The gap path — an incident with no runbook

The more interesting half. `unknown_signal` moves `tls_handshake_failures`, a metric family **no RCA
rule matches**:

```bash
kubectl exec deploy/read -- python -c "import httpx; print(httpx.post('http://meridian-reporting:8000/admin/fault', json={'type':'unknown_signal','magnitude':1.0}, timeout=10).text)"
```

It is still detected — `tls_handshake_failures` has a perfectly flat baseline, and the `robust`
correlator handles zero-variance windows — but no runbook is invented for it. The incident reaches
**needs_attention**, and the console offers **"Draft a runbook with AI"**: the governance agent
reads the incident plus past outcomes, calls tools, and proposes a `ProposedPlaybook` for a human to
approve. Nothing it drafts is auto-applied.

### 6. Reset between runs

**Do this before demoing.** The action service consumes diagnosed incidents serially and waits
inline for a human decision, so an incident left undecided blocks later ones until it times out
(`HITL_POLL_TIMEOUT_SECONDS`, 120s in the live posture). Clear the slate:

```bash
for s in meridian-gateway meridian-validation meridian-reporting meridian-aggregation; do
  kubectl exec deploy/read -- python -c "import httpx; httpx.post('http://$s:8000/admin/clear', json={}, timeout=10)"
done
curl -s -X POST http://localhost:30007/reset
kubectl exec deploy/read -- python -c "import httpx; [httpx.post(u, json={}, timeout=15) for u in ['http://governance:8000/reset-approvals','http://governance:8000/reset-proposed']]"
```

### 7. Tear down

```bash
kind delete cluster --name intelliops
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
