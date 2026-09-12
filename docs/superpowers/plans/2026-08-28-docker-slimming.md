# Docker Image-Slimming Implementation Plan (PR B)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Slim the non-ML/non-k8s service images from ~1.5GB toward ~300–400MB by fixing a numpy/river import leak, moving heavy deps into uv extras, splitting the Dockerfile into a slim `base` stage + a `full` stage, targeting the right stage per compose service, and adding a verification gate that proves no service silently lost a dependency.

**Architecture:** The heavy libs (numpy/scipy/scikit-learn/river/joblib ≈ ML; kubernetes ≈ k8s) are needed only by `correlation` and `action`, but a package-`__init__` side effect currently leaks numpy+river into 4 other services via `common/stores.py`. Fix the leak first (lazy correlator imports — the file already does this for the `trained` kind), then move the heavy deps into `[project.optional-dependencies]` extras, build a multi-stage Dockerfile (`base` = 12 common deps for 11 services; `full` = base + ml + k8s for correlation/action), and wire compose `target:` per service. Verify with an import-boundary test + grep-lints + a full 13-service compose smoke on `/ready`.

**Tech Stack:** Python 3.12 + uv (astral, virtual project — no `[build-system]`) + Docker multi-stage + docker-compose + GitHub Actions. Backend gates: `uv run pytest -m "not postgres and not kafka"`, `ruff check .`, `ruff format --check .`.

**Spec:** `docs/superpowers/specs/2026-08-28-docker-slimming-design.md`

## Global Constraints

- **No behavior change.** Detection/RCA/governance/remediation logic unchanged. `correlation` + `action` keep every dep they use. The leak fix is import-ordering only.
- **No version changes.** The dependency union (base ∪ ml ∪ k8s) equals today's flat set — `uv.lock` regeneration is metadata-only.
- **Lockfile discipline.** After editing `pyproject.toml`, run `uv lock` (NOT `--frozen`) once and commit the regenerated `uv.lock`. Then `uv sync --frozen` (Docker/CI) works. If the lock diff shows a version change, STOP — something is wrong (expected: only which section each of the 5 relocated packages belongs to).
- **Gates:** `uv run pytest -m "not postgres and not kafka"` green (~427); `ruff check .` + `ruff format --check .` clean. (Docker builds + compose smoke are verified in the final task, not per-task, since they need Docker.)
- **Commit trailer** on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **git identity:** CodexManvik. Branch `refactor/docker-slimming` (off master). Do NOT merge/push to master — the user does that.
- **Windows/git-bash** dev shell; CI runs on ubuntu-latest. The import-boundary test's Linux invocation is for CI; a local Windows equivalent is noted where relevant.
- **Shared files (coordinate):** `pyproject.toml`, `uv.lock`, `deploy/Dockerfile`, `deploy/docker-compose.yml`, `services/correlation/adapters/__init__.py`, `.github/workflows/ci.yml`.

---

## Task 1: Fix the numpy/river leak (lazy correlator imports)

**Files:**
- Modify: `services/correlation/adapters/__init__.py:7-9,15-42`
- Test: `services/correlation/tests/test_import_boundary.py` (new)

**Interfaces:**
- Produces: `make_correlator(settings)` unchanged in signature/behavior, but `RiverCorrelator`/`RobustCorrelator` are now imported lazily inside its branches (matching the existing `trained` pattern), so importing the `services.correlation.adapters` package no longer pulls numpy/river.

**Root cause:** `__init__.py:8-9` eagerly imports `RiverCorrelator` + `RobustCorrelator` (top-level `import numpy`, `from river import stats`). `common/stores.py` imports `services.correlation.adapters.baseline_store`/`model_store`, which runs this `__init__.py` → numpy+river load into action/governance/feedback/rca. `TrainedCorrelator` is ALREADY lazy here (lines 30-41).

- [ ] **Step 1: Write the failing import-boundary test**

Create `services/correlation/tests/test_import_boundary.py`. This test imports `common.stores` in a subprocess with the heavy modules blocked, proving `make_stores`'s dependency chain no longer needs numpy/river. Use a subprocess so the already-imported modules in the test process don't mask the result:

```python
"""Guards the dependency-isolation boundary: importing common.stores (which the
non-ML services do via make_stores) must NOT pull in numpy/river/sklearn. The
leak was services/correlation/adapters/__init__.py eagerly importing the
River/Robust correlators (numpy+river at module scope)."""

from __future__ import annotations

import subprocess
import sys


def test_common_stores_does_not_import_heavy_deps():
    # Run in a fresh subprocess: import common.stores, then assert none of the
    # heavy ML deps landed in sys.modules. A subprocess is required — the pytest
    # process itself has numpy/river already loaded from other tests.
    code = (
        "import importlib, sys; "
        "importlib.import_module('common.stores'); "
        "leaked = {'numpy', 'river', 'sklearn'} & set(sys.modules); "
        "assert not leaked, f'common.stores leaked heavy deps: {leaked}'; "
        "print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"import-boundary violated:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest services/correlation/tests/test_import_boundary.py -v`
Expected: FAIL — `common.stores` currently leaks numpy+river (via the adapters `__init__`).

- [ ] **Step 3: Make the correlator imports lazy**

Replace `services/correlation/adapters/__init__.py` entirely with:

```python
"""Correlation adapters: concrete Correlator implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from services.correlation.adapters.base_correlator import BaseCorrelator

if TYPE_CHECKING:
    from common.config import Settings


def make_correlator(settings: Settings) -> BaseCorrelator:
    """Build the configured Correlator implementation from settings.correlator_kind.

    Correlator classes are imported lazily so importing this package (which
    common/stores.py does transitively via baseline_store/model_store) never
    pulls in numpy/river/sklearn for services that only need the SQLAlchemy
    stores. Only the kind actually selected loads its heavy dependency.
    """
    kind = settings.correlator_kind
    if kind == "river":
        from services.correlation.adapters.river_correlator import RiverCorrelator

        return RiverCorrelator(
            z_threshold=settings.correlation_z_threshold,
            warmup_samples=settings.correlation_warmup_samples,
        )
    if kind == "robust":
        from services.correlation.adapters.robust_correlator import RobustCorrelator

        return RobustCorrelator(
            z_threshold=settings.correlation_z_threshold,
            warmup_samples=settings.correlation_robust_warmup,
            seasonal_buckets=settings.correlation_seasonal_buckets,
            window_size=settings.correlation_robust_window,
        )
    if kind == "trained":
        from services.correlation.adapters.trained_correlator import TrainedCorrelator

        return TrainedCorrelator(
            z_threshold=settings.correlation_z_threshold,
            warmup_samples=settings.correlation_robust_warmup,
            seasonal_buckets=settings.correlation_seasonal_buckets,
            window_size=settings.correlation_robust_window,
        )
    raise ValueError(f"Unknown CORRELATOR_KIND: {kind!r}")
```

(Only change vs today: the `RiverCorrelator`/`RobustCorrelator` imports move from module scope into the `river`/`robust` branches. `BaseCorrelator` stays at top — it's pure, no numpy. `make_correlator` behavior is identical.)

- [ ] **Step 4: Run the boundary test — verify it passes**

Run: `uv run pytest services/correlation/tests/test_import_boundary.py -v`
Expected: PASS.

- [ ] **Step 5: Run the correlation suite + full suite — no regression**

Run: `uv run pytest services/correlation/tests/ -q` then `uv run pytest -m "not postgres and not kafka" -q`
Expected: PASS. Any test that imports `RiverCorrelator`/`RobustCorrelator` does so from their own submodules (`services.correlation.adapters.river_correlator`) — unaffected. `make_correlator`'s only caller (`correlation/app.py`) still works: the correlator is built at lifespan startup, where the settings-selected kind imports its lib.

- [ ] **Step 6: Lint + commit**

Run: `ruff check . && ruff format --check .` (format the new test if flagged).

```bash
git add services/correlation/adapters/__init__.py services/correlation/tests/test_import_boundary.py
git commit -m "fix(correlation): lazy correlator imports — stop leaking numpy/river into non-ML services via common.stores"
```

---

## Task 2: Move heavy deps into uv extras + add pyyaml; regenerate the lockfile

**Files:**
- Modify: `pyproject.toml` (`[project.dependencies]` + new `[project.optional-dependencies]`)
- Modify: `uv.lock` (regenerated by `uv lock`)
- Test: none new (verified by `uv sync --frozen` + full suite)

**Interfaces:**
- Produces: base deps = the 11 light packages + explicit `pyyaml`; `[project.optional-dependencies]` with `ml` (numpy/scikit-learn/river/joblib) and `k8s` (kubernetes). `uv sync --extra ml --extra k8s` installs the full set.

- [ ] **Step 1: Edit `pyproject.toml`**

Replace the `[project].dependencies` list and add the extras. The new `dependencies`:

```toml
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "pydantic>=2.9",
    "pydantic-settings>=2.5",
    "redis>=5.1",
    "httpx>=0.28.1",
    "prometheus-client>=0.20",
    "sqlalchemy>=2.0",
    "alembic>=1.13",
    "psycopg[binary]>=3.1",
    "kafka-python>=2.0.2",
    "pyyaml>=6",
]

[project.optional-dependencies]
ml = ["numpy>=2", "scikit-learn>=1.9.0", "river>=0.25.0", "joblib>=1.5.3"]
k8s = ["kubernetes>=29"]
```

(Removed from base: `river`, `numpy`, `scikit-learn`, `joblib`, `kubernetes` → into extras. Added: `pyyaml` — `common/stores.py`→`playbook_store.py` needs `import yaml`, which currently only rides in transitively via kubernetes + uvicorn[standard]; pinning it removes that fragility. `scipy` stays unlisted — transitive of scikit-learn. Keep `[project.optional-dependencies]` placed right after the `dependencies` list, matching TOML section conventions; if a `[tool.*]` or other `[project.*]` table follows, insert before it.)

Note whether the repo pins a `[dependency-groups]` dev group — if `dev` deps live in `[tool.uv]` or `[dependency-groups]`, leave that block untouched.

- [ ] **Step 2: Regenerate the lockfile**

Run: `uv lock`
Then inspect the diff: `git diff uv.lock | head -60`
Expected: metadata-only — the `intelliops` virtual-package entry gains `optional-dependencies = { ml = [...], k8s = [...] }` and its top-level `dependencies` shrinks; the 5 relocated packages' `[[package]]` entries are unchanged in VERSION. **If any package's resolved version changed, STOP and report** — the union must be identical to before.

- [ ] **Step 3: Verify `uv sync --frozen` works both ways**

Run: `uv sync --frozen --no-dev` (base only — should NOT install numpy/sklearn/river/kubernetes) then `uv sync --frozen --extra ml --extra k8s` (should install them).
Expected: both succeed. (The second restores your full dev venv for running tests.)

- [ ] **Step 4: Full suite + ruff green**

Run: `uv sync --frozen` (or `--extra ml --extra k8s` to get everything incl. dev) then `uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check .`
Expected: ~427 pass, ruff clean. (The dev venv has all extras, so ML unit tests still run.)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: split heavy deps into ml/k8s uv extras; pin pyyaml explicitly in base"
```

---

## Task 3: Multi-stage `deploy/Dockerfile` (base + full)

**Files:**
- Modify: `deploy/Dockerfile` (rewrite as two selectable stages off a shared builder-base)
- Test: none (Docker build verified in Task 5's gate)

**Interfaces:**
- Produces: a `base` build target (12 common deps, for 11 services) and a `full` build target (base + ml + k8s, for correlation/action). `docker build --target base|full` selects.

- [ ] **Step 1: Rewrite `deploy/Dockerfile`**

Replace the entire file with:

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.11-slim AS builder-base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./

# ---- base: 12 common deps, NO ml/k8s extras (11 of 13 services) ----
FROM builder-base AS base
RUN uv sync --frozen --no-dev --no-install-project

COPY common/ ./common/
COPY services/ ./services/
COPY policies/ ./policies/
COPY deploy/playbooks/ ./data/playbooks/
COPY alembic.ini ./alembic.ini
COPY alembic/ ./alembic/
RUN uv sync --frozen --no-dev --no-install-project

ENV PATH="/app/.venv/bin:${PATH}"
ENV SERVICE_MODULE=services.ingestion.app:app
ENV PORT=8000
CMD ["sh", "-c", "uvicorn \"$SERVICE_MODULE\" --host 0.0.0.0 --port \"$PORT\""]

# ---- full: base + ml + k8s (correlation, action) ----
FROM builder-base AS full
RUN uv sync --frozen --no-dev --extra ml --extra k8s --no-install-project

COPY common/ ./common/
COPY services/ ./services/
COPY policies/ ./policies/
COPY deploy/playbooks/ ./data/playbooks/
COPY alembic.ini ./alembic.ini
COPY alembic/ ./alembic/
RUN uv sync --frozen --no-dev --extra ml --extra k8s --no-install-project

ENV PATH="/app/.venv/bin:${PATH}"
ENV SERVICE_MODULE=services.ingestion.app:app
ENV PORT=8000
CMD ["sh", "-c", "uvicorn \"$SERVICE_MODULE\" --host 0.0.0.0 --port \"$PORT\""]
```

(Preserves every COPY + ENV + CMD from the current Dockerfile; the only structural change is the two named stages and the `--extra ml --extra k8s` on the `full` stage. `--no-install-project` stays on both sync calls — virtual project, no wheel to build. Compare against the current `deploy/Dockerfile` to confirm the COPY set matches exactly — if the current file copies anything additional, carry it into BOTH stages.)

- [ ] **Step 2: Verify both stages build** (needs Docker — if Docker is unavailable in this task's environment, note it and defer the build verification to Task 5's gate, which runs the full compose build)

Run (if Docker available): `docker build -f deploy/Dockerfile --target base -t intelliops-slim-test .` and `docker build -f deploy/Dockerfile --target full -t intelliops-full-test .`
Expected: both succeed. Optional: `docker run --rm intelliops-slim-test python -c "import sys; import services.governance.app; assert 'numpy' not in sys.modules; print('slim OK')"`.

- [ ] **Step 3: Commit**

```bash
git add deploy/Dockerfile
git commit -m "build: multi-stage Dockerfile — slim base stage + full stage (ml+k8s)"
```

---

## Task 4: Wire compose to target the right stage per service

**Files:**
- Modify: `deploy/docker-compose.yml` (`x-service` anchor + correlation/action build overrides)
- Test: none (verified by Task 5's compose smoke)

**Interfaces:**
- Produces: 11 services build the `base` stage (via the anchor default); `correlation` and `action` build the `full` stage.

- [ ] **Step 1: Add `target: base` to the `x-service` anchor**

In `deploy/docker-compose.yml`, the anchor (lines 3-11) currently is:

```yaml
x-service: &service
  build:
    context: ..
    dockerfile: deploy/Dockerfile
  environment:
    INTELLIOPS_REDIS_URL: redis://redis:6379
  depends_on:
    redis:
      condition: service_healthy
```

Add `target: base` under `build:`:

```yaml
x-service: &service
  build:
    context: ..
    dockerfile: deploy/Dockerfile
    target: base
  environment:
    INTELLIOPS_REDIS_URL: redis://redis:6379
  depends_on:
    redis:
      condition: service_healthy
```

- [ ] **Step 2: Override the build block on `correlation` and `action`**

Because `<<: *service` merge is shallow (a service-level `build:` replaces the anchor's entirely), add a full `build:` block to each. On the `correlation` service (after its `<<: *service` line, before or among its other keys — a top-level service key, sibling to `environment`):

```yaml
  correlation:
    <<: *service
    build:
      context: ..
      dockerfile: deploy/Dockerfile
      target: full
    environment:
      # ...existing correlation environment unchanged...
```

Same for `action`:

```yaml
  action:
    <<: *service
    build:
      context: ..
      dockerfile: deploy/Dockerfile
      target: full
    environment:
      # ...existing action environment unchanged...
```

Do NOT change these services' `environment`, `ports`, `healthcheck`, or `depends_on` — only ADD the `build:` override block. The YAML merge puts `<<: *service` first, then the explicit `build:` key wins.

- [ ] **Step 3: Confirm `migrate` and the others resolve to `base`**

`migrate` and the 8 other IntelliOps services use `<<: *service` with no `build:` override → they inherit `target: base`. The 4 meridian services use `dockerfile: deploy/Dockerfile.meridian` (their own build block) — leave them untouched. Grep the file to confirm ONLY correlation + action have `target: full`:

Run: `grep -n "target:" deploy/docker-compose.yml`
Expected: `target: base` in the anchor, `target: full` on exactly correlation + action (2 occurrences), nothing else.

- [ ] **Step 4: Validate compose config parses**

Run (if Docker available): `docker compose -f deploy/docker-compose.yml config >/dev/null && echo "compose valid"`
Expected: valid. (If Docker unavailable, a YAML-lint / visual check that indentation matches; Task 5 does the real build.)

- [ ] **Step 5: Commit**

```bash
git add deploy/docker-compose.yml
git commit -m "build: target base stage for light services, full stage for correlation+action"
```

---

## Task 5: CI gate — import-boundary + grep-lints + full 13-service compose smoke

**Files:**
- Modify: `.github/workflows/ci.yml` (`compose-smoke` job — expand it; optionally add an import-boundary job)
- Test: the gate IS the test.

**Interfaces:** none (CI config).

- [ ] **Step 1: Add the import-boundary + grep-lint checks as a CI job**

In `.github/workflows/ci.yml`, add a job (before or alongside `compose-smoke`) that builds a base-only venv and proves no heavy dep leaks. It runs on ubuntu (Linux paths):

```yaml
  slim-boundary:
    runs-on: ubuntu-latest
    needs: [test]
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        run: curl -LsSf https://astral.sh/uv/install.sh | sh
      - name: Build base-only venv (no ml/k8s extras)
        run: |
          uv venv .slim
          VIRTUAL_ENV=.slim uv sync --frozen --no-dev
      - name: Every base-image service imports with zero heavy deps
        run: |
          for svc in ingestion read demo_app governance action feedback rca \
                     meridian.gateway meridian.validation meridian.aggregation meridian.reporting; do
            VIRTUAL_ENV=.slim .slim/bin/python -c "
          import importlib, sys
          importlib.import_module('services.${svc}.app')
          leaked = {'numpy','scipy','sklearn','river','joblib','kubernetes'} & set(sys.modules)
          assert not leaked, f'${svc} leaked: {leaked}'
          print('OK ${svc}')" || exit 1
          done
          VIRTUAL_ENV=.slim .slim/bin/python -c "from common.stores import make_stores; print('OK make_stores on slim')" || exit 1
      - name: Grep-lint guards
        run: |
          ! grep -rEn 'numpy|scipy|sklearn|river|joblib' services/feedback/ || { echo 'feedback must not import heavy deps'; exit 1; }
          ! grep -nE '^(import|from) .*(sklearn|joblib)' services/correlation/adapters/trained_correlator.py || { echo 'sklearn/joblib must stay lazy in trained_correlator'; exit 1; }
```

(Adjust `needs:`/naming to match the file's existing jobs. If `uv` is already installed via a setup step reused by the `test` job, reuse that pattern instead of the curl install.)

- [ ] **Step 2: Expand `compose-smoke` to all 13 services + `/ready` + migrate exit-0**

Replace the `compose-smoke` "Wait for all services" step (lines 59-73) with a version that checks `/ready` on the 7 IntelliOps HTTP services, `/health` on meridian+demo-app, and migrate's exit code:

```yaml
      - name: Wait for all services, verify readiness
        run: |
          # migrate is a one-shot with no HTTP port — assert it exited 0
          mid=$(docker compose -f deploy/docker-compose.yml ps -aq migrate)
          for attempt in $(seq 1 20); do
            state=$(docker inspect -f '{{.State.Status}}' "$mid" 2>/dev/null || echo pending)
            [ "$state" = "exited" ] && break
            [ "$attempt" -eq 20 ] && { echo "migrate never exited"; exit 1; }
            sleep 3
          done
          code=$(docker inspect -f '{{.State.ExitCode}}' "$mid")
          [ "$code" = "0" ] || { echo "migrate exited $code"; exit 1; }
          echo " ✓ migrate exited 0"
          # /ready for the 7 IntelliOps HTTP services (runs bus.ping + db_ready)
          for port in 8001 8002 8003 8004 8005 8006 8007; do
            for attempt in $(seq 1 20); do
              curl -sf "http://localhost:$port/ready" && { echo " ✓ $port /ready"; break; }
              [ "$attempt" -eq 20 ] && { echo " ✗ $port /ready never 200"; exit 1; }
              sleep 3
            done
          done
          # /health for meridian (8008/8011/8012/8013) + demo-app (8080)
          for port in 8008 8011 8012 8013 8080; do
            for attempt in $(seq 1 20); do
              curl -sf "http://localhost:$port/health" && { echo " ✓ $port /health"; break; }
              [ "$attempt" -eq 20 ] && { echo " ✗ $port /health never 200"; exit 1; }
              sleep 3
            done
          done
```

(Keep the existing `up -d --build`, the `failure()` logs step, and the `always()` down step. Note: the IntelliOps services expose `/ready` per `services/base.py`; meridian services expose `/health` per `services/meridian/common.py` — confirm meridian has no `/ready` before relying on `/health` for them.)

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: import-boundary + grep-lint guards; compose smoke covers all 13 services on /ready"
```

---

## Task 6: End-to-end verification + docs

**Files:**
- Modify: `architectural.md` (append `### ADR-022 — Slim per-service Docker images`) or a README note
- Test: run the full gate; measure image sizes.

**Interfaces:** none.

- [ ] **Step 1: Run the full local gate (needs Docker)**

Run the verification gate from the spec (A→D):
- A: base-only venv import-boundary (the CI job's script, run locally on Linux/WSL if available; on Windows, at minimum run the pytest `test_import_boundary.py` from Task 1 which covers the `common.stores` case).
- C: `uv run pytest -m "not postgres and not kafka" -q` + ruff.
- D: `docker compose -f deploy/docker-compose.yml up -d --build`, then the /ready + /health + migrate checks; `docker compose ... down`.

Record the results in the report.

- [ ] **Step 2: Measure the image-size win**

Run: `docker images | grep intelliops` (after the compose build). Compare a base-image service (e.g. `intelliops-rca`) against `intelliops-correlation` (full). Confirm the base services dropped materially (target < ~400MB) and correlation/action retain the ML/k8s libs. Record the before (~1.5GB) / after numbers.

Optional guard: `docker run --rm <base-image-service> python -c "import sys; assert 'sklearn' not in sys.modules and 'kubernetes' not in sys.modules; print('base has no ml/k8s')"`.

- [ ] **Step 3: Write ADR-022**

Append `### ADR-022 — Slim per-service Docker images` to `architectural.md` (ADRs are `### ADR-0xx — Title` sections in that single file; latest is ADR-021, added by the prior effort). Match the `**Context.** / **Decision.** / **Why.**` house format. Record: the measured bloat (all 13 images carried ~270MB of ML+k8s libs used by only 2 services); the leak discovered (common/stores.py → adapters/__init__ eager numpy/river import) and its fix (lazy correlator imports); the uv extras split (ml/k8s) + explicit pyyaml; the multi-stage base/full Dockerfile + compose targeting; and the verification gate (import-boundary + grep-lints + 13-service /ready smoke). Reference [ADR-012](#adr-012--config-switched-adapter-selection-with-test-safe-defaults) (config-switch discipline) as prior art for keeping optional behavior behind a boundary.

- [ ] **Step 4: Final gates + commit**

Run: `uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check .`
Expected: green.

```bash
git add architectural.md
git commit -m "docs: ADR-022 slim per-service Docker images (leak fix + extras + multi-stage)"
```

---

## Self-Review checklist (run after execution, before the PR)

1. **Leak fixed:** `test_import_boundary.py` passes; base-only venv imports every service with zero heavy deps — Tasks 1, 5.
2. **Extras split + lock:** heavy deps in `ml`/`k8s`; pyyaml pinned; `uv.lock` metadata-only diff; `uv sync --frozen` works both ways — Task 2.
3. **Multi-stage + targeting:** `base`/`full` stages; correlation+action on `full`, everything else (incl. migrate) on `base` — Tasks 3, 4.
4. **Gate green:** import-boundary + grep-lints + full suite ~427 + ruff + 13-service compose smoke on `/ready` — Tasks 5, 6.
5. **Images slimmed:** base services materially smaller (< ~400MB); correlation/action retain their libs — Task 6.
6. **No behavior change:** pipeline still detects→diagnoses→gates→remediates (compose smoke reaches `/ready` on all services).
