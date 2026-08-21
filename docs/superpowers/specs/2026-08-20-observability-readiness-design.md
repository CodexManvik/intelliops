# Observability & Readiness (Tier 1c) — Design

**Date:** 2026-08-20
**Status:** Approved (pending spec review)
**Owner:** Manvik (integration lead), toward "production-credible".
**Depends on:** the shared app factory (`services/base.py`), the bus (`common/bus.py`),
and the persistence work (Tier 1a/1b) whose DB engines readiness probes.

## Goal

Close the two most-exposed operability gaps in the backend, so IntelliOps can actually
be run and debugged in production:

1. **Structured logging** — there is currently **no log configuration at all**. ~8 modules
   call `logging.getLogger(__name__).warning/debug(...)`, but nothing sets a handler, level,
   or format, so those logs go out with Python's default (unformatted, WARNING-and-above, the
   DEBUG diagnostics we wrote are invisible). Add JSON-lines logging wired once in the shared
   app factory, behind `LOG_FORMAT=text|json` (default `text`).
2. **Readiness** — every service has a liveness `/health` (process-up), but **no `/ready`** that
   checks whether it can actually reach its dependencies (Redis, and Postgres in postgres mode).
   A k8s deployment would route traffic before a service can serve. Add a real `/ready` probe.

Both land through the single shared seam (`services/base.py` `create_app`) so all six services
get them uniformly, behind config that defaults to today's behavior. Zero new dependency.

## Why this (over Kafka, the other open Tier-1 item)

Kafka would prove ADR-001's "Kafka in prod" claim but closes no felt operational hole.
Observability + readiness *is* a real gap, and it directly reinforces the persistence + k8s
work just shipped (a readiness probe that checks the DB the compose `migrate` step provisions).
"Can you operate and debug this in production" is exactly what a PPO reviewer probes.

## Decisions (from brainstorming)

- **Logging:** stdlib `logging` + a small hand-rolled `JsonFormatter` (~30 lines). No new
  dependency; every existing `logger.*` call keeps working and now emits structured JSON.
- **Readiness:** an **active dependency ping** — `bus.ping()` (Redis PING) and, when the service
  has a DB engine, `SELECT 1`. Returns 503 naming the failed dependency. Short timeouts so a hung
  dependency fails fast. No caching for now (add only if probe frequency becomes a load source).
- **One seam:** both wired in `create_app`, not per-service.

## A. Structured logging

**`common/logging.py`** (new):

- `JsonFormatter(logging.Formatter)` — emits one JSON object per line:
  ```json
  {"ts":"2026-08-20T19:45:03.123Z","level":"INFO","logger":"services.correlation.app",
   "service":"correlation-service","msg":"...","module":"app","line":96}
  ```
  Fields: `ts` (ISO-8601 UTC), `level`, `logger` (the `getLogger(__name__)` name), `service`
  (from `create_app`), `msg` (rendered), `module`, `line`. On an exception record, add
  `exc_info` (formatted traceback). Any `extra={...}` a caller attaches is merged in, so future
  callers can add context (e.g. `situation_id`) without a schema change.

- `configure_logging(service_name: str, settings) -> None`:
  - Installs a single root handler: `JsonFormatter` when `settings.log_format == "json"`, else a
    plain human `Formatter` for `text`.
  - Sets the root level from `settings.log_level`.
  - Attaches a filter that stamps every record with `service = service_name` (so call sites never
    pass it; aggregated logs from all six services are attributable).
  - **Idempotent** — `create_app` may run multiple times in one test process; the function must
    NOT stack handlers. It clears existing root handlers and reinstalls one (or guards with a
    sentinel). A test asserts exactly one handler after two calls.

- `create_app` calls `configure_logging(service_name, settings)` once at construction, before
  anything else. All ~8 existing logger call sites immediately emit structured JSON with no edit.

**Config (`common/config.py`):**
```python
log_level: str = "INFO"     # DEBUG | INFO | WARNING | ERROR
log_format: str = "text"    # "text" | "json"
```
`text` default keeps pytest / local `uvicorn` readable; compose sets `INTELLIOPS_LOG_FORMAT=json`.

**Testing:** `JsonFormatter` produces the expected keys + levels, and `exc_info` on an exception
record; `configure_logging` is idempotent (one handler after two calls); `text` vs `json` switch
produces human vs parseable-JSON lines. The existing suite is the regression net that logging
setup doesn't break service startup (default `text`).

## B. Readiness probe

**`bus.ping()` — a backend-agnostic bus health check:**
- `common/interfaces.py` `BusClient` protocol gains `def ping(self) -> None` (raises on failure).
  (Current protocol is `publish` + `consume`; this is additive.)
- `RedisBus.ping()` → calls `self._r.ping()` and lets exceptions propagate (discards the bool;
  the contract is "raises on failure"). `redis.Redis.ping()` and `fakeredis`'s both exist
  (verified). Use a short socket timeout so a dead Redis fails fast, not hangs the probe.
- A future `KafkaBus` implements the same method. Keeps readiness working across bus backends.

**`create_app(service_name, auth_exempt=None, readiness=None)`:**
- `readiness`: an optional `Callable[[], None]` that raises if the service's own dependencies
  (its DB engine) are unreachable. The bus is ALWAYS checked. No-DB services pass nothing.
- `/health` stays liveness (always 200 while the process is up).
- New `/ready`:
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
- `/ready` joins `/health` in the auth-exempt set (probes carry no token, in any `AUTH_MODE`).

**Per-service wiring (papers over the DB-engine inconsistency the audit found):**

| Service | DB? | Readiness |
|---|---|---|
| governance, action, feedback | yes (`app.state.db_engine`) | closure runs `SELECT 1` on the engine, only in postgres mode |
| rca, correlation | yes (via `make_stores`) but engine not on `app.state` today | store `stores.engine` on `app.state.db_engine` in the lifespan (one-line consistency fix), then same closure |
| ingestion, read | no | no readiness callable → `/ready` checks only the bus |

The readiness closure reads `getattr(app.state, "db_engine", None)` **lazily at probe time** (not
at `create_app` construction, which runs before the lifespan builds the engine). If the engine is
absent (file mode, or a no-DB service), the DB check is skipped and readiness is bus-only. Concrete
closure: `lambda: _check_db(getattr(app.state, "db_engine", None))` where `_check_db` returns
immediately if the engine is None, else `with engine.connect() as c: c.execute(text("SELECT 1"))`.

**Compose + k8s:**
- The app services currently have NO compose healthcheck (only redis/postgres do). Adding a
  `/ready`-based healthcheck to each app service is purely additive — nothing depends on an app
  service via `service_healthy`, so there is no startup-ordering interaction to worry about. Keep
  `/health` as the liveness signal.
- Set `INTELLIOPS_LOG_FORMAT=json` at the compose level so the running stack emits structured logs.
- `deploy/k8s/`: document `livenessProbe: /health`, `readinessProbe: /ready` so a real cluster
  won't route traffic to a service that can't reach its DB.

**Testing:** `/ready` → 200 when bus + DB up; 503 `{failed:["redis"]}` when `bus.ping()` raises;
503 `{failed:["postgres"]}` when the DB check raises; 503 with both when both fail (fake bus whose
`ping` raises + a fake readiness callable). Bus-only service: 200 with just the bus, 503 when bus
down. `RedisBus.ping()` delegates to the client (fakeredis supports `ping`). `/ready` reachable
without a token under `AUTH_MODE=token`.

## Concrete change list

**New:** `common/logging.py` (`JsonFormatter`, `configure_logging`); tests
`tests/test_logging.py`, `tests/test_readiness.py`.

**Modified:** `services/base.py` (`create_app`: call `configure_logging`; add `readiness` param +
`/ready`; add `/ready` to auth-exempt); `common/interfaces.py` (`BusClient.ping`); `common/bus.py`
(`RedisBus.ping`); `common/config.py` (`log_level`, `log_format`); the six services' `app.py`
(pass a readiness closure where they have a DB; rca/correlation also set `app.state.db_engine`);
`deploy/docker-compose.yml` (healthcheck → `/ready`, `INTELLIOPS_LOG_FORMAT=json`); `deploy/k8s/`
docs (liveness/readiness probes); `flow.md` / `architectural.md` (ADR-016 — observability &
readiness) + a `docs/OBSERVABILITY.md`.

## Scope discipline (YAGNI)

No probe-result caching, no `/metrics` overhaul (the existing per-service inconsistency is noted,
not fixed here), no log rotation/sampling (the aggregator owns retention), no distributed tracing
(a separate, bigger piece). Just: structured logs, a real readiness probe, one shared seam.
