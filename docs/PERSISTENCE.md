# IntelliOps CoE — Persistence

This document covers **where durable state lives and how it is stored**: the three
store adapters that can be backed by Postgres (Tier 1a), the two pieces of durable *runtime*
state (Tier 1b — pending approvals and the z-score baseline), the schema they share, how
migrations are applied, and how the backend is selected. Read it alongside:

- [architectural.md](../architectural.md) — *why* (ADR-014 records the store-persistence
  decisions; ADR-015 the durable runtime state).
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

## 6. Durable runtime state (Tier 1b)

The three stores above (Tier 1a) are the *records* the system writes as it works. Two further
pieces of state are **live runtime state** — held in memory as the services run and, until now,
lost on restart. Tier 1b makes both durable behind the same `STORE_BACKEND=postgres` switch, so a
restart mid-incident resumes rather than forgetting.

| Holder | What it is | Table | Interface / adapters |
|--------|-----------|-------|----------------------|
| Pending approvals | The in-flight HITL decisions a human is being asked to make | `approvals` (`id` PK, `situation_id`, `playbook_id`, `status` indexed, JSONB `payload`) | `ApprovalStore` — `InMemoryApprovalStore` (file/tests), `PostgresApprovalStore` |
| Z-score baseline | The correlator's per-metric running mean/variance (its learned "normal") | `correlation_baseline` (`metric_name` PK, `n`, `mean`, `variance`, `count`) | `BaselineStore` — `InMemoryBaselineStore` (tests), `PostgresBaselineStore`; `None` in file mode |

Both are wired through the same `make_stores(settings)` factory as `Stores.approval_store` and
`Stores.baseline_store` (the latter `None` in file mode).

### 6.1 Two holders, two deliberately different error postures

The two pieces of runtime state fail differently *on purpose*, because losing one is a correctness
failure and losing the other is recoverable.

- **Approvals — errors PROPAGATE.** A lost approval write means a human's decision (or the pending
  request itself) silently vanishes — a correctness failure the same way a lost audit record is.
  `PostgresApprovalStore` lets DB errors raise, exactly like the audit sink (§5). A dropped
  approval must be *visible*.
- **Baseline snapshot / reload — best-effort, LOGGED, never fatal.** A baseline is a
  slowly-settling statistic; missing one 30-second snapshot, or failing to reload it on boot, just
  means the detector is slightly staler or starts cold — both recoverable. So the snapshot helper
  (`_snapshot_baseline_once`) and the boot reload (`_reload_baseline`) catch every exception, log a
  warning, and continue. A persistence hiccup can **never** crash the flusher thread or the service
  boot. This mirrors the fail-safe Kubernetes remediator (ADR-007 / ADR-013), the opposite posture
  from the audit sink and the approval store.

### 6.2 Snapshot cadence

The correlation-service's existing background **flusher** thread piggybacks the baseline snapshot.
Because that loop also wakes on the (possibly shorter) situation-flush cadence, the snapshot runs
on its *own* elapsed-time schedule tracked with `time.monotonic()` — every
`baseline_snapshot_seconds` (`INTELLIOPS_BASELINE_SNAPSHOT_SECONDS`, default **30** — see
`common/config.py`), not once per wake. Each snapshot upserts one row per metric
(`ON CONFLICT (metric_name) DO UPDATE`), so the table holds the latest baseline, not a history.

### 6.3 Reload on boot

On startup the correlation-service restores its durable runtime state **before the consumer thread
starts**, so the very first events are scored against the recovered baseline — no cold-start
warm-up blackout:

1. `_reload_baseline` calls `engine.load(baseline_store.load_all())`, rebuilding each metric's
   `river.stats.Mean`/`Var` from the stored `(n, mean, variance)` via river's `_from_state`.
2. Reliability is **recovered, not stored**: the same boot path re-derives per-signature
   reliability by replaying the durable **training records** through `retrain(...)`. There is no
   separate reliability table.

If the database is unavailable at boot, both steps are caught and logged and the service starts
**cold** (empty baseline, no recovered reliability) rather than failing to boot — consistent with
the best-effort posture above. In file mode `baseline_store` is `None` and the reload is a no-op.

### 6.4 Scope notes

- **The read-model stays on event replay, not a durable snapshot.** The dashboard projection
  (read-service, ADR-009) is rebuilt from the situation/outcome event stream, so it is deliberately
  *not* part of Tier 1b — persisting it would duplicate state the event log already owns.
- **Reliability is recovered from training records, never stored as its own table** (see §6.3) —
  the labeled outcomes are the source of truth; reliability is a derived aggregate.

## 7. Testing

Postgres-backed tests are marked `@pytest.mark.postgres` and run against a **real throwaway
Postgres** via `testcontainers` (a container spun up and torn down per test session). This
gives genuine Postgres fidelity — the `ON CONFLICT` upsert and `JSONB` behavior are exercised
against Postgres itself, not a SQLite stand-in that would need different SQL.

- `pytest -m "not postgres"` runs the fast suite with **no Docker** — the file/in-memory
  adapters and the rest of the system, hermetic and quick.
- `pytest` (Docker running) runs everything, including the Postgres-marked tests.

Because `store_backend` defaults to `file` and every DB-touching test carries the `postgres`
marker, the default and fast paths never reach for a database.

A single **cross-adapter contract test** (`tests/test_store_contract.py`) pins the file /
in-memory / Postgres adapters to the *same* observable behavior via shared assertion helpers —
one per interface (`AuditSink`, `PlaybookStore`, and now `ApprovalStore`) — so the backends are
provably interchangeable. The runtime-state adapters add `tests/test_postgres_approvals.py`
(create/get, decide-as-upsert, `list_pending` excludes decided, JSONB round-trip) and
`tests/test_baseline_persistence.py`, whose headline test settles a baseline, persists its
snapshot to a real container, reloads it into a **fresh** engine, and asserts a genuine outlier
fires immediately — proving the restart skipped the warm-up blackout.
