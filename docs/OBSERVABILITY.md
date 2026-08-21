# IntelliOps CoE — Observability & readiness

This document covers **how the services report what they are doing and whether they are
healthy**: the structured JSON log format and its switches, and the split between a *liveness*
probe (`/health`) and a *readiness* probe (`/ready`) that actively pings the service's
dependencies. Read it alongside:

- [architectural.md](../architectural.md) — *why* (ADR-016 records the observability & readiness
  decisions).
- [flow.md](../flow.md) — the per-service interfaces and status (§4, §8).

> **Status.** Both capabilities are **built** and wired once in the shared app factory
> (`services/base.py` `create_app`), so every service gets them uniformly. Logging defaults to
> human-readable **text** (so a bare `pytest` and local dev stay legible); the docker-compose
> stack flips every app service to **json**. Readiness always pings the bus and, for the
> database-backed services, also pings Postgres.

---

## 1. Structured logging

Logging is configured once, in `common/logging.py` `configure_logging(service_name, settings)`,
which `create_app` calls before any handler is registered. It installs a single root
`StreamHandler` (stderr), attaches a filter that stamps the `service` name onto every record, and
sets the level — so **every** `logging` call anywhere in the service (and its libraries) is
captured, with no per-call-site change.

### 1.1 The switches

| Setting (`common/config.py`) | Env var | Default | Meaning |
|------------------------------|---------|---------|---------|
| `log_format` | `INTELLIOPS_LOG_FORMAT` | `text` | `text` \| `json` |
| `log_level`  | `INTELLIOPS_LOG_LEVEL`  | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` |

- **`text`** (the default) uses a plain formatter —
  `%(asctime)s %(levelname)s %(name)s [%(service)s] %(message)s` — so local dev and pytest output
  stay readable.
- **`json`** emits one JSON object per line (JSON-lines), which any log aggregator can parse
  without a grok pattern. The compose stack sets `INTELLIOPS_LOG_FORMAT: json` on each of the
  seven app services.

`configure_logging` is idempotent: it clears its own root handlers first, so repeated
`create_app()` calls (as in tests) never stack duplicate handlers.

### 1.2 The JSON schema

Every JSON line carries these fields (see `JsonFormatter.format`):

| Field | Source | Notes |
|-------|--------|-------|
| `ts`     | `record.created`, as an ISO-8601 UTC timestamp | e.g. `2026-08-21T09:00:00.123456+00:00` |
| `level`  | `record.levelname` | `INFO`, `WARNING`, … |
| `logger` | `record.name` | the logger's dotted name |
| `service`| the service filter | e.g. `correlation-service` |
| `msg`    | `record.getMessage()` | the fully-formatted message |
| `module` | `record.module` | source module |
| `line`   | `record.lineno` | source line number |

Two conditional additions:

- **`exc_info`** — present only when the record carries an exception; the formatted traceback
  string (from `logging.Formatter.formatException`).
- **caller-supplied `extra={...}`** — any keys passed via `logger.info(..., extra={"k": v})` are
  merged in as top-level fields. Standard `LogRecord` attributes are excluded (a reserved-key
  set), so only genuinely extra fields appear. Non-JSON-native values are stringified
  (`json.dumps(..., default=str)`).

Example line (formatted for readability; emitted as a single line):

```json
{"ts":"2026-08-21T09:00:00.123456+00:00","level":"WARNING","logger":"services.correlation.app","service":"correlation-service","msg":"store reload failed, starting cold: ...","module":"app","line":107}
```

---

## 2. Liveness vs. readiness

The two probes answer **different** questions, and both live in `create_app` so every service
exposes them identically. Both are always exempt from the auth gate (the middleware
short-circuits `/health` and `/ready` before the auth predicate runs), so a compose or k8s probe
never needs a token, in any `AUTH_MODE`.

### 2.1 `/health` — liveness (is the process up?)

```
GET /health  ->  200  {"service": "<name>", "status": "ok"}
```

`/health` checks nothing external. It returns `200` as long as the process can serve a request.
It is the **liveness** signal: if it stops answering, the process is wedged and should be
restarted. It never depends on Redis or Postgres, so a dependency outage does not cause a restart
loop.

### 2.2 `/ready` — readiness (are dependencies reachable?)

```
GET /ready  ->  200  {"ready": true}
            ->  503  {"ready": false, "failed": ["redis", "postgres"]}
```

`/ready` **actively pings** the service's dependencies on each call and reports which, if any,
are unreachable:

- **The bus is always pinged** — `app.state.bus.ping()` (`RedisBus.ping` issues a Redis `PING`).
  A failure appends `"redis"` to the `failed` list.
- **The database is pinged only when the service has one** — a service in postgres mode passes a
  `readiness` callable into `create_app` that runs `db_ready(engine)`, which opens a connection
  and executes `SELECT 1`. A failure appends `"postgres"`. In **file mode** (or for a service
  with no database at all) the engine is `None`, `db_ready` is a no-op, and the service is
  **bus-only** — it never claims a Postgres dependency it does not have.

The handler **never raises**: each check is wrapped so a failed dependency becomes an entry in
`failed`, and the response is `503` with the list, rather than a `500`. A probe consumer can read
the JSON body to see exactly which dependency is down.

Which services check the DB (they set `app.state.db_engine` and pass `readiness`):

| Service | Bus (`bus.ping`) | Postgres (`SELECT 1`) |
|---------|:----------------:|:---------------------:|
| ingestion | ✓ | — (no DB) |
| correlation | ✓ | ✓ |
| rca | ✓ | ✓ |
| action | ✓ | ✓ |
| governance | ✓ | ✓ |
| feedback | ✓ | ✓ |
| read | ✓ | — (no DB) |

(The Postgres column reflects the compose default `STORE_BACKEND=postgres`; under the `file`
default the engine is `None` and those services fall back to bus-only.)

---

## 3. Probe wiring

### 3.1 docker-compose

Each of the seven app services in `deploy/docker-compose.yml` has a healthcheck that hits
`/ready`. The shared image ships Python but **not** `curl`, so the check is a Python one-liner
rather than a `curl` call:

```yaml
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/ready').status==200 else 1)\""]
      interval: 10s
      timeout: 3s
      retries: 5
      start_period: 20s
```

`start_period: 20s` gives a freshly-started service time to connect to Redis/Postgres before a
failing probe counts against the retry budget, so a slow-but-healthy boot is not flagged
unhealthy. All app services listen on internal port `8000`.

### 3.2 Kubernetes

For a real cluster deploy, map the two probes to their matching Kubernetes probe kinds:

```yaml
    livenessProbe:
      httpGet: { path: /health, port: 8000 }
    readinessProbe:
      httpGet: { path: /ready, port: 8000 }
```

`livenessProbe -> /health` restarts a wedged process without being tripped by a dependency
outage; `readinessProbe -> /ready` pulls a pod out of the Service's endpoints while a dependency
is unreachable, without killing it, and lets it back in when the dependency recovers. See
[deploy/k8s/README.md](../deploy/k8s/README.md).
