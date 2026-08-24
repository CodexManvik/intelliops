# Demo Assembly + Auth Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the console work under `AUTH_MODE=token`, make `reset` clear Postgres runtime-state (preserving audit + training), and write a two-act `DEMO.md` whose end-to-end run is the integration test.

**Architecture:** Three coordinated pieces — a frontend auth-header helper, two DB-aware reset endpoints, and a demo doc. Small code surface; the value is the end-to-end run.

**Tech Stack:** React/TypeScript (Vite), FastAPI, SQLAlchemy Core, bash, docker-compose, kind.

**Spec:** `docs/superpowers/specs/2026-08-22-demo-assembly-auth-coverage-design.md`

## Global Constraints

- **Default = current behavior.** The frontend token is opt-in (`VITE_AUTH_TOKEN` unset → no header, `AUTH_MODE=off` unchanged). The reset DB-delete only fires when a `db_engine` is present (postgres mode); file/in-memory mode is unchanged.
- **Audit + training are NEVER wiped by reset** — only `approvals` and `correlation_baseline`. Wiping the compliance/learning record would contradict the durable-state story.
- **Reset endpoints are simulation controls** (same category as `/break`, `/fix`, `/reset`, `/reset-baseline`) — gated under `AUTH_MODE=token`, documented as non-production.
- **`text` must be imported** (`from sqlalchemy import text`) in `correlation/app.py` and `governance/app.py` — it is NOT currently imported in either.
- **Gates:** `uv run pytest` green, `ruff check` + `ruff format --check .` clean, `npm run build` clean (frontend is TS-strict — a new env var needs a `vite-env.d.ts` declaration).
- **Git identity:** commits authored `CodexManvik <manviktalwar.official@gmail.com>`; messages end with the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer.

## Interfaces (exact, referenced across tasks)

- Frontend: `authHeaders(base?) -> Record<string,string>` in `frontend/src/data/api.ts`; env `VITE_AUTH_TOKEN`.
- `POST /reset-baseline` (correlation) — now also `DELETE FROM correlation_baseline` in postgres mode.
- `POST /reset-approvals` (governance, NEW) — `DELETE FROM approvals` + clears the in-memory store.

---

## Task 1: Frontend sends the auth token

**Files:**
- Modify: `frontend/src/data/api.ts`, `frontend/src/vite-env.d.ts`, `frontend/.env.example`

**Interfaces:**
- Produces: every read fetch + the decide POST attach `Authorization: Bearer <VITE_AUTH_TOKEN>` when the token is set.

- [ ] **Step 1: Add the env var declaration + example**

In `frontend/src/vite-env.d.ts`, add to the `ImportMetaEnv` interface (alongside the existing `VITE_READ_URL?` etc.):
```typescript
  readonly VITE_AUTH_TOKEN?: string;
```

In `frontend/.env.example`, add a line:
```
VITE_AUTH_TOKEN=
```

- [ ] **Step 2: Add the auth-header helper + apply it to both fetch sites**

In `frontend/src/data/api.ts`, after the `READ`/`GOV` consts:
```typescript
const AUTH_TOKEN = import.meta.env.VITE_AUTH_TOKEN ?? "";

function authHeaders(base: Record<string, string> = {}): Record<string, string> {
  return AUTH_TOKEN ? { ...base, Authorization: `Bearer ${AUTH_TOKEN}` } : base;
}
```
Change `getJSON`:
```typescript
async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url, { headers: authHeaders() });
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return (await r.json()) as T;
}
```
Change `decideApproval`'s fetch headers:
```typescript
    headers: authHeaders({ "content-type": "application/json" }),
```
(Leave the method/body unchanged.)

- [ ] **Step 3: Verify the frontend builds (TS-strict)**

Run: `cd frontend && npm run build`
Expected: clean build (the new `VITE_AUTH_TOKEN?` declaration satisfies strict TS; no other type errors).

- [ ] **Step 4: Verify behavior by inspection**

Confirm: with `VITE_AUTH_TOKEN` unset, `authHeaders()` returns `{}` (or just the base) → no `Authorization` header → `AUTH_MODE=off` unaffected. With it set, every `getJSON` call and the decide POST carry the Bearer token.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/data/api.ts frontend/src/vite-env.d.ts frontend/.env.example
git commit -m "feat(frontend): attach VITE_AUTH_TOKEN so the console works under AUTH_MODE=token"
```

---

## Task 2: DB-aware reset endpoints

**Files:**
- Modify: `services/correlation/app.py`, `services/governance/app.py`
- Test: `services/correlation/tests/` (extend), `services/governance/tests/` (new test)

**Interfaces:**
- Consumes: `app.state.db_engine` (present in postgres mode, from the observability work), `app.state.approval_store`.
- Produces: `/reset-baseline` clears the DB baseline too; `/reset-approvals` (new) clears approvals.

- [ ] **Step 1: Write the failing tests**

Correlation — add to `services/correlation/tests/test_reset.py` a test that the endpoint deletes from the DB when a `db_engine` is present. Use a fake engine that records the executed SQL:
```python
def test_reset_baseline_deletes_db_rows_in_postgres_mode():
    from fastapi.testclient import TestClient
    from services.correlation.app import app

    executed = []

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, stmt): executed.append(str(stmt))

    class _Engine:
        def begin(self): return _Conn()

    app.state.db_engine = _Engine()
    # app.state.engine may be unset in this unit context; the endpoint guards it.
    c = TestClient(app)
    r = c.post("/reset-baseline")
    assert r.status_code == 200 and r.json() == {"reset": True}
    assert any("correlation_baseline" in s.lower() for s in executed)
    del app.state.db_engine
```

Governance — new `services/governance/tests/test_reset_approvals.py`:
```python
from fastapi.testclient import TestClient
from common.contracts import ApprovalRequest
from services.governance.adapters.approval_store import InMemoryApprovalStore


def _client():
    from services.governance.app import app
    app.state.approval_store = InMemoryApprovalStore()
    return app, TestClient(app)


def test_reset_approvals_clears_in_memory_store():
    app, c = _client()
    app.state.approval_store.create(
        ApprovalRequest(id="a1", situation_id="s1", playbook_id="restart-pod",
                        requested_by="action-service")
    )
    assert app.state.approval_store.get("a1") is not None
    r = c.post("/reset-approvals")
    assert r.status_code == 200 and r.json() == {"reset": True}
    assert app.state.approval_store.get("a1") is None


def test_reset_approvals_deletes_db_rows_in_postgres_mode():
    app, c = _client()
    executed = []

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, stmt): executed.append(str(stmt))

    class _Engine:
        def begin(self): return _Conn()

    app.state.db_engine = _Engine()
    c.post("/reset-approvals")
    assert any("approvals" in s.lower() for s in executed)
    del app.state.db_engine
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest services/correlation/tests/test_reset.py services/governance/tests/test_reset_approvals.py -v`
Expected: FAIL (correlation: no DB delete; governance: no `/reset-approvals`).

- [ ] **Step 3: Extend correlation `/reset-baseline`**

In `services/correlation/app.py`, add `from sqlalchemy import text` to the imports. Change the endpoint:
```python
@app.post("/reset-baseline")
def reset_baseline() -> dict:
    engine = getattr(app.state, "engine", None)
    if engine is not None:
        engine.reset()
    db = getattr(app.state, "db_engine", None)
    if db is not None:
        with db.begin() as conn:
            conn.execute(text("DELETE FROM correlation_baseline"))
    return {"reset": True}
```

- [ ] **Step 4: Add governance `/reset-approvals`**

In `services/governance/app.py`, add `from sqlalchemy import text` to the imports. Add the endpoint (near the other `/approvals` routes):
```python
@app.post("/reset-approvals")
def reset_approvals() -> dict:
    db = getattr(app.state, "db_engine", None)
    if db is not None:
        with db.begin() as conn:
            conn.execute(text("DELETE FROM approvals"))
    store = getattr(app.state, "approval_store", None)
    if hasattr(store, "_by_id"):
        store._by_id.clear()
    return {"reset": True}
```

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest services/correlation/tests/ services/governance/tests/ -v`
Expected: PASS (new tests + existing service tests unaffected).

- [ ] **Step 6: Commit**

```bash
git add services/correlation/app.py services/governance/app.py services/correlation/tests/test_reset.py services/governance/tests/test_reset_approvals.py
git commit -m "feat(reset): /reset-baseline + /reset-approvals clear runtime-state DB tables"
```

---

## Task 3: reset.sh + the end-to-end run + DEMO.md

**Files:**
- Modify: `scripts/reset.sh`
- Create: `docs/DEMO.md`
- Verification: run Act 1 end-to-end

- [ ] **Step 1: Extend `scripts/reset.sh`**

Add a `GOV` URL var (`GOV=${GOV_URL:-http://localhost:8005}`) alongside the existing ones, and a step calling `/reset-approvals`:
```bash
echo "→ Clearing pending approvals…"
curl -fsS -X POST "$GOV/reset-approvals" >/dev/null && echo "  approvals cleared"
```
Place it before or after the baseline reset — order doesn't matter. Keep the existing three steps.

- [ ] **Step 2: Run Act 1 end-to-end (THE integration test)**

Bring up the stack with the hardened config and confirm every command in Act 1 works. This is the real deliverable of the task — do NOT write DEMO.md's Act 1 as steps you haven't run.

- `docker compose -f deploy/docker-compose.yml up -d --build` with `INTELLIOPS_AUTH_MODE=token` + `INTELLIOPS_AUTH_TOKEN=<demo-token>` + `INTELLIOPS_STORE_BACKEND=postgres` set (via a compose override or env). Confirm migrate runs and services reach `/ready`.
- Confirm the console (or a curl with the token) can read `/situations` etc. WITH the token and gets 401 WITHOUT it — proving auth is enforced and the token works.
- Drive `./scripts/chaos.sh`; confirm a Situation appears.
- Approve it (via the console, or `POST /approvals/{id}/decide` with the token + an authorized actor); confirm the outcome + KPIs.
- Confirm `GET /audit` (with token) shows the decision persisted.
- Run `./scripts/reset.sh`; confirm approvals + baseline cleared but `GET /audit` STILL shows the prior decision (audit preserved).

Record the EXACT commands that worked (ports, env, token) — those become DEMO.md verbatim.

- [ ] **Step 3: Write `docs/DEMO.md`**

Two acts, per the spec. Use ONLY commands verified in Step 2 for Act 1. Structure:
- Top: a "what you'll see" table (6-stage journey → where visible); prerequisites (Docker for Act 1, +kind +kubectl for Act 2); the env vars each act sets.
- **Act 1 — compose loop** (cold start with AUTH_MODE=token + STORE_BACKEND=postgres → console with token → chaos → approve → durability/audit → reset). Verbatim from Step 2.
- **Act 2 — kind real remediation** (kind-up + kubeconfig → k8s overlay → break in-cluster → approve → real pod restart via `kubectl get pods -w` → verified). Reference `deploy/k8s/README.md` for cluster mechanics; do not duplicate.
- Troubleshooting (detection latency normal; migrate before ready — check `/ready`; console needs the matching token).
- Honest notes (dry-run vs real; simulation controls gated in prod; frontend token baked into the bundle).

- [ ] **Step 4: Tear down the stack**

`docker compose -f deploy/docker-compose.yml down` (and `-v` if you want a clean volume). Leave no running containers.

- [ ] **Step 5: Commit**

```bash
git add scripts/reset.sh docs/DEMO.md
git commit -m "feat(demo): DB-aware reset.sh + two-act DEMO.md (verified Act 1 end-to-end)"
```

---

## Task 4: Docs — OPERATIONS, flow, README, ADR-017

**Files:**
- Modify: `docs/OPERATIONS.md`, `flow.md`, `README.md`, `architectural.md`

- [ ] **Step 1: OPERATIONS.md**

Add: the frontend authenticates via `VITE_AUTH_TOKEN` under `AUTH_MODE=token` (set it to match `INTELLIOPS_AUTH_TOKEN`); the console token is baked into the client bundle (shared-token demo, not a per-user secret); read + governance-read endpoints are gated under token mode (no public read surface).

- [ ] **Step 2: flow.md**

Update the §8 auth note: it currently says "No auth on the read/console endpoints." Correct it — under `AUTH_MODE=token` the console authenticates with a shared token and read endpoints ARE gated; the reset/break/fix endpoints remain simulation controls (gated under token mode, must be removed/gated against a real system). Note `/reset-baseline` + the new `/reset-approvals` clear the runtime-state DB tables (approvals, baseline) while preserving audit + training.

- [ ] **Step 3: README.md**

Add a link to `docs/DEMO.md` in the Documentation map and/or the Quickstart ("For a full guided demo, see DEMO.md").

- [ ] **Step 4: ADR-017 — Edge authentication model**

Add `### ADR-017 — Edge authentication` to `architectural.md` after ADR-016 (verified last is 016). This fills a real gap — the `AUTH_MODE` edge auth (shipped in the auth PR) has no ADR. Match the existing prose-ADR format. Capture: the decision (`AUTH_MODE=off|token`, timing-safe bearer at the edge via `common/auth.py`, wired in `create_app`; `/health` + `/ready` always exempt); internal service-to-service calls authenticate (don't bypass); the frontend authenticates with the same shared token (reads stay gated — no public surface); the honest limits (shared token, baked into the frontend bundle — a real deployment would use per-user tokens / an IdP). Update the §6 built/deferred lists.

- [ ] **Step 5: Full verification + commit**

- `uv run pytest -q -m "not postgres"` green; `uv run pytest -q` (Docker) green; `ruff check` + `ruff format --check .` clean; `cd frontend && npm run build` clean.
```bash
git add docs/OPERATIONS.md flow.md README.md architectural.md
git commit -m "docs: auth-coverage + reset semantics; ADR-017 (edge auth); link DEMO.md"
```

---

## Self-Review

**1. Spec coverage:** frontend token (T1 ✓); reset endpoints incl. audit-preservation (T2 ✓);
reset.sh + end-to-end run + DEMO.md two-act (T3 ✓); OPERATIONS/flow/README + ADR-017 (T4 ✓). All
three spec parts + the ADR gap covered.

**2. Placeholder scan:** every code step has real code (the authHeaders helper, both reset
endpoints, the fake-engine tests, the reset.sh line). Act 1 of DEMO.md is explicitly "verbatim
from the verified Step 2 run" — not invented steps. Act 2 references the already-proven k8s README
rather than fabricating cluster commands.

**3. Type/consistency:** `authHeaders(base?)` used by both `getJSON` and `decideApproval` — same
signature. `text` imported in both service files (T2) before use. The reset response `{reset: True}`
is identical across both endpoints and their tests. `app.state.db_engine` (the guard) matches how
the observability work set it. `VITE_AUTH_TOKEN` declared in `vite-env.d.ts` (T1) so the TS-strict
build (T1 Step 3, T4 Step 5) passes. ADR-017 numbering matches (last is 016, verified).
