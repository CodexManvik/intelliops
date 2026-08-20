# Postgres Persistence (Tier 1a) — Design

**Date:** 2026-08-20
**Status:** Approved (pending spec review)
**Owner:** Manvik (integration lead), toward "production-credible".
**Depends on:** the existing store interfaces (ADR-005) — this fills them in with a real DB.

## Goal

Replace the file/in-memory *stores* — the audit trail (compliance backbone), the
training records (the learning loop), and the playbook registry — with real
Postgres-backed adapters behind the existing interfaces, so they survive restarts
and are queryable at scale. Behind a `STORE_BACKEND=file|postgres` switch
defaulting to `file`, so the 236 existing tests, plain `docker compose up`, and CI
are unaffected.

This is **Tier 1a** of the production-credibility work. Durable *runtime state*
(governance's live approvals dict, the read-model projection, correlation's learned
baseline) is a separate sub-project (Tier 1b) that builds on this. Auth is Member
C's Stream D. This spec is strictly the three append/read stores → Postgres.

## Why this first

Highest compliance leverage (the audit trail is the NIST/EU-AI-Act story and cannot
be a flat file that vanishes on restart or can't be queried); most self-contained
(the `AuditSink`/`TrainingStore`/`PlaybookStore` interfaces already exist — ADR-005);
zero collision with the team; and it creates the Postgres foundation Tier 1b needs.

## Decisions (from brainstorming)

- **DB layer:** SQLAlchemy **Core** (not the ORM) + **Alembic** migrations. Typed,
  portable SQL; a real versioned-migration story (a production-credibility signal);
  lightweight for three simple tables.
- **Testing:** **testcontainers** (a real throwaway Postgres per test session).
  ~6-8s one-time container boot; tests themselves are millisecond-fast. 100%
  Postgres fidelity — the adapters use Postgres-specific SQL (JSONB, `ON CONFLICT`),
  which SQLite would test as a different dialect. Marked `@pytest.mark.postgres` so
  the fast 236-test path can skip it.
- **Schema:** **hybrid** — promote the fields we query/index to typed columns, store
  the full record as a JSONB `payload`. Fast indexed queries + lossless storage +
  schema flexibility as Pydantic contracts evolve. **The payload is the source of
  truth for reconstruction; the columns are a denormalized index that can't drift
  the model.**
- **Rollout:** `STORE_BACKEND=file|postgres` config switch, default `file`. Matches
  the established `REMEDIATOR_MODE`/`GOVERNANCE_MODE` pattern; nothing breaks by
  default; opt-in like every other real binding.

## Schema (three tables, Alembic-managed)

**`audit_records`** — append-only compliance backbone:
```
id             BIGSERIAL PRIMARY KEY
correlation_id TEXT        NOT NULL   -- INDEXED (the /audit query filters on this)
actor          TEXT        NOT NULL
action         TEXT        NOT NULL
resource       TEXT        NOT NULL
decision       TEXT        NOT NULL
ts             TIMESTAMPTZ NOT NULL   -- INDEXED (time-range)
payload        JSONB       NOT NULL   -- the full AuditRecord, lossless
```
The file version's `records()` loads *every* record into memory to filter by
`correlation_id`; Postgres pushes that into an indexed `WHERE` — a real scalability
fix, not just parity.

**`training_records`** — closed-loop learning data:
```
id           BIGSERIAL PRIMARY KEY
situation_id TEXT        NOT NULL
signature    TEXT        NOT NULL   -- INDEXED (correlation reads per-signature at retrain)
playbook_id  TEXT        NOT NULL
result       TEXT        NOT NULL   -- RemediationResult.value
worked       BOOLEAN     NOT NULL
ts           TIMESTAMPTZ NOT NULL
payload      JSONB       NOT NULL
```
`read_all()` → `SELECT payload ... ORDER BY id`.

**`playbooks`** — the CoE registry (a real upsert, not append-only):
```
id         TEXT PRIMARY KEY        -- the playbook id IS the key
name       TEXT        NOT NULL
hitl_mode  TEXT        NOT NULL    -- promoted: "which playbooks are auto?"
reversible BOOLEAN     NOT NULL
payload    JSONB       NOT NULL    -- steps, rollback_steps, match_rule (structured RemediationSteps)
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
```
`register()` → **`INSERT ... ON CONFLICT (id) DO UPDATE`** (upsert). This is why
graduation (hitl→auto re-register) works, and it's the Postgres-specific case that
justifies testcontainers over SQLite.

**Reconstruction principle:** Pydantic models serialize via `model_dump(mode="json")`
→ JSONB; read back via `Model.model_validate(row.payload)`. Always reconstruct from
`payload` — the promoted columns are only for filtering/indexing, never for
rebuilding the model, so there is one source of truth.

## Adapters

Thin classes, no ORM, SQLAlchemy Core against a shared `Engine`. Each lives next to
its file sibling in the same module (the "all AuditSink implementations in one place"
pattern holds).

**`common/db.py`** — shared foundation:
- `make_engine(database_url) -> Engine` (SQLAlchemy manages the connection pool).
- The three `Table` definitions as Core metadata — shared by adapters AND Alembic
  (Alembic autogenerates from this metadata: one source of truth for schema).
- `to_payload(model) -> dict` / `from_payload(row_payload, Model) -> Model` helpers so
  every adapter serializes/reconstructs identically.

**`PostgresAuditSink`** (in `services/governance/adapters/audit_sink.py`):
- `write(record)`: `INSERT` the promoted columns + `payload=record.model_dump(mode="json")`.
- `records(correlation_id=None)`: `SELECT payload` with an optional
  `WHERE correlation_id = :cid`, `ORDER BY id`; reconstruct each from payload.

**`PostgresTrainingStore`** (in `services/feedback/adapters/training_store.py`):
- `append(record)`: `INSERT` promoted columns + payload.
- `read_all()`: `SELECT payload ORDER BY id`; reconstruct.

**`PostgresPlaybookStore`** (in `services/governance/adapters/playbook_store.py`):
- `__init__(engine, seed_path)`: on init, upsert the seed playbooks from the YAML dir
  (same `load_seed_playbooks(seed_path)` the file store uses) so a fresh DB has the
  registry.
- `register(playbook)`: upsert (`ON CONFLICT (id) DO UPDATE`).
- `get(id)` / `list()`: `SELECT`, reconstruct from payload.

**Error posture (deliberate, and different from the K8s adapters):** persistence
errors **propagate loudly** — they do NOT fail-safe to a no-op. A DB write that
failed genuinely failed; silently losing an audit record is worse than a loud error.
SQLAlchemy exceptions surface to the caller's existing error handling. (Contrast:
the remediator fails *safe* because a missed remediation is safer than a crash; a
missed audit write is a *compliance* failure that must be visible.)

## Interface change (additive)

`AuditSink.records()` gains an optional `correlation_id: str | None = None` parameter
so the filter can be pushed into SQL. `File`/`InMemory` implement it (filtering in
Python); `Postgres` pushes it to a `WHERE`. The `/audit` endpoint passes its
`correlation_id` query param through instead of filtering the full list in Python.
Additive default — existing callers are unaffected.

## Migrations (Alembic) + engine lifecycle

- `alembic/` at repo root; `env.py` points at `common/db.py` metadata (autogenerate
  from the `Table`s — one schema source). `alembic.ini` reads
  `INTELLIOPS_DATABASE_URL` from env, never hardcoded.
- **`alembic/versions/0001_initial.py`** — the three tables + indexes. Hand-reviewed
  after autogenerate.
- Migrations are **versioned and committed** (the production-credibility signal).
- **Migrations run as a dedicated step, NEVER auto-on-app-startup** (auto-on-startup
  races when replicas boot). In compose: a one-shot `migrate` service (same image,
  runs `alembic upgrade head`, exits) that DB-using services `depends_on` with
  `condition: service_completed_successfully`. Documented command:
  `alembic upgrade head`.
- **Engine lifecycle:** one `Engine` per service process, created in the service's
  lifespan from `settings.database_url`, stored on `app.state.db_engine`. Adapters
  borrow connections per operation (`with engine.begin()`). `STORE_BACKEND=file`
  never touches the DB layer (no engine, no import cost).

## Config + dependencies

`common/config.py`:
- `store_backend: str = "file"`  # "file" | "postgres"
- `database_url: str = "postgresql+psycopg://intelliops:intelliops@localhost:5432/intelliops"`

`pyproject.toml`: `sqlalchemy`, `alembic`, `psycopg` (v3 sync driver — matches the
sync bus-consumer threading model), `testcontainers[postgres]` (dev).

## Testing

- **Session-scoped `postgres_engine` fixture:** one testcontainer Postgres per
  session; schema via `metadata.create_all` (fast) — plus one test that runs
  `alembic upgrade head` for migration fidelity. Teardown at session end.
- **Function-scoped `clean_db` fixture:** truncate the three tables between tests.
- Marked `@pytest.mark.postgres` — `pytest -m "not postgres"` runs the fast 236
  without Docker; CI runs everything.
- **Per-adapter tests** (audit / training / playbooks): write→read parity, the
  pushed-down filters, JSONB lossless round-trip, and the playbook **upsert**
  (register twice → updates — the graduation path, the SQLite-would-miss case) + seed
  present on a fresh store.
- **Parametrized cross-adapter contract test:** the same behavioral assertions run
  against `InMemory`/`File`/`Postgres` — proving all three satisfy the interface
  identically (swapping backends changes nothing observable). The real payoff.
- **Migration test:** `alembic upgrade head` on a fresh container → assert the three
  tables + indexes exist.
- The 236 existing tests keep running against `InMemory`/`File` (default `file` +
  the marker) — CI's fast path unchanged.

## Concrete change list

**New:** `common/db.py`; `alembic.ini`, `alembic/env.py`,
`alembic/versions/0001_initial.py`; `tests/conftest.py` (fixtures — or extend);
`tests/test_postgres_audit.py`, `tests/test_postgres_training.py`,
`tests/test_postgres_playbooks.py`, `tests/test_store_contract.py`,
`tests/test_migrations.py`.

**Modified:** `services/governance/adapters/audit_sink.py` (+`PostgresAuditSink`,
`records(correlation_id=None)`); `services/feedback/adapters/training_store.py`
(+`PostgresTrainingStore`); `services/governance/adapters/playbook_store.py`
(+`PostgresPlaybookStore`); **all four** store-constructing services —
`services/governance/app.py`, `services/action/app.py`, `services/feedback/app.py`,
**AND `services/rca/app.py`** (which constructs `FilePlaybookStore` + `FileAuditSink`
at rca/app.py:23-24) — get the shared `_make_stores`/factory selection so they all
use the same backend. **Split-brain hazard if rca is missed:** governance would
write playbooks to Postgres while rca reads them from files → the two diverge. The
factory (`_make_stores` + engine) is shared, not per-service-duplicated. `/audit`
passes correlation_id. `common/config.py` (`store_backend`, `database_url`);
`common/interfaces.py` (`AuditSink.records(correlation_id=None)`); `pyproject.toml`;
`deploy/docker-compose.yml` (postgres + one-shot migrate service; DB-using services
get `STORE_BACKEND=postgres`, `DATABASE_URL`, `depends_on` migrate);
`flow.md`/`architectural.md` (persistence now real; ADR-014); a `docs/PERSISTENCE.md`.

## Scope discipline (YAGNI)

No connection-pool tuning knobs, no read replicas, no ORM, no audit
partitioning/retention (a later scale concern — noted, not built), no migration of
runtime state (that's Tier 1b). Just: the three stores, on Postgres, behind a switch,
with real migrations and real tests.
