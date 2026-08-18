# IntelliOps CoE — Team Workplan

**Goal:** take the running dry-run demo to a **production-realistic, genuinely impressive live
system** — the target that earns a PPO. Everyone can speak to a substantial, distinct piece.

**Read first:** [flow.md](flow.md) (how it works) and [architectural.md](architectural.md) (why).
This plan assumes you understand the current state described there: the six-service closed loop +
read-service + React console run live on docker-compose, with remediation **dry-run** today.

---

## How we work (the contract)

- **Fork → branch → PR.** Each member forks this repo, works on their stream's files, and opens
  a PR back here. **Manvik is the integration lead and final tester** — he runs every PR against
  the live stack before merging.
- **Stay in your lane.** Each stream below lists the files/services it **owns**. Owning distinct
  paths is what keeps PRs conflict-free. If you need to touch a *shared* file (contracts, config,
  compose, the bus), see "Shared files" below — coordinate, don't just edit.
- **Every PR must:** keep `uv run pytest` green (add tests for new code — TDD), keep
  `uv run ruff check` clean, keep `npm run build` clean if you touched the frontend, and end each
  commit message with the standard co-author trailer. A PR that reddens the suite doesn't merge.
- **Keep it test-safe by default.** New behavior goes behind an env switch that defaults to the
  current behavior (the ADR-012 pattern) so `pytest` never needs real infra. The compose stack
  turns live bindings on.
- **Definition of done = the acceptance criteria in your stream.** Manvik tests against exactly
  those. If they pass on the live stack, it merges.

### Shared files (coordinate before editing — Manvik owns final say)

`common/contracts.py`, `common/config.py`, `common/bus.py`, `common/interfaces.py`,
`deploy/docker-compose.yml`, `deploy/Dockerfile`, `services/base.py`. Additive changes
(a new defaulted contract field, a new config setting, a new compose service) are usually fine;
say so in your PR. Changing an existing contract field's meaning is a breaking change — raise it
first.

---

## The four streams at a glance

| Stream | Owner | Theme | Depends on |
|--------|-------|-------|------------|
| **A — Real remediation (K8s)** | **Manvik** | Make "resolved" real: a Kubernetes remediator + real health checks acting on a real local cluster. The centerpiece production feature. | none (uses existing seams) |
| **B — Intelligence** | **Member A** | Make the "AI" real: smarter anomaly detection + richer RCA, benchmarked against the current rule-based baseline. | none (self-contained in correlation/rca) |
| **C — Frontend & observability** | **Member B** | Demo-grade console: real-time updates, a live topology/flow view, dashboards worthy of a demo. | read-service API (stable) |
| **D — Platform, security & CI/CD** | **Member C** | Make it deployable and safe: auth on the edge, Kafka binding, CI pipeline, K8s manifests/Helm, load/chaos testing. | none (mostly new files) |

Streams are **parallel** — none blocks another to *start*. The only true dependency is that
Stream A's real remediation is what makes the end-to-end demo fully "real"; B/C/D land
independently around it.

---

## Stream A — Real Kubernetes Remediation  ·  **Manvik** (integration lead)

**Why it's the centerpiece.** Today remediation is dry-run — `DryRunRemediator` logs steps and
an always-healthy check reports success. This stream makes the loop *act on real workloads*: on
a local **kind/minikube** cluster, a real `KubernetesRemediator` restarts/scales/rolls back real
pods, and a real health check verifies the fix by querying pod status. This is the single most
defensible "production engineering" story in the project.

**Owns (new files + the remediation adapter seam):**
- `services/action/adapters/k8s_remediator.py` — `KubernetesRemediator` implementing the existing
  `Remediator` interface (`execute(steps)`, `rollback(steps)`) via the Kubernetes Python client.
- `services/action/adapters/k8s_health.py` — a real `HealthChecker` that queries pod/deployment
  readiness after acting.
- `deploy/k8s/` — kind cluster config, a sample workload to remediate, manifests.
- `scripts/kind-up.sh` / `scripts/kind-down.sh` — spin the cluster + demo workload.
- Wiring in `services/action/app.py`: a `REMEDIATOR_MODE=dry_run|k8s` switch (default `dry_run`)
  selecting the binding — same pattern as `GOVERNANCE_MODE`.
- **Integration duty:** own `common/contracts.py`, `deploy/docker-compose.yml`, and the
  merge/test gate for everyone's PRs.

**Acceptance criteria (Manvik self-verifies + it's the demo climax):**
- With `REMEDIATOR_MODE=k8s` against a kind cluster, breaking the demo workload drives a real
  remediation: the playbook's steps run `kubectl`-equivalent actions, a real pod is
  restarted/scaled, and the health check confirms readiness — outcome `success/healthy` reflects
  a *real* recovery, not a simulated one.
- The rollback path is real: an unhealthy-after-acting result triggers real `rollback_steps` and
  the outcome is `rolled_back`.
- `REMEDIATOR_MODE=dry_run` (default) is unchanged; full `pytest` stays green with a **fake K8s
  client** (no cluster needed in CI).
- README + flow.md updated to show the real-remediation path.

---

## Stream B — Intelligence: Detection & RCA  ·  **Member A**

**Why it matters for a PPO.** Right now detection is a z-score + fixed rules, and RCA is
hand-written heuristics. This stream makes the "AIOps" genuinely intelligent — and, crucially,
**measures the improvement** against the current baseline, which is exactly the kind of
before/after result that impresses in an interview.

**Owns (self-contained in correlation + rca — low conflict):**
- `services/correlation/adapters/` — new/better `Correlator` implementations behind the existing
  interface (e.g. a seasonal/EWMA detector, an isolation-forest or scikit-learn model, or
  multivariate correlation). Keep `RiverCorrelator` as the default; add yours behind a
  `CORRELATOR_KIND` switch.
- `services/rca/rank.py` + `services/rca/adapters/` — richer hypothesis ranking (more evidence
  sources, better scoring, optionally an LLM-assisted explanation behind an interface).
- `services/correlation/retrain` path — actually close the learning loop: consume the
  feedback-service training records and re-fit, and show accuracy improving over time.
- `docs/BENCHMARKS.md` — a reproducible comparison (precision/recall/false-positive-rate, or
  detection latency) of your model vs. the `RiverCorrelator` baseline on a fixed scenario set.

**Acceptance criteria:**
- A new detector is selectable via config, defaults off; `pytest` green with the default.
- `docs/BENCHMARKS.md` shows a **measured** improvement (e.g. fewer false positives, or earlier
  detection) over the baseline on a documented, re-runnable scenario — numbers, not claims.
- RCA produces richer, evidence-backed hypotheses; the top suggestion still resolves to a real
  playbook id so the downstream action path is unaffected.
- The retrain path demonstrably updates the model from real outcomes (a test or a scripted demo
  showing the model learning a signature-plus-fix pair).

---

## Stream C — Frontend & Observability  ·  **Member B**

**Why it matters for a PPO.** The console is what the demo audience *sees*. This stream takes it
from "reads live data on a 5s poll" to "feels like a real production ops product" — real-time
updates, a live view of the pipeline, and dashboards that make the closed loop legible at a
glance.

**Owns (the frontend + read-service read shape — low conflict with backend logic):**
- `frontend/src/` — everything under here except the `data/types.ts` contract (coordinate on
  that one). Real-time updates (SSE or WebSocket instead of polling), a **live pipeline/topology
  view** (telemetry → situation → diagnosis → gate → resolved, animating as events flow), richer
  Overview dashboards, an audit-trail explorer.
- `services/read/app.py` **read endpoints only** — if the UI needs a new read (e.g. a
  per-situation timeline, or an SSE stream of situation updates), add it here. Coordinate with
  Manvik if it needs a new projection field.
- `docs/UI.md` — a short guide to the console for the demo/report (screenshots, what each view
  shows).

**Acceptance criteria:**
- Live updates without a full poll — a new situation or a status change appears in the console
  within ~1s of the event, via SSE/WebSocket (with graceful fallback).
- A live pipeline/topology view that visibly animates an incident moving through the stages
  during a `chaos.sh` run — this is the demo centerpiece for the UI.
- `npm run build` stays clean (strict TS); works in `VITE_DATA_MODE=mock` (no backend) **and**
  `live`. No regression to the existing three views or the empty-state safety.
- New read endpoints (if any) are null-safe and don't touch the write path.

---

## Stream D — Platform, Security & CI/CD  ·  **Member C**

**Why it matters for a PPO.** "It runs on my machine" isn't a production story. This stream makes
IntelliOps *deployable, secure, and continuously verified* — the operational maturity that
signals real engineering.

**Owns (mostly new files — very low conflict):**
- **Auth at the edge** — `services/base.py`-level auth middleware (API-key or JWT bearer) behind
  an `AUTH_MODE=off|token` switch (default `off` so tests/dev are unaffected), protecting the
  read/governance/simulation endpoints. Document which endpoints require auth.
- **CI pipeline** — `.github/workflows/` running `uv run pytest`, `uv run ruff check`, and
  `npm run build` on every PR (this directly supports Manvik's "green suite to merge" gate — it
  automates it).
- **Kafka binding** — a `KafkaBus` implementing `BusClient` behind the existing interface,
  selectable via config (Redis stays the default). Proves ADR-001's "Kafka in prod" claim.
- **K8s deployment** — Helm chart or manifests under `deploy/k8s/platform/` to deploy the *whole
  IntelliOps stack* (not just the demo workload — that's Stream A) onto a cluster.
- **Load & chaos testing** — `scripts/load-test.sh` (drive N incidents/min, show the system keeps
  up) and a documented chaos scenario (kill a service, show the bus/consumer-group recovery).
- `docs/OPERATIONS.md` — how to deploy, configure (all the env switches in one table), and the
  auth model.

**Acceptance criteria:**
- CI runs on PRs and blocks red ones (pytest + ruff + frontend build).
- `AUTH_MODE=token` requires a valid token on protected endpoints and returns 401 without one;
  `AUTH_MODE=off` (default) leaves everything open for dev/tests.
- The Kafka binding passes the same bus contract tests the Redis binding does (config-swapped).
- A documented one-command deploy of the stack to a local cluster.
- A load test showing sustained throughput and a chaos test showing recovery — with numbers in
  `docs/OPERATIONS.md`.

---

## Suggested sequence (2 sprints)

Nothing blocks anyone from *starting*, but this order de-risks integration:

**Sprint 1 — land the foundations in parallel:**
- A: kind cluster + `KubernetesRemediator` behind `REMEDIATOR_MODE` (fake-client tested).
- B: one better detector behind `CORRELATOR_KIND` + the benchmark harness.
- C: real-time updates (SSE/WS) + the live pipeline view.
- D: CI pipeline first (it protects everyone), then auth.

**Sprint 2 — make it production-real and demo-ready:**
- A: real health check + real rollback, wired into the end-to-end demo.
- B: close the retrain loop + publish benchmark numbers.
- C: dashboards + audit explorer + `docs/UI.md`.
- D: Kafka binding + K8s deploy + load/chaos tests + `docs/OPERATIONS.md`.

**Demo target (what we show for the PPO):** break a real workload on the cluster → IntelliOps
detects it with the improved model → diagnoses it → the console shows it animate to the HITL gate
in real time → a human approves → a **real** pod is remediated and verified healthy → live KPIs
update → and it all ran through CI, with auth on, deployable by one command.

---

## PR checklist (paste into every PR description)

```
- [ ] Stays in my stream's owned files (or shared-file change coordinated with Manvik)
- [ ] `uv run pytest` green  ·  `uv run ruff check` clean
- [ ] `npm run build` clean (if frontend touched)
- [ ] New behavior is behind an env switch defaulting to current behavior
- [ ] New code has tests (TDD)
- [ ] Meets my stream's acceptance criteria (list which)
- [ ] Docs updated (flow.md / architectural.md / my stream's doc) if behavior changed
- [ ] Commit messages end with the Co-Authored-By trailer
```
