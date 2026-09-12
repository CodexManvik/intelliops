# Docker Image-Slimming — Design Spec (PR B)

**Date:** 2026-08-28
**Owner:** Manvik (integration lead)
**Status:** design (architectural), hardened by a 4-agent audit (dep-surface + uv-mechanics + adversarial-risk + synthesis) that ran the actual imports against the project venv, not just grep.

## The problem (verified via `docker images` + running the imports)

Every service builds from one `deploy/Dockerfile` that installs a single flat `[project.dependencies]` list containing the full data-science + kubernetes stack. Measured uncompressed sizes in the venv: **scipy 101MB (+libs 20MB), kubernetes 64MB, scikit-learn 34MB, numpy 27MB (+libs 21MB), river 6MB** — ~270MB of libraries. But per-service **genuine** need (empirically verified by importing each service's app and inspecting `sys.modules`):

| Service | Genuine heavy need | Resident today |
|---|---|---|
| **correlation** | numpy, river (eager); sklearn, joblib, scipy (lazy, only `CORRELATOR_KIND=trained`) | numpy, river |
| **action** | kubernetes (lazy, only `REMEDIATOR_MODE=k8s`/`HEALTH_CHECK_MODE=k8s`) | numpy, river *(leaked)* |
| **rca, governance, feedback** | **none** | numpy, river *(leaked)* |
| **migrate** (one-shot) | none | numpy, river *(leaked, harmless)* |
| **ingestion, read, demo_app, meridian ×4** | none | **none — already clean** |

## The blocker the audit found (must fix first)

The intended "ML → correlation only" split is defeated at runtime by a package-`__init__` side effect:

`common/stores.py` (imported by action, governance, feedback, rca, correlation via `make_stores`) does `from services.correlation.adapters.baseline_store import PostgresBaselineStore` and `...model_store import ...`. Those two files are **pure SQLAlchemy** — but importing any submodule of `services.correlation.adapters` first executes its `__init__.py`, which **eagerly** does `from ...river_correlator import RiverCorrelator` and `from ...robust_correlator import RobustCorrelator` — the two files with top-level `import numpy as np` and `from river import stats`.

**Result:** numpy + river load into action/governance/feedback/rca — none of which need them. If we split the deps into extras and rebuild without fixing this, those 4 "slim" services `import numpy` at startup and **crash with `ModuleNotFoundError`**.

**The fix (verified safe):** `services/correlation/adapters/__init__.py:8-9` eagerly imports `RiverCorrelator` + `RobustCorrelator`. The file ALREADY imports `TrainedCorrelator` lazily inside `make_correlator()` (lines 30-41, with a comment explaining exactly this dependency-isolation reasoning). Apply the same pattern to river/robust: remove the two eager imports, move them inside `make_correlator`'s `river`/`robust` branches. Verified: `BaseCorrelator` (pure, no numpy) stays imported at top; no external code imports `RiverCorrelator`/`RobustCorrelator` from the package `__init__` (only from their own submodules); `make_correlator`'s only caller is `correlation/app.py`. Then `common.stores` → `baseline_store`/`model_store` no longer traverses numpy/river.

## Goal

Slim the non-ML/non-k8s service images from ~1.5GB toward ~300–400MB by (1) fixing the leak, (2) moving the heavy deps into uv extras, (3) a multi-stage Dockerfile with a slim `base` stage (11 services) and a `full` stage (correlation + action), and (4) a verification gate that proves no service silently lost a dependency.

## Non-goals / constraints

- **No functional/behavior change.** Detection, RCA, governance, remediation logic unchanged. `correlation` and `action` keep every dep they actually use.
- **Base image stays `python:3.11-slim`** (the win is removing libs, not the base).
- **No version changes.** The dependency *union* (base ∪ ml ∪ k8s) is identical to today's flat set — the `uv.lock` regeneration is metadata-only.
- **Separate PR** from the console-streamline work.

## Global Constraints

- **Test-safe.** The existing suite (`uv run pytest -m "not postgres and not kafka"`, ~427) stays green — the leak fix is import-ordering only; the extras split doesn't change what the full dev venv installs. `ruff check .` + `ruff format --check .` clean.
- **Lockfile discipline.** After editing `pyproject.toml`, run `uv lock` (NOT `--frozen`) and commit the regenerated `uv.lock`. Expect a metadata-only diff (which section each of the 5 relocated packages belongs to); NO resolved-version changes. `uv sync --frozen` (Docker/CI) then passes.
- **Commit trailer** on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Shared files (coordinate):** `pyproject.toml`, `uv.lock`, `deploy/Dockerfile`, `deploy/docker-compose.yml`, `services/correlation/adapters/__init__.py`, `.github/workflows/ci.yml`.

---

## Design

### 1. Fix the leak (`services/correlation/adapters/__init__.py`)

Remove the two eager imports (lines 8-9); move them lazily into `make_correlator`:

```python
"""Correlation adapters: concrete Correlator implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from services.correlation.adapters.base_correlator import BaseCorrelator

if TYPE_CHECKING:
    from common.config import Settings


def make_correlator(settings: Settings) -> BaseCorrelator:
    """Build the configured Correlator implementation from settings.correlator_kind.

    Correlator classes are imported lazily so that importing this package (which
    common/stores.py does transitively via baseline_store/model_store) never pulls
    in numpy/river/sklearn for services that only need the SQLAlchemy stores.
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

Verify: the existing correlation tests still pass (they import `make_correlator` or the submodules directly — both still work), and the import-boundary test (gate A) proves the leak is gone.

### 2. `pyproject.toml` — move heavy deps into extras + add pyyaml

Base shrinks to 12 deps (11 today's-light + explicit pyyaml):

```toml
[project]
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
ml  = ["numpy>=2", "scikit-learn>=1.9.0", "river>=0.25.0", "joblib>=1.5.3"]
k8s = ["kubernetes>=29"]
```

**Why pyyaml explicitly:** `common/stores.py` unconditionally imports `services/governance/adapters/playbook_store.py` → `import yaml` (not gated behind postgres; `store_backend` defaults to `file`). Today pyyaml rides in only via `kubernetes` + `uvicorn[standard]`. On the slim base (k8s gone), `[standard]` becomes its only source — a fragile silent transitive dependency. Pinning it in base removes the risk. `scipy` stays unlisted (transitive of scikit-learn, rides with ml).

Then: `uv lock` (regenerate) → commit `uv.lock` (metadata-only diff).

### 3. Multi-stage `deploy/Dockerfile`

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.11-slim AS builder-base
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./

# ---- base: 12 common deps, NO ml/k8s (11 of 13 services) ----
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

Notes: `--no-install-project` on both sync calls is deliberate (virtual project — no `[build-system]` — so no wheel to build). The two stages repeat their COPY/sync sequences intentionally so `base`'s cache is fully decoupled from `full`'s extras. The default `CMD`/`SERVICE_MODULE` are overridden per-service by compose env exactly as today.

### 4. `deploy/docker-compose.yml` — target the stage per service

The `<<: *service` merge is shallow — a service-level `build:` key fully replaces the anchor's. So set the default in the anchor:

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

Then on **correlation** and **action** ONLY, override the whole build block after `<<: *service` (later key wins):

```yaml
  correlation:
    <<: *service
    build: { context: .., dockerfile: deploy/Dockerfile, target: full }
    # ...environment/ports/healthcheck/depends_on unchanged
  action:
    <<: *service
    build: { context: .., dockerfile: deploy/Dockerfile, target: full }
    # ...unchanged
```

`migrate` and the 8 other IntelliOps services inherit `target: base`. The 4 meridian services keep `dockerfile: deploy/Dockerfile.meridian` (all clean; that Dockerfile needs no extras — leave it as-is). **Confirm `migrate` resolves to `base`** (a mis-tag to `full` is harmless but wasteful — see gate).

### 5. CI (`.github/workflows/ci.yml`) — close the smoke-test gap

The current `compose-smoke` loops ports 8001–8007 + 8080 hitting `/health` (a static 200), skipping migrate and all 4 meridian ports. Update it to the verification gate below so a slim-image missing-dep is actually caught.

---

## Verification gate (the acceptance test)

Run A → B → C → D in order. A + B need no Docker and catch the highest-value structural risks fastest.

**A. Import-boundary test (catches the leak + pyyaml + feedback).** Build a venv from the base group only; every base-image service must import with zero heavy deps resident:

```bash
uv venv /tmp/slim && VIRTUAL_ENV=/tmp/slim uv sync --frozen --no-dev
for svc in ingestion read demo_app governance action feedback rca \
           meridian.gateway meridian.validation meridian.aggregation meridian.reporting; do
  VIRTUAL_ENV=/tmp/slim /tmp/slim/bin/python -c "
import importlib, sys
importlib.import_module('services.${svc}.app')
leaked = {'numpy','scipy','sklearn','river','joblib','kubernetes'} & set(sys.modules)
assert not leaked, f'${svc} leaked: {leaked}'
print('OK ${svc}')" || exit 1
done
VIRTUAL_ENV=/tmp/slim /tmp/slim/bin/python -c "from common.stores import make_stores; print('OK make_stores on slim')" || exit 1
```

(On Windows locally the paths differ — the CI job runs on Linux. The plan will provide the Linux invocation for CI and note the local-Windows equivalent.)

**B. Grep-lint guards.**

```bash
! grep -rEn 'numpy|scipy|sklearn|river|joblib' services/feedback/ || exit 1
! grep -nE '^(import|from) .*(sklearn|joblib)' services/correlation/adapters/trained_correlator.py || exit 1
```

**C. Full test suite** (unchanged full venv — all extras, ML unit tests still run):

```bash
uv sync --frozen && uv run pytest -m "not postgres and not kafka" -q   # ~427 pass
ruff check . && ruff format --check .
```

**D. Full compose smoke — all 13 services, correct endpoints.**

```bash
docker compose -f deploy/docker-compose.yml up -d --build
# migrate one-shot: assert exited 0
[ "$(docker inspect -f '{{.State.ExitCode}}' $(docker compose -f deploy/docker-compose.yml ps -aq migrate))" = "0" ] || { echo 'migrate not 0'; exit 1; }
# /ready for the 7 IntelliOps HTTP services (runs bus.ping + db_ready)
for port in 8001 8002 8003 8004 8005 8006 8007; do
  for a in $(seq 1 20); do curl -sf "http://localhost:$port/ready" && break; [ "$a" = 20 ] && exit 1; sleep 3; done
done
# /health for meridian (8008/8011/8012/8013) + demo-app (8080)
for port in 8008 8011 8012 8013 8080; do
  for a in $(seq 1 20); do curl -sf "http://localhost:$port/health" && break; [ "$a" = 20 ] && exit 1; sleep 3; done
done
docker compose -f deploy/docker-compose.yml down
```

**Image-size regression guard:** after build, assert the base-image services do NOT contain sklearn (`docker image inspect intelliops-rca ... `; or a size check) — proves the slim images actually slimmed.

**Out of scope for automation (documented manual checks):** the mode-specific boots for `CORRELATOR_KIND=trained` (restart-after-retrain, proves joblib present in `full`) and `REMEDIATOR_MODE=k8s` (proves kubernetes importable in `full`). These paths aren't part of the default demo; the spec records the exact commands for anyone verifying those modes.

## Acceptance criteria

1. **The leak is gone:** gate A passes — action/governance/feedback/rca (and ingestion/read/demo_app/meridian) import with zero of {numpy, scipy, sklearn, river, joblib, kubernetes} in `sys.modules`.
2. **Deps split into extras** (`ml`, `k8s`); base has explicit `pyyaml`; `uv.lock` regenerated (metadata-only, no version changes); `uv sync --frozen` works.
3. **Multi-stage Dockerfile:** `base` (11 services) + `full` (correlation, action); compose targets the right stage per service; `migrate` on `base`.
4. **All gates green:** import-boundary (A), grep-lints (B), full test suite ~427 + ruff (C), 13-service compose smoke on `/ready` (D).
5. **Images actually slimmed:** the base-image services no longer contain the ML/k8s libs (size drops materially, target < ~400MB); correlation + action retain what they use.
6. **No behavior change:** the pipeline still detects → diagnoses → gates → remediates end-to-end (proven by the compose smoke reaching `/ready` on all services).

## Suggested task ordering (for the plan)

1. Fix the leak (`adapters/__init__.py` lazy imports) + a unit/import-boundary test proving numpy/river no longer load via `common.stores`. Run the existing correlation suite green.
2. `pyproject.toml` extras + explicit pyyaml; `uv lock`; commit regenerated `uv.lock`; confirm `uv sync --frozen` + full suite green.
3. Multi-stage `deploy/Dockerfile` (`base` + `full`).
4. `deploy/docker-compose.yml` target wiring (anchor `base`; correlation/action `full`; confirm migrate `base`).
5. CI `compose-smoke` rewrite to the full 13-service `/ready` gate + the import-boundary + grep-lints + image-size guard.
6. Verify end-to-end: run gate A–D; confirm image sizes dropped; docs note (a short ADR or README line on the slim/full split).

Rationale: the leak fix is the prerequisite (step 1) — without it steps 2–4 produce images that still import numpy. Then deps → Dockerfile → compose → CI, each independently verifiable.
