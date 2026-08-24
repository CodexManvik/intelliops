# Demo Assembly + Auth Coverage — Design

**Date:** 2026-08-22
**Status:** Approved (pending spec review)
**Owner:** Manvik (integration lead) — integration-lead work that no stream owns.
**Depends on:** the auth work (`AUTH_MODE`, `common/auth.py`; documented in `docs/OPERATIONS.md`
— note there is no ADR for edge auth yet, a gap this effort can close with ADR-017), persistence
(Tier 1a/1b), observability (`/ready`), and the K8s remediation demo (`deploy/k8s/README.md`).

## Goal

Turn the pile of shipped Tier-1 features into a **runnable, rehearsed PPO demo**, and close the
one security gap that would embarrass a "production-credible with auth on" claim. One integration
effort, three coordinated pieces, one PR:

1. **Auth coverage** — under `AUTH_MODE=token` the React console is currently **fully broken**
   (it sends no token; every read endpoint is gated → 401). Make the frontend authenticate so
   "auth is on" is a true, demonstrable claim.
2. **DB-aware reset** — `reset.sh` clears in-memory state but leaves Postgres populated, so a
   mid-demo re-run shows stale approvals/baseline. Extend the reset path to clear the
   **runtime-state** tables while **preserving** the audit trail and training records.
3. **DEMO.md** — a two-act walkthrough (compose loop → kind real-remediation) with exact commands,
   reflecting the hardened, auth-on, persistent configuration. Writing it *is* the end-to-end
   integration test nobody has run yet.

## Why this (integration-lead work)

The WORKPLAN defines the demo target but assigns no owner for assembling/scripting/rehearsing it,
and no one has tested that the Tier-1 pieces land together. The auth gap is this integration
lead's code (`common/auth.py` + the frontend). Both fall to the lead by default.

## Part 1: Auth coverage — the frontend sends the token

**The gap (verified):** `services/base.py`'s `_auth_gate` middleware gates every endpoint except
`/health` and `/ready` when `AUTH_MODE=token`. The frontend's `frontend/src/data/api.ts` sends no
`Authorization` header, so under token mode the console's data feed (`/situations`, `/outcomes`,
`/audit`, `/playbooks`, `/approvals`, `/approvals/{id}/decide`) all 401. Compose sets no
`AUTH_MODE`, so it defaults to `off` — which is why the break is hidden today.

**Decision: the frontend attaches the token.** Rejected alternatives: exempting read endpoints
(punches a hole in the exact surface a reviewer probes — audit/situations readable by anyone);
a separate read-only token (two-token scope machinery, over-engineered for a capstone). Sending
the token is the smallest change that keeps the "nothing is reachable without a credential" story
honest, and it matches how the internal services already send Bearer tokens (the PR #6 auth work).

**The change (`frontend/src/data/api.ts`):** a shared header helper used by BOTH fetch sites
(`getJSON` and `decideApproval`):
```typescript
const AUTH_TOKEN = import.meta.env.VITE_AUTH_TOKEN ?? "";

function authHeaders(base: Record<string, string> = {}): Record<string, string> {
  return AUTH_TOKEN ? { ...base, Authorization: `Bearer ${AUTH_TOKEN}` } : base;
}
```
- `getJSON`: `fetch(url, { headers: authHeaders() })`.
- `decideApproval`: `headers: authHeaders({ "content-type": "application/json" })`.
- When `VITE_AUTH_TOKEN` is unset (the default), NO header is sent — so `AUTH_MODE=off` dev/CI is
  unchanged. The token is opt-in, exactly like every other switch.

**Also:** add `readonly VITE_AUTH_TOKEN?: string;` to `frontend/src/vite-env.d.ts` (strict TS
build needs the declaration); add `VITE_AUTH_TOKEN=` to `frontend/.env.example`.

**Read + governance-read endpoints stay gated** — no new server exemptions. The console
authenticates like any client.

**Honest note (documented, not hidden):** a token in a `VITE_` env var is compiled into the
client bundle, so it is not secret from a determined user. Acceptable for a shared-token demo
(`docs/OPERATIONS.md` already frames the token as a shared secret). Documented plainly in DEMO.md
and OPERATIONS.md rather than overclaimed.

## Part 2: DB-aware reset

**The gap:** `scripts/reset.sh` calls demo-app `/fix`, correlation `/reset-baseline`
(in-memory only), and read `/reset`. Under `STORE_BACKEND=postgres`, the `approvals` and
`correlation_baseline` tables survive, so a re-run shows stale pending approvals and a baseline
that reloads on the next restart.

**Decision: reset endpoints truncate their OWN runtime-state table; audit + training are
preserved.** Wiping the audit trail or training records mid-demo would contradict the durable
compliance/learning story — and being able to *show* the audit log still holding prior decisions
after a reset is a feature, not a bug.

**Correlation `/reset-baseline`** (extend the existing endpoint):
```python
@app.post("/reset-baseline")
def reset_baseline() -> dict:
    engine = getattr(app.state, "engine", None)
    if engine is not None:
        engine.reset()                      # in-memory baseline (existing)
    db = getattr(app.state, "db_engine", None)
    if db is not None:                       # postgres mode → clear the durable snapshot too
        with db.begin() as conn:
            conn.execute(text("DELETE FROM correlation_baseline"))
    return {"reset": True}
```

**Governance — new `/reset-approvals`** (governance owns approvals; it has no reset endpoint):
```python
@app.post("/reset-approvals")
def reset_approvals() -> dict:
    db = getattr(app.state, "db_engine", None)
    if db is not None:
        with db.begin() as conn:
            conn.execute(text("DELETE FROM approvals"))
    store = getattr(app.state, "approval_store", None)
    if hasattr(store, "_by_id"):            # in-memory backend: clear it too
        store._by_id.clear()
    return {"reset": True}
```

`DELETE FROM` (not `TRUNCATE`) — runs against the live app connection, needs no table-lock
privilege, row counts are tiny. `audit_records` and `training_records` are deliberately untouched.

**`scripts/reset.sh`** gains one call: `POST $GOV/reset-approvals` (with `GOV=${GOV_URL:-http://localhost:8005}`), alongside the existing three.

**Safety:** these are simulation controls (same category as `/break`, `/fix`, `/reset`,
`/reset-baseline`). Under `AUTH_MODE=token` they are gated like everything else; the docs reiterate
they must be gated/removed against a real system.

## Part 3: DEMO.md — two-act walkthrough

`docs/DEMO.md`, from cold checkout to "a real pod was remediated and verified." Each act is
self-contained; Act 1 needs only Docker, Act 2 adds kind.

**Top matter:** a "what you'll see" table mapping the 6-stage journey (telemetry → situation →
diagnosis → gate → remediation → verified) to where it's visible (console / `kubectl` / `/audit`);
a prerequisites block; the exact env vars each act sets in one place.

**Act 1 — the closed loop on compose (fast, no cluster):**
1. Cold start: `docker compose up --build` with `AUTH_MODE=token` + `STORE_BACKEND=postgres` (the
   hardened, persistent config). Migrate one-shot runs first; services become ready (`/ready`).
2. Console in `live` mode with `VITE_AUTH_TOKEN` set to match — it loads, proving the auth wiring.
3. `./scripts/chaos.sh` — break demo-app, detection in ~15-30s (the honest timing note), Situation
   appears in the console.
4. Approve at the HITL gate in the console → dry-run remediation → outcome → KPIs update.
5. Show durability: `GET /audit` has the decision in Postgres; restart a service, show the
   situation/approval survived (the Tier-1b payoff).
6. `./scripts/reset.sh` — clean detection/approval slate, but note the audit log STILL shows prior
   decisions (audit preserved by design).

**Act 2 — real remediation on a kind cluster (the climax):**
1. `./scripts/kind-up.sh` + export the container-facing kubeconfig — reference
   `deploy/k8s/README.md` for the mechanics (kubeconfig rewrite, the Windows `/tmp` gotcha), don't
   duplicate them.
2. Start with the k8s overlay: `REMEDIATOR_MODE=k8s` + `HEALTH_CHECK_MODE=k8s`.
3. Break the in-cluster workload; same detect → diagnose → HITL flow in the console.
4. Approve → real remediation: `kubectl get pods -w` shows the pod terminate and recreate;
   `cpu_usage` recovers; health check confirms; outcome `success/healthy` — a real recovery.
5. Note the reversible-only rollback property (unhealthy-after-acting → real rollback →
   `rolled_back`), pointing at the `restart-pod` clean-success path.
6. `./scripts/kind-down.sh`.

**Also in DEMO.md:** a troubleshooting section (detection latency is normal; migrate must finish
before services are ready — check `/ready`; console needs the matching token), and the honest
notes (dry-run vs real; simulation controls must be gated in prod; the frontend token isn't secret
from the bundle).

**Cross-references, not duplication:** DEMO.md is the narrative; `deploy/k8s/README.md` is the k8s
reference; `docs/OPERATIONS.md` is the env-switch/auth reference. Link, don't repeat.

## Verification (this IS the integration test)

Writing DEMO.md requires running the stack. **Act 1 is run end-to-end by me**: `docker compose up`
with `AUTH_MODE=token` + `STORE_BACKEND=postgres`, console loads with the token (and 401s without
it — proving enforcement), chaos → approve → verify audit persisted → reset leaves audit intact.
Every command in Act 1 is confirmed to work. Act 2 (kind) is verified as far as the environment
allows: the overlay compose config validates and the documented commands match the already-proven
`deploy/k8s/README.md`.

Plus the usual gates: `uv run pytest` green (new tests for the two reset endpoints), `ruff check` +
`ruff format --check` clean, `npm run build` clean (the frontend change is TS-strict), `docker
compose config` valid.

## Concrete change list

**New:** `docs/DEMO.md`; tests for `/reset-approvals` + the extended `/reset-baseline` DB path.

**Modified:** `frontend/src/data/api.ts` (auth header helper), `frontend/src/vite-env.d.ts`
(`VITE_AUTH_TOKEN`), `frontend/.env.example` (`VITE_AUTH_TOKEN=`); `services/correlation/app.py`
(`/reset-baseline` DB delete), `services/governance/app.py` (`/reset-approvals`);
`scripts/reset.sh` (call `/reset-approvals`); `docs/OPERATIONS.md` (note the frontend token +
that reads are gated under token mode); `flow.md` (update the §8 auth note — the console now
authenticates; reset clears runtime-state tables); `README.md` (link DEMO.md); ADR-017 in
`architectural.md` if the auth-coverage decision warrants a record (the "frontend authenticates,
reads stay gated" boundary is a real decision).

## Scope discipline (YAGNI)

No new demo *tooling* beyond the one endpoint + the `reset.sh` line + the frontend token — DEMO.md
orchestrates existing scripts. No screenshots/video (separate effort). No `demo.sh` mega-script (a
live demo is narrated command-by-command; a script that hides steps is worse). No read-only token,
no per-endpoint auth scopes. Just: the console authenticates, reset is DB-aware, and the whole
thing is written down and run.
