# Observability & Readiness (Tier 1c) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add structured JSON logging and a real `/ready` readiness probe to all six services through the shared app factory, behind config that defaults to today's behavior.

**Architecture:** One `common/logging.py` (`JsonFormatter` + `configure_logging`, stdlib only) and a `/ready` endpoint, both wired once in `services/base.py` `create_app`. The bus gains a backend-agnostic `ping()`; readiness actively checks the bus and (in postgres mode) the DB engine. No new dependency.

**Tech Stack:** stdlib `logging`, FastAPI, pydantic-settings, redis-py / fakeredis, SQLAlchemy Core.

**Spec:** `docs/superpowers/specs/2026-08-20-observability-readiness-design.md`

## Global Constraints

- **Default = current behavior.** `LOG_FORMAT=text` (human logs) and `LOG_LEVEL=INFO` are the
  defaults; the full `pytest` suite stays green and readable with them. Compose sets `json`.
- **Zero new dependency.** stdlib `logging` + a hand-rolled formatter. No structlog / json-logger.
- **`create_app` is the one seam.** Logging setup, `/ready`, and the auth-exempt update all happen
  there; no per-service duplication of that logic.
- **`bus.ping()` raises on failure** (contract), discarding any return value. `/ready` catches to
  build the `failed` list; it never lets a probe exception escape as a 500.
- **`/ready` is auth-exempt** alongside `/health`, in every `AUTH_MODE`.
- **Idempotent logging setup** — `configure_logging` must not stack handlers when `create_app`
  runs multiple times in one test process (asserted by a test).
- **Config:** `env_prefix="INTELLIOPS_"`, so the new fields are `INTELLIOPS_LOG_LEVEL` /
  `INTELLIOPS_LOG_FORMAT`.
- **Git identity:** commits authored `CodexManvik <manviktalwar.official@gmail.com>`; commit
  messages end with the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer. CI
  enforces `ruff check` + `ruff format --check .` + `pytest`.

## Interfaces (exact, referenced across tasks)

- `configure_logging(service_name: str, settings) -> None` (in `common/logging.py`).
- `class JsonFormatter(logging.Formatter)` with `format(record) -> str`.
- `BusClient.ping(self) -> None` (protocol); `RedisBus.ping(self) -> None`.
- `create_app(service_name: str, auth_exempt=None, readiness=None) -> FastAPI` — `readiness` is
  `Callable[[], None] | None`.
- Settings: `log_level: str = "INFO"`, `log_format: str = "text"`.

---

## Task 1: Config fields + `common/logging.py` (JsonFormatter + configure_logging)

**Files:**
- Modify: `common/config.py`
- Create: `common/logging.py`
- Test: `tests/test_logging.py`

**Interfaces:**
- Produces: `configure_logging(service_name, settings)`, `JsonFormatter`; `settings.log_level`,
  `settings.log_format`.

- [ ] **Step 1: Add config fields**

In `common/config.py`, after the existing fields (e.g. after `database_url`):
```python
    log_level: str = "INFO"     # DEBUG | INFO | WARNING | ERROR
    log_format: str = "text"    # "text" | "json"
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_logging.py
import json
import logging
from common.logging import JsonFormatter, configure_logging


class _S:
    log_level = "INFO"
    log_format = "json"


class _SText(_S):
    log_format = "text"


def _record(msg="hello", level=logging.INFO, exc_info=None):
    return logging.LogRecord("services.foo.app", level, "/x/app.py", 96, msg, None, exc_info)


def test_json_formatter_has_expected_keys():
    rec = _record()
    rec.service = "foo-service"
    out = json.loads(JsonFormatter().format(rec))
    assert out["level"] == "INFO"
    assert out["logger"] == "services.foo.app"
    assert out["service"] == "foo-service"
    assert out["msg"] == "hello"
    assert out["line"] == 96
    assert "ts" in out


def test_json_formatter_includes_exc_info():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        rec = _record(level=logging.ERROR, exc_info=sys.exc_info())
    rec.service = "foo-service"
    out = json.loads(JsonFormatter().format(rec))
    assert "exc_info" in out and "ValueError" in out["exc_info"]


def test_configure_logging_is_idempotent():
    root = logging.getLogger()
    configure_logging("foo-service", _S())
    n = len(root.handlers)
    configure_logging("foo-service", _S())
    assert len(root.handlers) == n, "configure_logging must not stack handlers"


def test_configure_logging_stamps_service_and_emits_json(capsys):
    configure_logging("foo-service", _S())
    logging.getLogger("services.foo.app").info("wired")
    err = capsys.readouterr().err
    # the json line carries the service + message
    line = [ln for ln in err.splitlines() if "wired" in ln][-1]
    payload = json.loads(line)
    assert payload["service"] == "foo-service" and payload["msg"] == "wired"


def test_text_format_is_human_not_json(capsys):
    configure_logging("foo-service", _SText())
    logging.getLogger("services.foo.app").info("plain")
    err = capsys.readouterr().err
    assert "plain" in err
    # text mode is not JSON
    line = [ln for ln in err.splitlines() if "plain" in ln][-1]
    try:
        json.loads(line)
        raise AssertionError("text mode should not emit JSON")
    except json.JSONDecodeError:
        pass
```

- [ ] **Step 3: Run to verify it fails**

Run: `uv run pytest tests/test_logging.py -v`
Expected: FAIL — `common.logging` not defined.

- [ ] **Step 4: Implement `common/logging.py`**

```python
"""Structured logging for all services, wired once in create_app.

JSON-lines (one object per line) so any aggregator can parse it; behind
LOG_FORMAT=text|json (default text) so local dev and pytest stay readable.
Every logger.* call across the services picks this up with no code change."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime


class _ServiceFilter(logging.Filter):
    def __init__(self, service_name: str) -> None:
        super().__init__()
        self._service = service_name

    def filter(self, record: logging.LogRecord) -> bool:
        record.service = self._service
        return True


class JsonFormatter(logging.Formatter):
    # stdlib LogRecord attributes we do not want to blindly dump as "extra"
    _RESERVED = frozenset(
        vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()
    ) | {"service", "message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": getattr(record, "service", "-"),
            "msg": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        # merge caller-supplied extra={...} fields
        for k, v in record.__dict__.items():
            if k not in self._RESERVED and k not in payload:
                payload[k] = v
        return json.dumps(payload, default=str)


def configure_logging(service_name: str, settings) -> None:
    """Install a single root handler + level + service filter. Idempotent."""
    root = logging.getLogger()
    # Clear our own handlers so repeated create_app() calls never stack.
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler()  # stderr
    if getattr(settings, "log_format", "text") == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(service)s] %(message)s")
        )
    handler.addFilter(_ServiceFilter(service_name))
    root.addHandler(handler)
    root.setLevel(getattr(settings, "log_level", "INFO"))
```

Note: the `_ServiceFilter` is on the handler so `record.service` is set before the text formatter
references `%(service)s` (a filter on the handler runs before that handler formats). The JSON
formatter reads `getattr(record, "service", "-")` so it is safe even without the filter.

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_logging.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add common/config.py common/logging.py tests/test_logging.py
git commit -m "feat(obs): structured JSON logging (JsonFormatter + configure_logging)"
```

---

## Task 2: `bus.ping()` — backend-agnostic bus health check

**Files:**
- Modify: `common/interfaces.py`, `common/bus.py`
- Test: `tests/test_bus.py` (extend)

**Interfaces:**
- Produces: `BusClient.ping(self) -> None`; `RedisBus.ping(self) -> None`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_bus.py`:
```python
def test_redisbus_ping_ok():
    import fakeredis
    from services.... # (match the file's existing import of RedisBus)
    from common.bus import RedisBus
    bus = RedisBus(client=fakeredis.FakeStrictRedis(decode_responses=True))
    bus.ping()  # must not raise against a live (fake) client


def test_redisbus_ping_raises_when_down():
    import pytest
    from common.bus import RedisBus

    class _DeadClient:
        def ping(self):
            raise ConnectionError("redis down")

    bus = RedisBus(client=_DeadClient())
    with pytest.raises(Exception):
        bus.ping()
```
(Use the same `RedisBus` import the existing `test_bus.py` uses; drop the stray import line.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_bus.py -k ping -v`
Expected: FAIL — `RedisBus` has no `ping`.

- [ ] **Step 3: Add `ping` to the protocol + RedisBus**

In `common/interfaces.py`, add to the `BusClient` protocol (additive):
```python
    def ping(self) -> None: ...
```

In `common/bus.py` `RedisBus`:
```python
    def ping(self) -> None:
        """Raise if the bus backend is unreachable (readiness probe uses this)."""
        self._r.ping()  # redis-py returns True; we discard it. Exceptions propagate.
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_bus.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add common/interfaces.py common/bus.py tests/test_bus.py
git commit -m "feat(bus): BusClient.ping() for readiness probes"
```

---

## Task 3: `create_app` — wire logging + `/ready` + auth-exempt

**Files:**
- Modify: `services/base.py`
- Test: `tests/test_readiness.py`

**Interfaces:**
- Consumes: `configure_logging`, `bus.ping()`.
- Produces: `create_app(service_name, auth_exempt=None, readiness=None)`; a `/ready` endpoint.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_readiness.py
from fastapi.testclient import TestClient
from services.base import create_app


class _OkBus:
    def ping(self): pass


class _DeadBus:
    def ping(self): raise ConnectionError("redis down")


def _client(bus, readiness=None):
    app = create_app("test-service", readiness=readiness)
    app.state.bus = bus  # override the real bus with a fake
    return TestClient(app)


def test_ready_200_when_bus_ok_no_db():
    c = _client(_OkBus())
    r = c.get("/ready")
    assert r.status_code == 200 and r.json() == {"ready": True}


def test_ready_503_when_bus_down():
    c = _client(_DeadBus())
    r = c.get("/ready")
    assert r.status_code == 503 and r.json()["failed"] == ["redis"]


def test_ready_503_when_db_check_fails():
    def _bad_db(): raise RuntimeError("db down")
    c = _client(_OkBus(), readiness=_bad_db)
    r = c.get("/ready")
    assert r.status_code == 503 and r.json()["failed"] == ["postgres"]


def test_ready_503_both_down():
    def _bad_db(): raise RuntimeError("db down")
    c = _client(_DeadBus(), readiness=_bad_db)
    r = c.get("/ready")
    assert r.status_code == 503 and set(r.json()["failed"]) == {"redis", "postgres"}


def test_ready_200_with_passing_db():
    c = _client(_OkBus(), readiness=lambda: None)
    assert c.get("/ready").status_code == 200


def test_health_still_liveness_only():
    c = _client(_DeadBus())  # bus down
    assert c.get("/health").status_code == 200  # liveness unaffected


def test_ready_exempt_under_auth_token(monkeypatch):
    from common.config import get_settings
    monkeypatch.setenv("INTELLIOPS_AUTH_MODE", "token")
    monkeypatch.setenv("INTELLIOPS_AUTH_TOKEN", "secret")
    get_settings.cache_clear()
    c = _client(_OkBus())
    assert c.get("/ready").status_code == 200  # reachable without a token
    get_settings.cache_clear()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_readiness.py -v`
Expected: FAIL — no `/ready`, `readiness` param not accepted.

- [ ] **Step 3: Modify `create_app`**

In `services/base.py`:
- Add `from collections.abc import Callable` (already imported) and import
  `from common.logging import configure_logging`; `from fastapi.responses import JSONResponse`
  (already imported).
- Signature: `def create_app(service_name: str, auth_exempt=None, readiness: "Callable[[], None] | None" = None) -> FastAPI:`
- As the FIRST line of the body (before building the app): `configure_logging(service_name, get_settings())`.
- Update the exempt predicate default to also exempt `/ready`:
  `_is_exempt = auth_exempt or (lambda method, path: path in ("/health", "/ready"))`.
  (For services that pass a custom `auth_exempt` — only governance — that predicate already
  exempts `/health`; add `/ready` there too in Task 4's governance touch, OR make the middleware
  always treat `/health` and `/ready` as exempt regardless of the predicate. SIMPLER + safer:
  in the middleware, short-circuit `if request.url.path in ("/health", "/ready"): return await
  call_next(request)` before consulting `_is_exempt`. Do that — it guarantees probes are never
  gated even when a service passes a custom predicate.)
- After the `/health` route, add:
```python
    @app.get("/ready")
    def ready():
        failed = []
        try:
            app.state.bus.ping()
        except Exception:
            failed.append("redis")
        if readiness is not None:
            try:
                readiness()
            except Exception:
                failed.append("postgres")
        if failed:
            return JSONResponse({"ready": False, "failed": failed}, status_code=503)
        return {"ready": True}
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_readiness.py services/*/tests/ -v`
Expected: PASS (readiness tests + existing service tests, incl. auth, unaffected).

- [ ] **Step 5: Commit**

```bash
git add services/base.py tests/test_readiness.py
git commit -m "feat(obs): /ready probe + logging setup wired in create_app"
```

---

## Task 4: Per-service readiness wiring (+ the correlation db_engine fix)

**Files:**
- Modify: `services/governance/app.py`, `services/action/app.py`, `services/feedback/app.py`,
  `services/rca/app.py`, `services/correlation/app.py`
- Test: extend one service's tests to assert `/ready` checks its DB (or a focused readiness test)

**Interfaces:**
- Consumes: `create_app(..., readiness=...)`, `app.state.db_engine`.

- [ ] **Step 1: Add a shared DB readiness helper**

In `common/logging.py`? No — put a tiny DB check helper in `services/base.py` (co-located with
`create_app`), so services import one thing:
```python
# services/base.py
from sqlalchemy import text

def db_ready(engine) -> None:
    """Raise if the DB is unreachable. A None engine (file mode / no DB) is a no-op pass."""
    if engine is None:
        return
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
```

- [ ] **Step 2: Wire the four store-using services**

Each of these ALREADY sets `app.state.db_engine = stores.engine` in its lifespan/init (governance,
action, feedback, rca). Change their `create_app(...)` call to pass a readiness closure that reads
the engine lazily at probe time:

- `services/governance/app.py:18` — `app = create_app("governance-service", auth_exempt=_governance_exempt, readiness=lambda: db_ready(getattr(app.state, "db_engine", None)))`
  WAIT: `app` is referenced inside its own construction. Fix: define the closure to capture `app`
  after creation is not possible in one line. Instead, since `create_app` returns the app and the
  closure needs `app.state`, pass a closure that takes no args and reads a module-level `app` —
  which is fine because the closure only runs at request time, after `app` is bound. So:
  ```python
  app = create_app("governance-service", auth_exempt=_governance_exempt,
                   readiness=lambda: db_ready(getattr(app.state, "db_engine", None)))
  ```
  This works: the lambda closes over the module global `app`, evaluated lazily when `/ready` is
  hit (long after `app` is assigned). Import `db_ready` from `services.base`.
- `services/action/app.py:100` — `app = create_app("action-service", readiness=lambda: db_ready(getattr(app.state, "db_engine", None)))`
- `services/feedback/app.py:74` — same pattern with `"feedback-service"`.
- `services/rca/app.py:42` — same pattern with `"rca-service"`.

- [ ] **Step 3: Fix correlation (the one inconsistency) + wire it**

`services/correlation/app.py` sets `app.state.engine` (the CorrelationEngine) but NOT
`app.state.db_engine`. In its lifespan, after `stores = make_stores(settings)` (the guarded
block), add `app.state.db_engine = getattr(stores, "engine", None)` — but note `stores` may be
unbound on the DB-down cold-start path. Set it INSIDE the try where `stores` is bound:
```python
    try:
        stores = make_stores(settings)
        app.state.db_engine = stores.engine   # <-- add
        baseline_store = stores.baseline_store
        training_records = [r.model_dump() for r in stores.training_store.read_all()]
    except Exception as exc:
        logger.warning("store reload failed, starting cold: %s", exc)
        # app.state.db_engine stays unset → readiness treats it as no-DB (getattr default None)
```
Then wire: `app = create_app("correlation-service", readiness=lambda: db_ready(getattr(app.state, "db_engine", None)))` at line 135.
(Ingestion + read have no DB → leave their `create_app` calls unchanged; `/ready` checks bus only.)

- [ ] **Step 4: Test — a service's /ready reflects its DB**

Add a focused test (e.g. in `services/governance/tests/`) that constructs the governance app,
sets `app.state.bus` to a fake OK bus and `app.state.db_engine` to a fake engine whose
`connect()` raises, and asserts `/ready` → 503 `{failed:["postgres"]}`; then with a passing engine
→ 200. (Use a minimal fake engine with a `connect()` context manager.)

- [ ] **Step 5: Run**

Run: `uv run pytest services/*/tests/ tests/test_readiness.py -v` then `uv run pytest -q -m "not postgres"`.
Expected: all pass. In `file` mode (default), `db_ready(None)` is a no-op so `/ready` is bus-only.

- [ ] **Step 6: Commit**

```bash
git add services/base.py services/governance/app.py services/action/app.py services/feedback/app.py services/rca/app.py services/correlation/app.py services/governance/tests/
git commit -m "feat(obs): per-service /ready DB checks; correlation db_engine on app.state"
```

---

## Task 5: Compose + k8s hookup + verification + docs

**Files:**
- Modify: `deploy/docker-compose.yml`, `deploy/k8s/` docs, `flow.md`, `architectural.md`
- Create: `docs/OBSERVABILITY.md`
- Verification: full suite

- [ ] **Step 1: Compose**

In `deploy/docker-compose.yml`:
- Add `INTELLIOPS_LOG_FORMAT: json` to **each app service's own `environment:` block** — NOT the
  `x-service` anchor. VERIFIED: the anchor sets `environment: {INTELLIOPS_REDIS_URL: ...}`, but
  every app service redeclares its own `environment:`, and YAML merge (`<<: *service`) does NOT
  deep-merge the `environment` mapping — a redeclared `environment:` fully REPLACES the anchor's
  (that's why each service already repeats `INTELLIOPS_REDIS_URL`). So a value put only in the
  anchor would not reach services that have their own block. Add `INTELLIOPS_LOG_FORMAT: json` to
  the six app services (ingestion, correlation, rca, action, governance, feedback, read) the same
  way REDIS_URL is repeated. (redis/postgres/migrate are infra, not app services — skip them.)
- Add a healthcheck to each of the SIX app services pointing at `/ready`:
  ```yaml
      healthcheck:
        test: ["CMD-SHELL", "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/ready').status==200 else 1)\""]
        interval: 10s
        timeout: 3s
        retries: 5
        start_period: 20s
  ```
  (The image has Python but not curl — use the Python one-liner, matching the k8s-demo pattern.
  `start_period` gives the service time to connect before failures count.)
- Validate: `docker compose -f deploy/docker-compose.yml config >/dev/null && echo OK`.

- [ ] **Step 2: Full verification**

- `uv run pytest -q -m "not postgres"` — green.
- `uv run pytest -q` (Docker) — green.
- `uv run ruff check` + `uv run ruff format --check .` — clean.
- `docker compose -f deploy/docker-compose.yml config` — validates.
If anything red, STOP and report before docs.

- [ ] **Step 3: Docs**

- `docs/OBSERVABILITY.md` (new): the log schema + `LOG_FORMAT`/`LOG_LEVEL` switches; the
  liveness-vs-readiness distinction (`/health` = process up, `/ready` = dependencies reachable,
  503 + `failed` list); the compose/k8s probe wiring.
- `deploy/k8s/README.md` (or manifests): document `livenessProbe: /health`,
  `readinessProbe: /ready`.
- `flow.md`: note structured logs + `/ready` per service.
- `architectural.md`: **ADR-016 — Observability & readiness** (the two capabilities, the one-seam
  decision, the active-ping readiness choice, zero-dep logging). Match the existing ADR prose
  format. Update the §6 built/deferred lists.

- [ ] **Step 4: Commit**

```bash
git add deploy/docker-compose.yml deploy/k8s/ docs/OBSERVABILITY.md flow.md architectural.md
git commit -m "feat(obs): compose /ready healthchecks + json logs; docs (ADR-016)"
```

---

## Self-Review

**1. Spec coverage:** JSON logging (T1 ✓); config switches (T1 ✓); `bus.ping` (T2 ✓); `/ready` +
logging in create_app + auth-exempt (T3 ✓); per-service DB readiness + correlation fix (T4 ✓);
compose/k8s + docs/ADR-016 (T5 ✓). All spec sections covered.

**2. Placeholder scan:** every code step has real code. The `readiness=lambda: db_ready(getattr(
app.state,"db_engine",None))` closure-over-module-`app` pattern is spelled out with the reason it
is safe (evaluated lazily at request time, after `app` is bound). The compose healthcheck is a
concrete Python one-liner (no curl in the image).

**3. Type consistency:** `configure_logging(service_name, settings)` and `JsonFormatter.format`
signatures match T1→T3. `bus.ping() -> None` consistent T2→T3. `create_app(service_name,
auth_exempt, readiness)` consistent T3→T4. `db_ready(engine)` (None-tolerant) consistent T4→T4
wiring. The `/ready` response shape (`{ready: bool}` / `{ready: false, failed: [...]}`) is
identical across the T3 handler and the T3/T4 tests. Correlation's `app.state.db_engine` set
inside the guarded try (where `stores` is bound) — consistent with the Tier-1b lifespan structure
that this plan modifies.
