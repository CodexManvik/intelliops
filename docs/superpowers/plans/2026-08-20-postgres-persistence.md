# Postgres Persistence (Tier 1a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Back the audit, training, and playbook stores with real Postgres (behind a `STORE_BACKEND=file|postgres` switch defaulting to `file`), with SQLAlchemy Core + Alembic migrations and testcontainers-based tests, so nothing existing breaks and the production path is durable.

**Architecture:** Three new Postgres adapters implement the existing `AuditSink`/`TrainingStore`/`PlaybookStore` interfaces using SQLAlchemy Core against a shared `Engine`. A hybrid schema (indexed key columns + a JSONB `payload`) stores each Pydantic record; reads always reconstruct from `payload`. All four store-constructing services (governance, action, feedback, rca) select the backend via a shared factory. Alembic manages the schema; a one-shot compose `migrate` service runs `upgrade head` before the app services start.

**Tech Stack:** Python 3.11, SQLAlchemy Core, Alembic, psycopg (v3, sync), Postgres 16, testcontainers, Pydantic v2, FastAPI.

**Spec:** `docs/superpowers/specs/2026-08-20-postgres-persistence-design.md`

## Global Constraints

- The 236 existing tests MUST stay green and stay infra-free. `STORE_BACKEND` defaults to `file`; DB tests are marked `@pytest.mark.postgres` and only run when Docker is available. The default `pytest` (and `pytest -m "not postgres"`) never needs Postgres.
- The existing `AuditSink`/`TrainingStore`/`PlaybookStore` **interfaces do not change** except one additive one: `AuditSink.records()` gains `correlation_id: str | None = None` (defaulted — existing callers unaffected).
- **Reconstruction is always from the JSONB `payload`** (`Model.model_validate(row.payload)`), never from the promoted columns. The columns are a denormalized index only.
- **Persistence errors propagate** (they do NOT fail-safe to a no-op — a lost audit write is a compliance failure). SQLAlchemy exceptions surface to the caller.
- **All four** store-constructing services must use the SAME backend (governance, action, feedback, rca) — a shared factory, not per-service duplication. Missing rca (rca/app.py:23-24) split-brains playbooks between Postgres and files.
- Migrations run as a dedicated step (`alembic upgrade head`), NEVER auto-on-app-startup.
- Pydantic → JSONB via `model_dump(mode="json")`; enums store as their `.value`.
- Python: `uv run pytest`, `uv run ruff check`. Commit after each task; messages end with:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

## File Structure

**New:**
- `common/db.py` — `make_engine`, the three Core `Table`s (shared metadata `METADATA`), `to_payload`/`from_payload` helpers
- `alembic.ini`, `alembic/env.py`, `alembic/versions/0001_initial.py`
- `tests/db_fixtures.py` (or extend `tests/conftest.py`) — `postgres_engine` (session) + `clean_db` (function) fixtures + the `postgres` marker
- `tests/test_postgres_audit.py`, `tests/test_postgres_training.py`, `tests/test_postgres_playbooks.py`, `tests/test_store_contract.py`, `tests/test_migrations.py`
- `docs/PERSISTENCE.md`

**Modified:**
- `services/governance/adapters/audit_sink.py` (+`PostgresAuditSink`; `records(correlation_id=None)` on all three)
- `services/feedback/adapters/training_store.py` (+`PostgresTrainingStore`)
- `services/governance/adapters/playbook_store.py` (+`PostgresPlaybookStore`)
- `common/stores.py` (NEW) — the shared `make_stores(settings)` factory
- `services/governance/app.py`, `services/action/app.py`, `services/feedback/app.py`, `services/rca/app.py` — use the factory; `/audit` passes `correlation_id`
- `common/config.py` (`store_backend`, `database_url`)
- `common/interfaces.py` (`AuditSink.records(correlation_id=None)`)
- `pyproject.toml`, `deploy/docker-compose.yml`, `flow.md`, `architectural.md`

---

## Task 1: Dependencies + `common/db.py` (engine + table metadata + helpers)

**Files:**
- Modify: `pyproject.toml`
- Create: `common/db.py`
- Test: `tests/test_db_metadata.py`

**Interfaces:**
- Produces: `common.db.METADATA` (SQLAlchemy `MetaData`); `common.db.audit_records`, `training_records`, `playbooks` (`Table` objects); `make_engine(database_url: str) -> Engine`; `to_payload(model) -> dict`; `from_payload(payload: dict, model_cls) -> BaseModel`.

- [ ] **Step 1: Add dependencies**

In `pyproject.toml` `dependencies`, add: `"sqlalchemy>=2.0"`, `"alembic>=1.13"`, `"psycopg[binary]>=3.1"`. In a dev/optional group (or `dependencies` if no dev group exists — check the file), add `"testcontainers[postgres]>=4.0"`. Run `uv sync`. Commit `pyproject.toml` + `uv.lock` at the end of this task.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_db_metadata.py
from common.db import METADATA, audit_records, training_records, playbooks, to_payload, from_payload

def test_tables_registered():
    names = set(METADATA.tables)
    assert {"audit_records", "training_records", "playbooks"} <= names

def test_audit_columns():
    cols = {c.name for c in audit_records.columns}
    assert {"id", "correlation_id", "actor", "action", "resource", "decision", "ts", "payload"} <= cols

def test_playbooks_pk_is_id():
    assert [c.name for c in playbooks.primary_key.columns] == ["id"]

def test_payload_roundtrip():
    from datetime import UTC, datetime
    from common.contracts import AuditRecord
    rec = AuditRecord(actor="a", action="x", resource="r", decision="allow",
                      ts=datetime(2026, 8, 20, tzinfo=UTC), correlation_id="sit-1")
    payload = to_payload(rec)
    assert isinstance(payload, dict) and payload["correlation_id"] == "sit-1"
    back = from_payload(payload, AuditRecord)
    assert back == rec
```

- [ ] **Step 3: Run to verify fail**

Run: `uv run pytest tests/test_db_metadata.py -v`
Expected: FAIL — `common.db` does not exist.

- [ ] **Step 4: Implement `common/db.py`**

```python
# common/db.py
"""SQLAlchemy Core foundation for the Postgres store adapters.

The three tables are defined once here (shared by the adapters and by Alembic's
autogenerate) using the hybrid schema: promoted key columns for indexed queries
plus a JSONB `payload` that is the source of truth for reconstructing the
Pydantic record. Reads always rebuild from `payload`, never from the columns."""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import (
    JSON, BigInteger, Boolean, Column, DateTime, Index, MetaData, String, Table,
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine

METADATA = MetaData()

# JSONB on Postgres; falls back to generic JSON on other dialects (none used).
_JSON = JSONB().with_variant(JSON(), "sqlite")

audit_records = Table(
    "audit_records", METADATA,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("correlation_id", String, nullable=False),
    Column("actor", String, nullable=False),
    Column("action", String, nullable=False),
    Column("resource", String, nullable=False),
    Column("decision", String, nullable=False),
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("payload", _JSON, nullable=False),
    Index("ix_audit_correlation_id", "correlation_id"),
    Index("ix_audit_ts", "ts"),
)

training_records = Table(
    "training_records", METADATA,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("situation_id", String, nullable=False),
    Column("signature", String, nullable=False),
    Column("playbook_id", String, nullable=False),
    Column("result", String, nullable=False),
    Column("worked", Boolean, nullable=False),
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("payload", _JSON, nullable=False),
    Index("ix_training_signature", "signature"),
)

playbooks = Table(
    "playbooks", METADATA,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("hitl_mode", String, nullable=False),
    Column("reversible", Boolean, nullable=False),
    Column("payload", _JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


def make_engine(database_url: str) -> Engine:
    return create_engine(database_url, future=True, pool_pre_ping=True)


def to_payload(model: BaseModel) -> dict:
    return model.model_dump(mode="json")


def from_payload(payload: dict, model_cls: type[BaseModel]) -> BaseModel:
    return model_cls.model_validate(payload)
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_db_metadata.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock common/db.py tests/test_db_metadata.py
git commit -m "feat(db): SQLAlchemy Core metadata + engine/payload helpers for Postgres stores"
```

---

## Task 2: The testcontainers fixture (session Postgres + per-test clean)

**Files:**
- Create: `tests/db_fixtures.py`
- Modify: `pyproject.toml` (register the `postgres` marker) or `pytest.ini`/`tool.pytest` in pyproject
- Test: `tests/test_db_fixtures_smoke.py`

**Interfaces:**
- Produces: pytest fixtures `postgres_engine` (session-scoped `Engine` against a real testcontainer Postgres with the schema created) and `clean_db` (function-scoped; truncates the three tables). The `postgres` marker.

- [ ] **Step 1: Register the marker**

In `pyproject.toml`, under `[tool.pytest.ini_options]` (create if absent), add:
```toml
markers = ["postgres: tests that require a real Postgres (testcontainers + Docker)"]
```

- [ ] **Step 2: Write the fixtures**

```python
# tests/db_fixtures.py
"""Shared fixtures for the Postgres-backed store tests.

A single testcontainer Postgres per session (the ~6-8s boot cost, amortized);
the schema is created once via METADATA; each test truncates between runs so
they don't cross-contaminate. Import these where needed, or make them global by
importing from conftest."""

import pytest
from sqlalchemy import text

from common.db import METADATA, make_engine


@pytest.fixture(scope="session")
def postgres_engine():
    from testcontainers.postgres import PostgresContainer
    with PostgresContainer("postgres:16-alpine") as pg:
        # testcontainers returns a psycopg2 URL by default; force psycopg (v3).
        url = pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql+psycopg://")
        engine = make_engine(url)
        METADATA.create_all(engine)
        yield engine
        engine.dispose()


@pytest.fixture()
def clean_db(postgres_engine):
    with postgres_engine.begin() as conn:
        conn.execute(text("TRUNCATE audit_records, training_records, playbooks RESTART IDENTITY CASCADE"))
    yield postgres_engine
```

To make them available to all test files, re-export from `tests/conftest.py`:
```python
# add to tests/conftest.py (create if absent)
from tests.db_fixtures import postgres_engine, clean_db  # noqa: F401
```

- [ ] **Step 3: Write a smoke test proving the fixture works**

```python
# tests/test_db_fixtures_smoke.py
import pytest
from sqlalchemy import text

@pytest.mark.postgres
def test_container_up_and_schema_present(clean_db):
    with clean_db.connect() as conn:
        tables = conn.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
        )).scalars().all()
    assert {"audit_records", "training_records", "playbooks"} <= set(tables)
```

- [ ] **Step 4: Run it (needs Docker)**

Run: `uv run pytest tests/test_db_fixtures_smoke.py -v -m postgres`
Expected: PASS (Docker must be running — it pulls postgres:16-alpine the first time). Also confirm the default path skips it: `uv run pytest -m "not postgres" -q` runs the existing suite with no Postgres.

- [ ] **Step 5: Commit**

```bash
git add tests/db_fixtures.py tests/conftest.py tests/test_db_fixtures_smoke.py pyproject.toml
git commit -m "test(db): testcontainers Postgres fixtures + the postgres marker"
```

---

## Task 3: PostgresAuditSink (+ records(correlation_id) on all AuditSinks)

**Files:**
- Modify: `common/interfaces.py`, `services/governance/adapters/audit_sink.py`
- Test: `tests/test_postgres_audit.py`

**Interfaces:**
- Consumes: `common.db` tables + helpers (Task 1); `clean_db` fixture (Task 2).
- Produces: `PostgresAuditSink(engine)` with `write(record)` and `records(correlation_id=None) -> list[AuditRecord]`. `FileAuditSink.records` and `InMemoryAuditSink.records` gain the same optional `correlation_id` param. `AuditSink` protocol updated.

- [ ] **Step 1: Update the interface + File/InMemory signatures**

In `common/interfaces.py`, change `AuditSink`:
```python
class AuditSink(Protocol):
    def write(self, record: AuditRecord) -> None: ...
    def records(self, correlation_id: str | None = None) -> list[AuditRecord]: ...
```
In `services/governance/adapters/audit_sink.py`, add the param to both existing `records`:
- `InMemoryAuditSink.records`: `return [r for r in self._records if correlation_id is None or r.correlation_id == correlation_id]`
- `FileAuditSink.records`: read all as now, then `return [r for r in out if correlation_id is None or r.correlation_id == correlation_id]`

- [ ] **Step 2: Write the failing test**

```python
# tests/test_postgres_audit.py
from datetime import UTC, datetime
import pytest
from common.contracts import AuditRecord
from services.governance.adapters.audit_sink import PostgresAuditSink

def _rec(cid, actor="a"):
    return AuditRecord(actor=actor, action="execute", resource="playbook:x",
                       decision="allow", ts=datetime(2026, 8, 20, tzinfo=UTC), correlation_id=cid)

@pytest.mark.postgres
def test_write_and_read_all(clean_db):
    s = PostgresAuditSink(clean_db)
    s.write(_rec("sit-1")); s.write(_rec("sit-2"))
    got = s.records()
    assert len(got) == 2 and {r.correlation_id for r in got} == {"sit-1", "sit-2"}

@pytest.mark.postgres
def test_filter_by_correlation_id(clean_db):
    s = PostgresAuditSink(clean_db)
    s.write(_rec("sit-1")); s.write(_rec("sit-1")); s.write(_rec("sit-2"))
    assert len(s.records(correlation_id="sit-1")) == 2
    assert len(s.records(correlation_id="nope")) == 0

@pytest.mark.postgres
def test_jsonb_roundtrip_lossless(clean_db):
    s = PostgresAuditSink(clean_db)
    original = _rec("sit-9", actor="oncall-alice")
    s.write(original)
    assert s.records()[0] == original   # reconstructed from payload, field-for-field
```

- [ ] **Step 3: Run to verify fail**

Run: `uv run pytest tests/test_postgres_audit.py -v -m postgres`
Expected: FAIL — `PostgresAuditSink` not defined.

- [ ] **Step 4: Implement PostgresAuditSink**

Append to `services/governance/adapters/audit_sink.py` (keep the file/in-memory classes):

```python
from sqlalchemy import select

from common.db import audit_records, from_payload, to_payload


class PostgresAuditSink:
    """AuditSink backed by Postgres. Errors propagate — a failed audit write is a
    compliance failure and must be visible, not silently swallowed."""

    def __init__(self, engine) -> None:
        self._engine = engine

    def write(self, record: AuditRecord) -> None:
        with self._engine.begin() as conn:
            conn.execute(audit_records.insert().values(
                correlation_id=record.correlation_id, actor=record.actor,
                action=record.action, resource=record.resource,
                decision=record.decision, ts=record.ts, payload=to_payload(record)))

    def records(self, correlation_id: str | None = None) -> list[AuditRecord]:
        stmt = select(audit_records.c.payload)
        if correlation_id is not None:
            stmt = stmt.where(audit_records.c.correlation_id == correlation_id)
        stmt = stmt.order_by(audit_records.c.id)
        with self._engine.connect() as conn:
            return [from_payload(row.payload, AuditRecord) for row in conn.execute(stmt)]
```

- [ ] **Step 5: Run to verify pass (+ existing governance tests unaffected by the interface change)**

Run: `uv run pytest tests/test_postgres_audit.py -m postgres services/governance/tests/ -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add common/interfaces.py services/governance/adapters/audit_sink.py tests/test_postgres_audit.py
git commit -m "feat(governance): PostgresAuditSink + records(correlation_id) filter"
```

---

## Task 4: PostgresTrainingStore

**Files:**
- Modify: `services/feedback/adapters/training_store.py`
- Test: `tests/test_postgres_training.py`

**Interfaces:**
- Produces: `PostgresTrainingStore(engine)` with `append(record)` and `read_all() -> list[TrainingRecord]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_postgres_training.py
from datetime import UTC, datetime
import pytest
from common.contracts import RemediationResult, TrainingRecord
from services.feedback.adapters.training_store import PostgresTrainingStore

def _rec(sig, worked=True):
    return TrainingRecord(situation_id="sit-1", signature=sig, playbook_id="restart-pod",
                          result=RemediationResult.SUCCESS, worked=worked,
                          ts=datetime(2026, 8, 20, tzinfo=UTC))

@pytest.mark.postgres
def test_append_and_read_all_in_order(clean_db):
    s = PostgresTrainingStore(clean_db)
    s.append(_rec("aaa")); s.append(_rec("bbb"))
    got = s.read_all()
    assert [r.signature for r in got] == ["aaa", "bbb"]

@pytest.mark.postgres
def test_training_roundtrip_lossless(clean_db):
    s = PostgresTrainingStore(clean_db)
    original = _rec("sig-x", worked=False)
    s.append(original)
    assert s.read_all()[0] == original
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/test_postgres_training.py -v -m postgres`
Expected: FAIL — not defined.

- [ ] **Step 3: Implement**

Append to `services/feedback/adapters/training_store.py`:

```python
from sqlalchemy import select

from common.db import from_payload, to_payload, training_records


class PostgresTrainingStore:
    def __init__(self, engine) -> None:
        self._engine = engine

    def append(self, record: TrainingRecord) -> None:
        result = record.result.value if hasattr(record.result, "value") else str(record.result)
        with self._engine.begin() as conn:
            conn.execute(training_records.insert().values(
                situation_id=record.situation_id, signature=record.signature,
                playbook_id=record.playbook_id, result=result, worked=record.worked,
                ts=record.ts, payload=to_payload(record)))

    def read_all(self) -> list[TrainingRecord]:
        stmt = select(training_records.c.payload).order_by(training_records.c.id)
        with self._engine.connect() as conn:
            return [from_payload(row.payload, TrainingRecord) for row in conn.execute(stmt)]
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_postgres_training.py -m postgres services/feedback/tests/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/feedback/adapters/training_store.py tests/test_postgres_training.py
git commit -m "feat(feedback): PostgresTrainingStore"
```

---

## Task 5: PostgresPlaybookStore (with upsert + seed-on-init)

**Files:**
- Modify: `services/governance/adapters/playbook_store.py`
- Test: `tests/test_postgres_playbooks.py`

**Interfaces:**
- Produces: `PostgresPlaybookStore(engine, seed_path)` with `register(playbook)` (upsert), `get(id) -> Playbook | None`, `list() -> list[Playbook]`. Seeds from `load_seed_playbooks(seed_path)` on init (upsert each).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_postgres_playbooks.py
import pytest
from common.contracts import HitlMode, Playbook, RemediationStep
from services.governance.adapters.playbook_store import PostgresPlaybookStore

def _pb(pid="restart-pod", mode=HitlMode.HITL):
    return Playbook(id=pid, name="Restart", match_rule="x",
                    steps=[RemediationStep(action="restart")], hitl_mode=mode,
                    reversible=True, rollback_steps=[RemediationStep(action="restart")])

@pytest.mark.postgres
def test_register_get_list(clean_db):
    s = PostgresPlaybookStore(clean_db, seed_path="deploy/playbooks")
    s.register(_pb("my-pb"))
    got = s.get("my-pb")
    assert got is not None and got.id == "my-pb" and got.steps[0].action == "restart"
    assert "my-pb" in {p.id for p in s.list()}

@pytest.mark.postgres
def test_register_twice_upserts(clean_db):
    s = PostgresPlaybookStore(clean_db, seed_path="deploy/playbooks")
    s.register(_pb("g", mode=HitlMode.HITL))
    s.register(_pb("g", mode=HitlMode.AUTO))   # graduation: same id, new mode
    assert s.get("g").hitl_mode == HitlMode.AUTO
    assert len([p for p in s.list() if p.id == "g"]) == 1   # not duplicated

@pytest.mark.postgres
def test_seed_playbooks_present_on_fresh_store(clean_db):
    s = PostgresPlaybookStore(clean_db, seed_path="deploy/playbooks")
    ids = {p.id for p in s.list()}
    assert "restart-pod" in ids and "scale-service" in ids
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/test_postgres_playbooks.py -v -m postgres`
Expected: FAIL — not defined.

- [ ] **Step 3: Implement**

Append to `services/governance/adapters/playbook_store.py`:

```python
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from common.db import from_payload, playbooks, to_payload


class PostgresPlaybookStore:
    def __init__(self, engine, seed_path: str) -> None:
        self._engine = engine
        for pb in load_seed_playbooks(seed_path):
            self.register(pb)

    def register(self, playbook: Playbook) -> None:
        mode = playbook.hitl_mode.value if hasattr(playbook.hitl_mode, "value") else str(playbook.hitl_mode)
        values = dict(id=playbook.id, name=playbook.name, hitl_mode=mode,
                      reversible=playbook.reversible, payload=to_payload(playbook),
                      updated_at=datetime.now(UTC))
        stmt = pg_insert(playbooks).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[playbooks.c.id],
            set_=dict(name=stmt.excluded.name, hitl_mode=stmt.excluded.hitl_mode,
                      reversible=stmt.excluded.reversible, payload=stmt.excluded.payload,
                      updated_at=stmt.excluded.updated_at))
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def get(self, playbook_id: str) -> Playbook | None:
        stmt = select(playbooks.c.payload).where(playbooks.c.id == playbook_id)
        with self._engine.connect() as conn:
            row = conn.execute(stmt).first()
        return from_payload(row.payload, Playbook) if row else None

    def list(self) -> list[Playbook]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(playbooks.c.payload).order_by(playbooks.c.id)).all()
        return [from_payload(r.payload, Playbook) for r in rows]
```

Note: `on_conflict_do_update` comes from `sqlalchemy.dialects.postgresql.insert` (the pg-specific insert) — confirm the import path resolves in the installed SQLAlchemy 2.x. This is the Postgres-specific upsert the testcontainer verifies.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_postgres_playbooks.py -m postgres services/governance/tests/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/governance/adapters/playbook_store.py tests/test_postgres_playbooks.py
git commit -m "feat(governance): PostgresPlaybookStore with ON CONFLICT upsert + seed-on-init"
```

---

## Task 6: Alembic migrations + the migration fidelity test

**Files:**
- Create: `alembic.ini`, `alembic/env.py`, `alembic/versions/0001_initial.py`, `tests/test_migrations.py`

**Interfaces:**
- Produces: `alembic upgrade head` creates the three tables + indexes from `common.db.METADATA`.

- [ ] **Step 1: Scaffold alembic**

Run: `uv run alembic init alembic` (creates `alembic.ini` + `alembic/`). Then edit:
- `alembic.ini`: comment out the hardcoded `sqlalchemy.url`; the URL comes from env (set in `env.py`).
- `alembic/env.py`: set `target_metadata = METADATA` (import from `common.db`), and read the URL from `INTELLIOPS_DATABASE_URL`:

```python
# in alembic/env.py, near the top after imports
import os
from common.db import METADATA
target_metadata = METADATA
config.set_main_option("sqlalchemy.url",
                       os.environ.get("INTELLIOPS_DATABASE_URL",
                                      "postgresql+psycopg://intelliops:intelliops@localhost:5432/intelliops"))
```

- [ ] **Step 2: Write the initial migration (hand-authored, matching METADATA)**

```python
# alembic/versions/0001_initial.py
"""initial store tables"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_records",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("correlation_id", sa.String, nullable=False),
        sa.Column("actor", sa.String, nullable=False),
        sa.Column("action", sa.String, nullable=False),
        sa.Column("resource", sa.String, nullable=False),
        sa.Column("decision", sa.String, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
    )
    op.create_index("ix_audit_correlation_id", "audit_records", ["correlation_id"])
    op.create_index("ix_audit_ts", "audit_records", ["ts"])
    op.create_table(
        "training_records",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("situation_id", sa.String, nullable=False),
        sa.Column("signature", sa.String, nullable=False),
        sa.Column("playbook_id", sa.String, nullable=False),
        sa.Column("result", sa.String, nullable=False),
        sa.Column("worked", sa.Boolean, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
    )
    op.create_index("ix_training_signature", "training_records", ["signature"])
    op.create_table(
        "playbooks",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("hitl_mode", sa.String, nullable=False),
        sa.Column("reversible", sa.Boolean, nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("playbooks")
    op.drop_table("training_records")
    op.drop_table("audit_records")
```

- [ ] **Step 3: Write the migration fidelity test**

```python
# tests/test_migrations.py
import pytest
from sqlalchemy import text

@pytest.mark.postgres
def test_alembic_upgrade_creates_schema():
    from testcontainers.postgres import PostgresContainer
    from alembic.config import Config
    from alembic import command
    import os
    with PostgresContainer("postgres:16-alpine") as pg:
        url = pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql+psycopg://")
        os.environ["INTELLIOPS_DATABASE_URL"] = url
        cfg = Config("alembic.ini")
        command.upgrade(cfg, "head")
        from common.db import make_engine
        with make_engine(url).connect() as conn:
            tables = set(conn.execute(text(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
            )).scalars().all())
            idx = set(conn.execute(text(
                "SELECT indexname FROM pg_indexes WHERE schemaname='public'"
            )).scalars().all())
    assert {"audit_records", "training_records", "playbooks"} <= tables
    assert {"ix_audit_correlation_id", "ix_training_signature"} <= idx
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_migrations.py -v -m postgres`
Expected: PASS (its own throwaway container — independent of the session fixture).

- [ ] **Step 5: Commit**

```bash
git add alembic.ini alembic/ tests/test_migrations.py
git commit -m "feat(db): alembic migrations (0001 initial schema) + fidelity test"
```

---

## Task 7: The cross-adapter contract test

**Files:**
- Create: `tests/test_store_contract.py`

**Interfaces:**
- Consumes: all three backends of each store.
- Produces: parametrized tests proving `InMemory`/`File`/`Postgres` satisfy each interface identically.

- [ ] **Step 1: Write the contract test**

```python
# tests/test_store_contract.py
"""Every store backend must be observably interchangeable behind its interface."""
from datetime import UTC, datetime
import pytest
from common.contracts import AuditRecord, HitlMode, Playbook, RemediationStep


def _audit(cid):
    return AuditRecord(actor="a", action="x", resource="r", decision="allow",
                       ts=datetime(2026, 8, 20, tzinfo=UTC), correlation_id=cid)


# --- audit: parametrize over backends, each yielding a fresh empty sink ---
def _inmem_audit():
    from services.governance.adapters.audit_sink import InMemoryAuditSink
    return InMemoryAuditSink()

def _file_audit(tmp_path):
    from services.governance.adapters.audit_sink import FileAuditSink
    return FileAuditSink(str(tmp_path / "audit.jsonl"))

@pytest.mark.parametrize("kind", ["inmem", "file", "postgres"])
def test_audit_backends_agree(kind, tmp_path, request):
    if kind == "inmem":
        sink = _inmem_audit()
    elif kind == "file":
        sink = _file_audit(tmp_path)
    else:
        pg = request.getfixturevalue("clean_db")   # only pulls the container for the postgres param
        from services.governance.adapters.audit_sink import PostgresAuditSink
        sink = PostgresAuditSink(pg)
    sink.write(_audit("sit-1")); sink.write(_audit("sit-2")); sink.write(_audit("sit-1"))
    assert len(sink.records()) == 3
    assert len(sink.records(correlation_id="sit-1")) == 2
    assert sink.records(correlation_id="sit-1")[0] == _audit("sit-1")


# --- playbooks: same idea ---
def _pb(pid, mode=HitlMode.HITL):
    return Playbook(id=pid, name="n", match_rule="x", steps=[RemediationStep(action="restart")],
                    hitl_mode=mode, reversible=True, rollback_steps=[])

@pytest.mark.parametrize("kind", ["inmem", "file", "postgres"])
def test_playbook_backends_agree(kind, tmp_path, request):
    if kind == "inmem":
        from services.governance.adapters.playbook_store import InMemoryPlaybookStore
        store = InMemoryPlaybookStore()
    elif kind == "file":
        from services.governance.adapters.playbook_store import FilePlaybookStore
        store = FilePlaybookStore(str(tmp_path))
    else:
        pg = request.getfixturevalue("clean_db")
        from services.governance.adapters.playbook_store import PostgresPlaybookStore
        store = PostgresPlaybookStore(pg, seed_path=str(tmp_path))  # empty seed dir
    store.register(_pb("p1", HitlMode.HITL))
    store.register(_pb("p1", HitlMode.AUTO))   # upsert on every backend
    assert store.get("p1").hitl_mode == HitlMode.AUTO
    assert len([p for p in store.list() if p.id == "p1"]) == 1
```

Note: the `postgres` param of each parametrized test pulls the `clean_db` fixture only for that param (via `request.getfixturevalue`), so the `inmem`/`file` params run with no Docker. But the WHOLE test file still gets the `postgres` marker applied per-param is awkward — simplest: mark the postgres params to skip when Docker absent by keeping them un-marked but tolerant. Since `clean_db` is only fetched in the postgres branch, running `pytest -m "not postgres"` will still execute inmem/file params fine; the postgres param will try to fetch the fixture. To keep `-m "not postgres"` clean, split the postgres param into its own `@pytest.mark.postgres` test instead of a param, OR accept that this file needs Docker. Implementer: split the postgres assertions into a separate `@pytest.mark.postgres`-marked test that reuses the same assertion helper, so the inmem/file contract runs infra-free and the postgres contract runs under the marker. (Refactor the shared assertions into a helper `_assert_audit_contract(sink)` / `_assert_playbook_contract(store)` and call from both.)

- [ ] **Step 2: Run to verify (all params)**

Run: `uv run pytest tests/test_store_contract.py -v` (with Docker) and `uv run pytest tests/test_store_contract.py -v -m "not postgres"` (infra-free params pass).
Expected: PASS in both; the postgres-marked assertions only run with Docker.

- [ ] **Step 3: Commit**

```bash
git add tests/test_store_contract.py
git commit -m "test(db): cross-adapter contract — inmem/file/postgres interchangeable"
```

---

## Task 8: The `make_stores` factory + wire all four services + config + compose

**Files:**
- Create: `common/stores.py`
- Modify: `common/config.py`; `services/governance/app.py`, `services/action/app.py`, `services/feedback/app.py`, `services/rca/app.py`; `services/governance/app.py` `/audit` endpoint; `deploy/docker-compose.yml`
- Test: `tests/test_store_factory.py`

**Interfaces:**
- Consumes: the three Postgres adapters + the file/in-memory ones; `make_engine`.
- Produces: `make_stores(settings) -> Stores` (a small dataclass/namedtuple with `audit_sink`, `playbook_store`, `training_store`, and `engine`), selecting file vs postgres by `settings.store_backend`.

- [ ] **Step 1: Add config**

In `common/config.py`, after the k8s settings:
```python
    store_backend: str = "file"   # "file" | "postgres"
    database_url: str = "postgresql+psycopg://intelliops:intelliops@localhost:5432/intelliops"
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_store_factory.py
from common.stores import make_stores
from services.governance.adapters.audit_sink import FileAuditSink

class _S:
    store_backend = "file"
    database_url = "postgresql+psycopg://x"
    audit_store_path = "data/audit.jsonl"
    training_store_path = "data/training.jsonl"
    playbook_store_path = "deploy/playbooks"

def test_file_backend_builds_file_adapters():
    s = make_stores(_S())
    assert isinstance(s.audit_sink, FileAuditSink)
    assert s.engine is None

def test_postgres_backend_selected(monkeypatch):
    # don't actually connect — just assert the postgres classes are chosen.
    from services.governance.adapters.audit_sink import PostgresAuditSink
    class _P(_S): store_backend = "postgres"
    # make_engine is called but lazy/pool — constructing the engine object does not connect.
    s = make_stores(_P())
    assert isinstance(s.audit_sink, PostgresAuditSink)
    assert s.engine is not None
```

- [ ] **Step 3: Run to verify fail**

Run: `uv run pytest tests/test_store_factory.py -v`
Expected: FAIL — `common.stores` not defined.

- [ ] **Step 4: Implement the factory**

```python
# common/stores.py
"""One factory selecting the store backend for ALL store-constructing services.

Shared (not per-service) so governance/action/feedback/rca never diverge — a
split backend would, e.g., have governance writing playbooks to Postgres while
rca reads them from files."""

from __future__ import annotations

from dataclasses import dataclass

from services.feedback.adapters.training_store import FileTrainingStore, PostgresTrainingStore
from services.governance.adapters.audit_sink import FileAuditSink, PostgresAuditSink
from services.governance.adapters.playbook_store import FilePlaybookStore, PostgresPlaybookStore


@dataclass
class Stores:
    audit_sink: object
    playbook_store: object
    training_store: object
    engine: object | None


def make_stores(settings) -> Stores:
    if settings.store_backend == "postgres":
        from common.db import make_engine
        engine = make_engine(settings.database_url)
        return Stores(
            audit_sink=PostgresAuditSink(engine),
            playbook_store=PostgresPlaybookStore(engine, seed_path=settings.playbook_store_path),
            training_store=PostgresTrainingStore(engine),
            engine=engine,
        )
    return Stores(
        audit_sink=FileAuditSink(settings.audit_store_path),
        playbook_store=FilePlaybookStore(settings.playbook_store_path),
        training_store=FileTrainingStore(settings.training_store_path),
        engine=None,
    )
```

- [ ] **Step 5: Wire the four services to the factory**

Each service currently constructs stores inline. Replace with `make_stores`:

- `services/governance/app.py` `_init_state`: `stores = make_stores(settings); app.state.audit_sink = stores.audit_sink; app.state.playbook_store = stores.playbook_store` (rbac/approvals unchanged). Update the `/audit` endpoint: `records = app.state.audit_sink.records(correlation_id)` (push the filter down; remove the Python-side filter).
- `services/action/app.py`: it builds `FilePlaybookStore` (line ~69) and `FileAuditSink` (in the in-process gate, line ~35). Use `make_stores(settings)` for both — `store = stores.playbook_store`, and the gate's audit sink = `stores.audit_sink`.
- `services/feedback/app.py`: replace `FileTrainingStore(...)` with `make_stores(settings).training_store`.
- `services/rca/app.py` (lines 23-24): replace `FilePlaybookStore`/`FileAuditSink` with `make_stores(settings)`'s `playbook_store`/`audit_sink`.

Each: import `from common.stores import make_stores`; drop the now-unused direct File-adapter imports (strict ruff will flag them).

- [ ] **Step 6: Compose — Postgres + one-shot migrate service**

In `deploy/docker-compose.yml`:
- Add a `postgres` service (`image: postgres:16-alpine`, `POSTGRES_USER/PASSWORD/DB=intelliops`, a healthcheck `pg_isready`, a named volume for data, port `5432`).
- Add a one-shot `migrate` service (the shared build image, `command: uv run alembic upgrade head`, `INTELLIOPS_DATABASE_URL=postgresql+psycopg://intelliops:intelliops@postgres:5432/intelliops`, `depends_on: postgres (service_healthy)`).
- On the four store-using services (ingestion doesn't use stores; correlation reads training via feedback's store? — check: correlation retrain reads the training store, so include it if it constructs one), add: `INTELLIOPS_STORE_BACKEND=postgres`, `INTELLIOPS_DATABASE_URL=...@postgres:5432/intelliops`, and `depends_on: migrate (service_completed_successfully)` + `postgres (service_healthy)`.
- Keep the base stack's default behavior documented: without these env vars the services default to `file`. (This overlay-style change makes Postgres the compose default; note it in the commit.)

Validate: `docker compose -f deploy/docker-compose.yml config >/dev/null && echo OK`.

- [ ] **Step 7: Run tests + confirm the whole suite**

Run: `uv run pytest -q` (with Docker) and `uv run pytest -q -m "not postgres"`.
Expected: all pass. The factory default (`file`) keeps every existing service test green.

- [ ] **Step 8: Commit**

```bash
git add common/stores.py common/config.py services/governance/app.py services/action/app.py services/feedback/app.py services/rca/app.py deploy/docker-compose.yml tests/test_store_factory.py
git commit -m "feat(stores): STORE_BACKEND factory across all 4 services; compose postgres+migrate"
```

---

## Task 9: Full verification + docs

**Files:**
- Create: `docs/PERSISTENCE.md`
- Modify: `flow.md`, `architectural.md`
- Verification: full suite

- [ ] **Step 1: Full suite + ruff**

Run: `uv run pytest -q` (Docker up), then `uv run pytest -q -m "not postgres"`, then `uv run ruff check` (and `--fix` for import nits). All green/clean.

- [ ] **Step 2: `docs/PERSISTENCE.md`**

Document: the `STORE_BACKEND=file|postgres` switch + `DATABASE_URL`; the schema (the three tables, hybrid columns+JSONB, "payload is the source of truth"); running migrations (`alembic upgrade head`, and the compose one-shot `migrate` service); the compose Postgres service; and the honest note that `file` stays the default for tests/quick-dev while `postgres` is the durable production path.

- [ ] **Step 3: flow.md + architectural.md**

- `flow.md` §4 interfaces table: move the `AuditSink`/`TrainingStore`/`PlaybookStore` Postgres impls from "deferred" to "exist today (behind STORE_BACKEND=postgres)". §8 status: persistence is now real behind the switch.
- `architectural.md`: update ADR-005's note and the §6 deferred list (Postgres persistence now built). Add **ADR-014 — Postgres persistence with a hybrid schema** (the decision: Core+Alembic, hybrid columns+JSONB with payload-as-truth, errors propagate not fail-safe, backend switch). Follow the existing ADR format.

- [ ] **Step 4: Commit**

```bash
git add docs/PERSISTENCE.md flow.md architectural.md
git commit -m "docs: Postgres persistence now built (ADR-014); PERSISTENCE.md + status updates"
```

---

## Self-Review Notes (for the executor)

- **`on_conflict_do_update` import** (Task 5): `from sqlalchemy.dialects.postgresql import insert as pg_insert`, then `pg_insert(table).on_conflict_do_update(index_elements=[...], set_={...})`. Confirm against the installed SQLAlchemy 2.x — this is the pg-specific insert, and it's exactly the Postgres feature the testcontainer verifies (SQLite would need different syntax, which is why we chose testcontainers).
- **testcontainers URL scheme** (Task 2/6): `get_connection_url()` returns a `postgresql+psycopg2://` URL by default; we `.replace(...)` it to `postgresql+psycopg://` so it uses psycopg v3 (our driver). If the installed testcontainers already returns psycopg3, the replace is a harmless no-op.
- **Contract test + the marker** (Task 7): the parametrized test must NOT force Docker for the inmem/file params. The clean split is a separate `@pytest.mark.postgres` test that reuses shared `_assert_*_contract(store)` helpers — implementer should refactor to that shape so `pytest -m "not postgres"` runs the inmem/file contract infra-free.
- **Correlation is NOT a store constructor (verified in self-review):** its `RiverCorrelator.retrain(training_data: list[dict])` RECEIVES the training data as a parameter — it does not read a store itself. So the store-constructing services are exactly the FOUR the factory covers (action ×2, governance ×2, feedback ×1, rca ×2). No fifth service, no split-brain gap. Task 8 Step 6's "check correlation" resolves to: correlation needs no change.
- **Existing suite stays green from Task 1**: `STORE_BACKEND` defaults to `file` and every DB test is `@pytest.mark.postgres`, so the 236 existing tests never touch Postgres. The one interface change (`records(correlation_id=None)`) is additive-defaulted.
