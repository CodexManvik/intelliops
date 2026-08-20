# IntelliOps CoE — Persistence

This document covers **where durable state lives and how it is stored**: the three
store adapters that can be backed by Postgres, the schema they share, how migrations
are applied, and how the backend is selected. Read it alongside:

- [architectural.md](../architectural.md) — *why* (ADR-014 records the persistence decisions).
- [flow.md](../flow.md) — the pluggable interfaces these adapters implement (§4).

> **Status.** Postgres persistence is **built** behind a config switch. `file` stays the
> default for tests and quick local dev (no Docker); `postgres` is the durable path and the
> **compose default**. Both backends are exercised by the test suite.

---

## 1. The backend switch

Three stores can be backed either by files or by Postgres:

| Store | Interface (`common/interfaces.py`) | File adapter | Postgres adapter |
|-------|------------------------------------|--------------|------------------|
| Audit log | `AuditSink` | `FileAuditSink` (JSONL) | `PostgresAuditSink` |
| Playbook registry | `PlaybookStore` | `FilePlaybookStore` (YAML dir) | `PostgresPlaybookStore` |
| Training store | `TrainingStore` | `FileTrainingStore` (JSONL) | `PostgresTrainingStore` |

Which pair is bound is a single config choice, not a per-service one:

| Setting (`common/config.py`) | Env var | Default | Meaning |
|------------------------------|---------|---------|---------|
| `store_backend` | `INTELLIOPS_STORE_BACKEND` | `file` | `file` \| `postgres` |
| `database_url` | `INTELLIOPS_DATABASE_URL` | `postgresql+psycopg://intelliops:intelliops@localhost:5432/intelliops` | SQLAlchemy URL (psycopg v3 driver) |

The switch is realized in one place — `common/stores.py` `make_stores(settings)` — which
returns the three stores (and, for Postgres, the shared `Engine`) as a `Stores` bundle. All
four store-constructing services (governance, action, feedback, rca) build their stores through
this one factory, so the backend can never split: it is not possible for governance to write
playbooks to Postgres while rca reads them from files.

## 2. The schema

Three tables, defined once with SQLAlchemy Core in `common/db.py` (a shared `MetaData`), used
both by the adapters and by Alembic's autogenerate:

| Table | Promoted columns (indexed / queried) | `payload` |
|-------|--------------------------------------|-----------|
| `audit_records` | `id`, `correlation_id`, `actor`, `action`, `resource`, `decision`, `ts` — indexed on `correlation_id` and `ts` | JSONB `AuditRecord` |
| `training_records` | `id`, `situation_id`, `signature`, `playbook_id`, `result`, `worked`, `ts` — indexed on `signature` | JSONB `TrainingRecord` |
| `playbooks` | `id` (PK), `name`, `hitl_mode`, `reversible`, `updated_at` | JSONB `Playbook` |

**Hybrid design.** Each row carries both a set of *promoted* key columns and a JSONB `payload`
holding the full serialized Pydantic record. The columns exist to be indexed and queried
(`records(correlation_id=...)`, the training `signature` lookup); the `payload` holds everything.

**The payload is the source of truth for reconstruction; the promoted columns are a denormalized
index that can't drift the model.** Reads always rebuild the Pydantic object from `payload`
(`from_payload(...)`), never from the columns. So even if a promoted column and the payload
were ever to disagree, the reconstructed record is whatever the payload says — the columns
only steer *which* rows are found, never *what* a row means. Writes populate both from the same
record in one statement, so they start consistent.

`payload` is `JSONB` on Postgres (with a generic-`JSON` variant declared for SQLite, which is
not used by any running configuration). `audit_records` and `training_records` are append-only.
`playbooks` is an upsert keyed on `id`: `PostgresPlaybookStore.register` uses Postgres
`INSERT ... ON CONFLICT (id) DO UPDATE`, so re-registering a playbook (including the seed
playbooks loaded on init) overwrites in place rather than erroring or duplicating.

## 3. Migrations

Schema changes are applied by **Alembic**, configured at the repo root (`alembic.ini`,
`alembic/`), with `alembic/versions/0001_initial.py` creating the three tables and their
indexes. Autogenerate compares against `common.db.METADATA` (wired in `alembic/env.py`), and the
database URL comes from `INTELLIOPS_DATABASE_URL` (falling back to the same localhost default),
so one migration story works against local dev, CI, and the throwaway test container.

Apply migrations with:

```bash
alembic upgrade head
```

**Migrations run as a dedicated step, never automatically on service startup.** Auto-migrating
on boot races when replicas start together (several processes trying to create the same tables).
In compose this is a one-shot **`migrate`** service that runs `uv run alembic upgrade head` and
exits; the four store services `depends_on` it with
`condition: service_completed_successfully`, so they start only after the schema is in place.

## 4. The compose Postgres service

`deploy/docker-compose.yml` runs `postgres:16-alpine` with a `pg_isready` healthcheck and a
named `postgres-data` volume for durability across restarts. **Postgres is the compose default
now** — the four store services set `INTELLIOPS_STORE_BACKEND=postgres` and point
`INTELLIOPS_DATABASE_URL` at the `postgres` container. Startup ordering is: `postgres`
(healthy) → `migrate` (completed) → the store services.

The `file` backend remains the default for tests and quick local dev where standing up Postgres
isn't worth it; `postgres` is the durable production path.

## 5. Error posture: persistence errors propagate

The Postgres adapters **let errors propagate loudly**. A failed audit write is a compliance
failure and a lost training record is a real data-loss error — both must be *visible*, not
silently swallowed. This is a deliberate contrast with the Kubernetes remediator (ADR-007 /
ADR-013), which is fail-safe by construction: there, any API error is caught and turned into a
`False` return because a remediation that can't confirm success must not pretend it succeeded.
The two postures serve opposite goals — the remediator must never act on uncertainty; the store
must never hide a lost write.

## 6. Testing

Postgres-backed tests are marked `@pytest.mark.postgres` and run against a **real throwaway
Postgres** via `testcontainers` (a container spun up and torn down per test session). This
gives genuine Postgres fidelity — the `ON CONFLICT` upsert and `JSONB` behavior are exercised
against Postgres itself, not a SQLite stand-in that would need different SQL.

- `pytest -m "not postgres"` runs the fast suite with **no Docker** — the file/in-memory
  adapters and the rest of the system, hermetic and quick.
- `pytest` (Docker running) runs everything, including the Postgres-marked tests.

Because `store_backend` defaults to `file` and every DB-touching test carries the `postgres`
marker, the default and fast paths never reach for a database.
