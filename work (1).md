# Stream D — CI fixes + Auth-at-the-edge: handoff doc

**Repo:** https://github.com/CodexManvik/intelliops
**Owner:** Member C, Stream D (Platform, Security & CI/CD)
**Base branch:** `master`
**Status:** implemented and verified locally (228/228 tests pass, `ruff check` clean). Not yet pushed/PR'd.

This doc has everything needed to recreate the changes and open the PR:
context, what changed and why, full file contents, and the PR checklist
from the team's WORKPLAN. Hand this to a coding agent (Antigravity or
otherwise) and it can apply every file verbatim.

---

## 1. Context

The team runs a fork → branch → PR workflow (see WORKPLAN.md in the repo
root). Manvik is integration lead and final tester. Four parallel streams;
this work is entirely Stream D (mine — Member C), which owns:
`.github/workflows/`, `common/*` (shared, coordinate on changes),
`services/base.py` (shared), auth middleware, Kafka binding, K8s deploy,
load/chaos testing, `docs/OPERATIONS.md`.

### 1a. Where this started: a PR review

I opened a CI pipeline PR (`.github/workflows/ci.yml` + `build.yml`)
against an Aug-14 base. Manvik reviewed it against current master (which
had since grown a read-service, a React console, a demo-app, and
Prometheus) and asked for two fixes before merge:

1. **Blocking:** `uv run ruff format --check .` was in the lint job, but
   the repo has never run `ruff format` — it currently reports 71 files
   would be reformatted. Merging as-is would red-wall CI for everyone,
   permanently, starting with this PR. **Fix: drop that line.** `ruff
   check .` (the linter, not the formatter) already passes and stays.
2. **Non-blocking:** `compose-smoke` only checked ports 8001–8006. The
   stack had grown three more services since the PR was opened: read
   (8007), demo-app (8080), prometheus (9090). The smoke test was passing
   without ever checking read-service — which the whole dashboard depends
   on. **Fix: add 8007 to the health loop; 8080 as a bonus.** Also
   flagged: image build takes ~130s in CI and demo-app starts slowly, so
   the original 10×3s=30s wait budget per port was tight.

I also independently found and closed a real gap while doing this: CI had
no frontend job at all, so `npm run build` — which is explicitly one of
Stream D's own acceptance criteria ("CI runs on PRs and blocks red ones:
pytest + ruff + frontend build") — was never actually enforced. Added a
`frontend-build` job.

### 1b. Then: the actual acceptance criteria

Stream D's full acceptance criteria (from WORKPLAN.md) are:

- [x] CI runs on PRs and blocks red ones (pytest + ruff + frontend build)
- [x] `AUTH_MODE=token` requires a valid token on protected endpoints and
      returns 401 without one; `AUTH_MODE=off` (default) leaves everything
      open for dev/tests
- [ ] Kafka binding passes the same bus contract tests as Redis (not done —
      see §4)
- [ ] Documented one-command K8s deploy (not done — see §4)
- [ ] Load test + chaos test with numbers in `docs/OPERATIONS.md` (not
      done — see §4)

This handoff covers the first two, fully implemented and tested. The last
three are explicitly out of scope for this PR (see §4 for what they'd
need).

---

## 2. What changed and why

### 2.1 `.github/workflows/ci.yml`

- Removed `uv run ruff format --check .` (review comment 1 — the repo's
  never run the formatter; this line would have broken CI for everyone on
  merge).
- Extended the compose-smoke port loop from `8001-8006` to
  `8001 8002 8003 8004 8005 8006 8007 8080` (review comment 2 — covers
  read-service and demo-app).
- Bumped the per-port wait budget from 10×3s (30s) to 20×3s (60s), per
  Manvik's flake warning about demo-app's slow startup.
- Added a `frontend-build` job (Node 20, `npm ci` + `npm run build`),
  running in parallel with `lint`/`test`. `compose-smoke` now depends on
  `[test, frontend-build]` instead of just `test`, so a broken frontend
  build blocks merge the same way a broken pytest run does. This satisfies
  Stream D's own acceptance criterion, which the original PR missed.

`build.yml` (the GHCR publish workflow) needed **no changes** — Manvik's
review already confirmed it was clean (pinned action versions, correct
permissions, sensible tags, no file collision).

### 2.2 Auth at the edge (`AUTH_MODE=off|token`)

Design goals, in order: (a) match the existing env-switch pattern already
used in the repo (`GOVERNANCE_MODE`, `TELEMETRY_MODE`, `CORRELATOR_KIND`
— an additive Settings field, default = current behavior, so
tests/dev/CI are unaffected); (b) protect exactly what WORKPLAN says
("read/governance/simulation endpoints"), not more; (c) never gate
`/health`, in any mode, because compose healthchecks, k8s probes, and
CI's own compose-smoke job hit it without a token.

**`common/config.py`** — two new `Settings` fields:
`auth_mode: str = "off"` and `auth_token: str = ""`.

**`common/auth.py`** (new) — the shared check. `is_authorized(request,
settings)` returns `True` immediately if `auth_mode != "token"`; otherwise
it requires `Authorization: Bearer <auth_token>` to match exactly.
`require_token` is a FastAPI dependency wrapper around the same check, for
services that need to gate specific routes rather than the whole app.

**`services/base.py`** — the six core services (ingestion, correlation,
rca, action, governance, feedback, read — everything built via the shared
`create_app()` factory) get an `@app.middleware("http")` gate that calls
`is_authorized` on every request except `path == "/health"`. Since it's
one middleware in the shared factory, this covers governance's and read's
protected endpoints (audit, playbooks, approvals, situations, outcomes,
etc.) without touching each route individually, and it covers
correlation's `/reset-baseline` simulation control too.

**`services/demo_app/app.py`** — demo-app doesn't use the shared factory
(it's explicitly "the real running app the demo observes," not an
IntelliOps service), so it's gated per-route instead:
`Depends(require_token)` on `/break` and `/fix` only (the simulation
controls). `/health` stays open, `/metrics` stays open (Prometheus scrapes
it with no auth header — confirmed against `deploy/prometheus.yml`), and
`/work` stays open (it's simulated *application* traffic, not a control).

**`docs/OPERATIONS.md`** (new) — documents the `AUTH_MODE` table and
exactly which endpoints are gated per service, per WORKPLAN's explicit
"document which endpoints require auth" requirement. Notes the rest of
this doc (Kafka, K8s, load/chaos) is TODO as those pieces land.

**Tests** — `tests/test_auth.py` (new, 5 tests) exercises the shared
middleware directly against `create_app()`: off-mode is fully open,
`/health` stays open in token mode, missing/wrong/correct tokens behave
as expected. `services/demo_app/tests/test_demo_app.py` (+3 tests): the
existing 3 tests are untouched; added coverage that `/break`/`/fix` are
open by default, gated in token mode, and that `/health`/`/metrics`/`/work`
stay open even in token mode.

### 2.3 Verification

```
$ uv run ruff check common/auth.py common/config.py services/base.py \
    services/demo_app/app.py tests/test_auth.py \
    services/demo_app/tests/test_demo_app.py
All checks passed!

$ uv run pytest -q
228 passed, 1 warning in 2.75s
```

(228 vs. the 220 Manvik verified pre-review — the 8 new auth tests. The
one warning is a pre-existing `httpx`/starlette deprecation notice,
unrelated to this change.)

---

## 3. Full file contents

Apply these verbatim. Three are brand new files; four are edits to
existing files (shown in full, not as a diff, so they can be written
directly).

### 3.1 `.github/workflows/ci.yml` (edited — full file)

```yaml
name: CI
on:
  push:
    branches: [master]
  pull_request:

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          python-version: "3.11"
          enable-cache: true
          cache-dependency-glob: uv.lock
      - run: uv sync --frozen
      - run: uv run ruff check .

  test:
    runs-on: ubuntu-latest
    needs: lint
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          python-version: "3.11"
          enable-cache: true
          cache-dependency-glob: uv.lock
      - run: uv sync --frozen
      - run: uv run pytest -v

  frontend-build:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: frontend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - run: npm ci
      - run: npm run build

  compose-smoke:
    runs-on: ubuntu-latest
    needs: [test, frontend-build]
    steps:
      - uses: actions/checkout@v4
      - run: docker compose -f deploy/docker-compose.yml up -d --build
      - name: Wait for all services to become healthy
        run: |
          for port in 8001 8002 8003 8004 8005 8006 8007 8080; do
            for attempt in $(seq 1 20); do
              if curl -sf http://localhost:$port/health; then
                echo " ✓ port $port healthy"
                break
              fi
              if [ "$attempt" -eq 20 ]; then
                echo " ✗ port $port never became healthy"
                exit 1
              fi
              sleep 3
            done
          done
      - if: failure()
        run: docker compose -f deploy/docker-compose.yml logs
      - if: always()
        run: docker compose -f deploy/docker-compose.yml down
```

### 3.2 `common/config.py` (edited — full file)

```python
"""Runtime configuration, sourced from environment variables."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INTELLIOPS_", env_file=".env")

    redis_url: str = "redis://localhost:6379"
    audit_store_path: str = "data/audit.jsonl"
    playbook_store_path: str = "data/playbooks"
    rbac_policy_path: str = "policies/rbac_policy.yaml"
    rca_context_path: str = "data/rca_context"
    hitl_poll_timeout_seconds: float = 30.0
    hitl_poll_interval_seconds: float = 0.5
    training_store_path: str = "data/training.jsonl"
    reliability_suppress_threshold: float = 0.8
    graduation_min_successes: int = 3

    # --- live-stack settings (test-safe defaults) ---
    telemetry_mode: str = "file"  # "file" | "prometheus"
    prometheus_url: str = "http://localhost:9090"
    # A gauge query: cpu_usage keeps its __name__ (so the source maps a real
    # metric name, not "unknown") and its labels (job/service), and it spikes
    # when the demo target breaks. A rate() query would strip __name__, which
    # leaves correlation with a nameless, label-less series it cannot classify.
    prometheus_query: str = "cpu_usage"
    telemetry_poll_seconds: float = 5.0
    # Correlation tuning. Defaults preserve production behavior (a long warm-up
    # so a cold service doesn't emit spurious anomalies); a live demo overrides
    # these via env to detect an injected incident within a minute or two.
    correlation_warmup_samples: int = 50
    correlation_z_threshold: float = 3.0
    correlation_window_seconds: float = 30.0
    governance_mode: str = "in_process"  # "in_process" | "http"
    governance_url: str = "http://localhost:8005"
    read_outcomes_max: int = 200
    read_situation_ttl_seconds: float = 600.0
    read_situations_max: int = 50
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # Auth at the edge (ADR: deferred item in architectural.md §6). "off"
    # (default) preserves today's open behavior so tests/dev are unaffected;
    # "token" requires a matching bearer token on every non-/health request.
    auth_mode: str = "off"  # "off" | "token"
    auth_token: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

### 3.3 `common/auth.py` (new file)

```python
"""Shared bearer-token auth for the edge, behind AUTH_MODE.

AUTH_MODE=off (default): every endpoint stays open — current dev/test
behavior, unchanged.
AUTH_MODE=token: a request must carry `Authorization: Bearer
<INTELLIOPS_AUTH_TOKEN>` to reach a protected endpoint; a missing or
mismatched token gets 401.

/health is exempt in every service, in every mode (see services/base.py),
so container healthchecks, k8s liveness/readiness probes, and the CI
compose-smoke job keep working without a token. demo-app's /metrics
(scraped by Prometheus, unauthenticated) and /work (simulated app
traffic, not a control) are exempt the same way — see services/demo_app/app.py.
Only the endpoints named in WORKPLAN.md (read, governance, and the
/break /fix /reset /reset-baseline simulation controls) are gated.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from common.config import Settings, get_settings


def _token_from_header(request: Request) -> str:
    header = request.headers.get("authorization", "")
    prefix = "Bearer "
    return header[len(prefix) :].strip() if header.startswith(prefix) else ""


def is_authorized(request: Request, settings: Settings) -> bool:
    """True if AUTH_MODE is off, or the request carries a valid token."""
    if settings.auth_mode != "token":
        return True
    token = _token_from_header(request)
    return bool(settings.auth_token) and token == settings.auth_token


def require_token(request: Request) -> None:
    """FastAPI dependency: gate a single route regardless of its path.

    Use this (rather than the app-wide middleware in services/base.py) for
    services that mix protected and open routes on one app, e.g. demo-app's
    /break and /fix.
    """
    settings = get_settings()
    if not is_authorized(request, settings):
        raise HTTPException(status_code=401, detail="Unauthorized")
```

### 3.4 `services/base.py` (edited — full file)

```python
"""Shared FastAPI app factory for all six services.

At skeleton stage every service is identical: a /health endpoint and a bus
client on app.state. Service-specific handlers arrive in later slices.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from common.auth import is_authorized
from common.bus import make_bus
from common.config import get_settings


def create_app(service_name: str) -> FastAPI:
    app = FastAPI(title=f"IntelliOps · {service_name}")
    settings = get_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.bus = make_bus(settings)

    # Auth at the edge (AUTH_MODE=off|token). /health is always exempt so
    # compose/k8s healthchecks never need a token, in any mode.
    @app.middleware("http")
    async def _auth_gate(request: Request, call_next):
        if request.url.path != "/health" and not is_authorized(request, settings):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"service": service_name, "status": "ok"}

    return app
```

### 3.5 `services/demo_app/app.py` (edited — full file)

```python
"""A tiny breakable target that emits Prometheus metrics.

The operator flips /break to simulate an incident (error rate + CPU spike) and
/fix to recover. IntelliOps scrapes these metrics via Prometheus. This is the
'real running app' the live demo observes — nothing here depends on IntelliOps.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest

from common.auth import require_token

app = FastAPI(title="IntelliOps · demo-app")

_state: dict[str, bool] = {"broken": False}

_requests = Counter("http_requests_total", "Total work requests")
_errors = Counter("http_request_errors_total", "Total failed work requests")
_cpu = Gauge("cpu_usage", "Simulated CPU utilization percent")

_CPU_HEALTHY = 18.0
_CPU_BROKEN = 92.0


@app.get("/health")
def health() -> dict[str, str]:
    return {"service": "demo-app", "status": "ok"}


@app.get("/work")
def work() -> dict[str, str]:
    _requests.inc()
    if _state["broken"]:
        _errors.inc()
        raise HTTPException(status_code=500, detail="dependency failure")
    return {"result": "ok"}


@app.post("/break", dependencies=[Depends(require_token)])
def break_it() -> dict[str, bool]:
    _state["broken"] = True
    _cpu.set(_CPU_BROKEN)
    return {"broken": True}


@app.post("/fix", dependencies=[Depends(require_token)])
def fix_it() -> dict[str, bool]:
    _state["broken"] = False
    _cpu.set(_CPU_HEALTHY)
    return {"broken": False}


@app.get("/metrics")
def metrics() -> Response:
    # keep the gauge fresh even if neither toggle was hit this scrape
    _cpu.set(_CPU_BROKEN if _state["broken"] else _CPU_HEALTHY)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
```

### 3.6 `tests/test_auth.py` (new file)

```python
from fastapi.testclient import TestClient

from common.config import get_settings
from services.base import create_app


def _client(monkeypatch, mode: str, token: str = "") -> TestClient:
    monkeypatch.setenv("INTELLIOPS_AUTH_MODE", mode)
    monkeypatch.setenv("INTELLIOPS_AUTH_TOKEN", token)
    get_settings.cache_clear()
    return TestClient(create_app("test-service"))


def _clear_cache():
    get_settings.cache_clear()


def test_off_mode_leaves_everything_open(monkeypatch):
    client = _client(monkeypatch, "off")
    assert client.get("/health").status_code == 200
    # off mode gates nothing, even paths that don't exist on this app --
    # they should 404 from routing, not 401 from the auth gate.
    assert client.get("/situations").status_code == 404
    _clear_cache()


def test_health_open_even_in_token_mode(monkeypatch):
    client = _client(monkeypatch, "token", "secret")
    assert client.get("/health").status_code == 200
    _clear_cache()


def test_token_mode_blocks_missing_token(monkeypatch):
    client = _client(monkeypatch, "token", "secret")
    assert client.get("/situations").status_code == 401
    _clear_cache()


def test_token_mode_blocks_wrong_token(monkeypatch):
    client = _client(monkeypatch, "token", "secret")
    r = client.get("/situations", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    _clear_cache()


def test_token_mode_allows_correct_token(monkeypatch):
    client = _client(monkeypatch, "token", "secret")
    r = client.get("/situations", headers={"Authorization": "Bearer secret"})
    # Auth passes; this app has no /situations route, so routing 404s --
    # the point is it's a 404, not a 401.
    assert r.status_code == 404
    _clear_cache()
```

### 3.7 `services/demo_app/tests/test_demo_app.py` (edited — full file)

```python
# services/demo_app/tests/test_demo_app.py
from fastapi.testclient import TestClient

from common.config import get_settings
from services.demo_app.app import _state, app


def _client():
    _state["broken"] = False
    return TestClient(app)


def test_work_ok_when_healthy():
    c = _client()
    assert c.get("/work").status_code == 200


def test_break_makes_work_error_and_cpu_spike():
    c = _client()
    c.post("/break")
    assert c.get("/work").status_code == 500
    body = c.get("/metrics").text
    assert "cpu_usage" in body
    # cpu gauge should read high (>= 80) when broken
    line = next(l for l in body.splitlines() if l.startswith("cpu_usage "))
    assert float(line.split()[1]) >= 80.0


def test_fix_recovers():
    c = _client()
    c.post("/break")
    c.post("/fix")
    assert c.get("/work").status_code == 200


def test_break_and_fix_open_by_default(monkeypatch):
    monkeypatch.setenv("INTELLIOPS_AUTH_MODE", "off")
    get_settings.cache_clear()
    c = _client()
    assert c.post("/break").status_code == 200
    assert c.post("/fix").status_code == 200
    get_settings.cache_clear()


def test_break_and_fix_gated_in_token_mode(monkeypatch):
    monkeypatch.setenv("INTELLIOPS_AUTH_MODE", "token")
    monkeypatch.setenv("INTELLIOPS_AUTH_TOKEN", "secret")
    get_settings.cache_clear()
    c = _client()
    assert c.post("/break").status_code == 401
    r = c.post("/break", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    get_settings.cache_clear()


def test_health_and_metrics_stay_open_in_token_mode(monkeypatch):
    monkeypatch.setenv("INTELLIOPS_AUTH_MODE", "token")
    monkeypatch.setenv("INTELLIOPS_AUTH_TOKEN", "secret")
    get_settings.cache_clear()
    c = _client()
    assert c.get("/health").status_code == 200
    assert c.get("/metrics").status_code == 200
    assert c.get("/work").status_code == 200
    get_settings.cache_clear()
```

### 3.8 `docs/OPERATIONS.md` (new file)

```markdown
# Operations

Stream D (platform, security, CI/CD) owns this doc. Sections beyond auth
(Kafka binding, K8s deploy, load/chaos numbers) land as those pieces ship.

## Auth at the edge

Controlled by `INTELLIOPS_AUTH_MODE`:

| Value | Behavior |
| --- | --- |
| `off` (default) | Every endpoint open. Current dev/test/CI behavior, unchanged. |
| `token` | Every request except `/health` (and demo-app's `/metrics`, `/work`) must carry `Authorization: Bearer <INTELLIOPS_AUTH_TOKEN>`, or the service returns `401`. |

Set `INTELLIOPS_AUTH_TOKEN` to the shared token when `AUTH_MODE=token`. A
service started in `token` mode with no `AUTH_TOKEN` set rejects every
protected request — there's no accidental-open fallback.

`/health` is exempt in every mode, on every service, so docker-compose
healthchecks, k8s liveness/readiness probes, and CI's compose-smoke job
never need a token.

### What's gated

Applied via the shared app factory (`services/base.py`), so it covers every
route on ingestion, correlation, rca, action, governance, feedback, and
read — except `/health`. In practice the endpoints this actually protects
are the ones with real external read/write surface:

- **read-service** — `/situations`, `/outcomes`, `/metrics`, `/reset`
- **governance-service** — `/audit`, `/playbooks`, `/rbac/check`, `/approvals`
- **correlation-service** — `/reset-baseline` (simulation control)

demo-app doesn't use the shared factory (it's an external target, not an
IntelliOps service), so it's gated per-route instead: `/break` and `/fix`
(simulation controls) require the token in `token` mode; `/health`,
`/metrics` (scraped by Prometheus, unauthenticated), and `/work`
(simulated app traffic) stay open.

### Not yet covered

RBAC inside governance-service (who can approve what) is unrelated to this
and already existed — this only gates network access to the HTTP surface.
```

---

## 4. Explicitly not done in this PR

Don't let a coding agent silently invent these — they're real, separate
pieces of work:

- **Kafka binding** (`common/bus.py` needs a `KafkaBus` implementing the
  existing `BusClient` interface, selectable via config, tested against
  the same contract tests Redis passes). Not started.
- **K8s deployment** (Helm chart / manifests under `deploy/k8s/platform/`
  for the whole stack — this is separate from Stream A's `deploy/k8s/`
  demo-workload manifests). Not started.
- **Load & chaos testing** (`scripts/load-test.sh`, a documented chaos
  scenario, numbers written into `docs/OPERATIONS.md`). Not started.
- **`ruff format`** was intentionally *not* adopted repo-wide — the
  review flagged 71 files would reformat. That's a separate PR (one
  commit, `ruff format .`, before any formatter gate goes live), not
  bundled into this one.

---

## 5. PR checklist (from WORKPLAN.md — paste into the PR description)

```
- [x] Stays in my stream's owned files (or shared-file change coordinated with Manvik)
- [x] `uv run pytest` green  ·  `uv run ruff check` clean
- [x] `npm run build` clean (n/a — no frontend files touched)
- [x] New behavior is behind an env switch defaulting to current behavior
- [x] New code has tests (TDD)
- [x] Meets my stream's acceptance criteria (list which): CI pipeline
      (pytest + ruff + frontend build gate); AUTH_MODE=off|token with 401
      enforcement
- [x] Docs updated: docs/OPERATIONS.md (new)
- [ ] Commit messages end with the Co-Authored-By trailer — add before pushing
```

**Shared files touched:** `common/config.py` (additive — two new Settings
fields, default-safe) and `services/base.py` (additive — one new
middleware, default-safe). Per WORKPLAN, additive changes to shared files
are fine to note in the PR rather than needing prior sign-off — call this
out explicitly in the PR description since Manvik owns final say on
shared files.

**Suggested branch name:** `stream-d/ci-fixes-and-auth`

**Suggested commit message:**

```
Stream D: fix CI review comments, add frontend-build gate, add auth at the edge

- Drop `ruff format --check` from lint (repo has never run the formatter;
  71 files would fail — this would red-wall CI on merge)
- Extend compose-smoke health loop to cover read (8007) and demo-app (8080),
  bump wait budget 30s -> 60s per port for demo-app's slower startup
- Add frontend-build job (npm ci + npm run build), gate compose-smoke on it
- Add AUTH_MODE=off|token with bearer-token auth on read/governance/
  simulation-control endpoints, /health always exempt
- docs/OPERATIONS.md: document the auth model and what's gated

Co-Authored-By: <fill in per team convention>
```
