# Durable Runtime State (Tier 1b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist governance's pending approvals and correlation's z-score baseline to Postgres so they survive a restart, behind the existing `STORE_BACKEND=file|postgres` switch.

**Architecture:** Two holders, two patterns. Approvals become a synchronous keyed `ApprovalStore` (a near-clone of the Tier-1a `PlaybookStore`). The z-score baseline is snapshotted periodically by correlation's existing flusher thread and reloaded on boot, reconstructing `river` stats from stored scalars via river's `_from_state`. Reliability recovers by re-running `retrain()` from the already-durable training records. Read-model stays on event replay (out of scope).

**Tech Stack:** SQLAlchemy Core + Alembic; psycopg v3; testcontainers; FastAPI; `river` (online stats).

**Spec:** `docs/superpowers/specs/2026-08-20-durable-runtime-state-design.md`

## Global Constraints

- **Backend switch:** everything opt-in behind `settings.store_backend` (`"file"` default). `file`/in-memory is unchanged behavior; the existing suite (all tests) must stay green with the default.
- **Error posture — TWO deliberately different rules:**
  - The **approval store** follows Tier-1a: errors **PROPAGATE** (a lost approval write is a correctness failure). No try/except swallowing.
  - The **baseline snapshot flush** is **best-effort with a logged warning** (`try/except → log.warning → continue`) — a missed snapshot is recoverable and must never crash the flusher thread. This is the K8s-remediator fail-safe posture, and the contrast with the audit sink is intentional and documented.
- **Reconstruct from payload / stored scalars** — never rebuild a model from promoted columns; never store river objects as pickles.
- **Baseline codec (VERIFIED against installed river — do not deviate):** `Var._from_state(n, m, sig, *, ddof=1)` takes `sig = the VARIANCE` (`var.get()`), NOT the raw `_S`. Store `(n, mean, variance)`; `n` from `var.mean.n`. Storing `_S` is a silent behavior bug (diverging z-scores).
- **Concurrency:** `detect()` mutates `_mean`/`_var` on the consumer thread while the flusher snapshots concurrently; the `CorrelationEngine` already holds a `_lock`. Snapshot reads must be taken under that lock.
- **psycopg v3 URL:** testcontainers `get_connection_url()` returns `postgresql+psycopg2://`; `.replace("postgresql+psycopg2://", "postgresql+psycopg://")` in test fixtures (same as Tier 1a).
- **Marker:** DB tests are `@pytest.mark.postgres` so `pytest -m "not postgres"` stays infra-free.
- **Git identity:** commits authored `CodexManvik <manviktalwar.official@gmail.com>`; commit messages end with the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer.

---

## File Structure

- `common/db.py` — add `approvals` + `correlation_baseline` `Table`s to METADATA (Task 2).
- `common/interfaces.py` — `ApprovalStore` Protocol (Task 3).
- `services/governance/adapters/approval_store.py` — NEW: `InMemoryApprovalStore` + `PostgresApprovalStore` (Task 3).
- `services/governance/app.py` — approval endpoints use the store (Task 4).
- `services/correlation/adapters/river_correlator.py` — `snapshot()`/`load()` (Task 5).
- `services/correlation/engine.py` — `CorrelationEngine.snapshot()`/`load()` delegating to the correlator, lock-safe (Task 6).
- `services/correlation/app.py` — reload-on-boot in lifespan; pass a baseline store to the flusher (Task 8).
- `services/correlation/consumer.py` — `run_flusher` gains the periodic snapshot step (Task 7).
- `services/correlation/adapters/baseline_store.py` — NEW: `InMemoryBaselineStore` + `PostgresBaselineStore` (Task 7).
- `common/stores.py` — `make_stores` grows `approval_store` + `baseline_store` (Task 9).
- `common/config.py` — `baseline_snapshot_seconds` (Task 9).
- `alembic/versions/0002_runtime_state.py` — NEW migration (Task 9).
- Tests per task; final verification + docs (Task 10).

---

## Task 1: Spike — pin river's `_from_state` and prove a behavioral round-trip

**Files:**
- Test: `tests/test_baseline_codec.py` (create — this task writes ONLY the codec round-trip test, no adapter yet)

**Interfaces:**
- Produces: a proven, committed test asserting a `river` Mean/Var snapshot→reload preserves the z-score. Later tasks depend on the exact `(n, mean, variance)` scalars this pins.

- [ ] **Step 1: Write the behavioral round-trip test**

```python
# tests/test_baseline_codec.py
"""The z-score baseline must survive a snapshot→reload with identical behavior.

This pins river's _from_state contract: Var._from_state(n, m, sig) takes the
VARIANCE as sig, not the raw _S. Storing _S diverges (verified during design).
"""
from river import stats


def _snap(mean: stats.Mean, var: stats.Var) -> dict:
    return {"n": var.mean.n, "mean": mean.get(), "variance": var.get()}


def _load(row: dict) -> tuple[stats.Mean, stats.Var]:
    n = int(row["n"])
    m = stats.Mean._from_state(n, row["mean"])
    v = stats.Var._from_state(n, row["mean"], row["variance"], ddof=1)
    return m, v


def test_snapshot_reload_preserves_zscore():
    orig_m, orig_v = stats.Mean(), stats.Var()
    for x in [10, 12, 11, 13, 9, 14, 8, 11, 12, 10]:
        orig_m.update(x)
        orig_v.update(x)

    new_m, new_v = _load(_snap(orig_m, orig_v))

    # variance reconstructs exactly
    assert abs(new_v.get() - orig_v.get()) < 1e-9
    # the next value's z-score is identical
    test_val = 25.0
    z_orig = abs(test_val - orig_m.get()) / (orig_v.get() ** 0.5)
    z_new = abs(test_val - new_m.get()) / (new_v.get() ** 0.5)
    assert abs(z_orig - z_new) < 1e-9
    # continuing to update stays identical (state, not just a snapshot)
    orig_v.update(test_val)
    new_v.update(test_val)
    assert abs(orig_v.get() - new_v.get()) < 1e-9
```

- [ ] **Step 2: Run to verify it PASSES**

Run: `uv run pytest tests/test_baseline_codec.py -v`
Expected: PASS. (If `_from_state`'s signature differs on the installed river, STOP and report — the whole baseline approach depends on this. The signature verified during design is `Mean._from_state(n, mean)` and `Var._from_state(n, m, sig, *, ddof=1)` with `sig` = variance.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_baseline_codec.py
git commit -m "test(correlation): pin river _from_state codec round-trip (spike)"
```

---

## Task 2: Schema — add `approvals` + `correlation_baseline` tables to METADATA

**Files:**
- Modify: `common/db.py`
- Test: `tests/test_db_metadata.py` (extend the existing metadata test)

**Interfaces:**
- Consumes: existing `METADATA`, `_JSON` (the `JSONB().with_variant(JSON(), "sqlite")`) from `common/db.py`.
- Produces: `approvals` and `correlation_baseline` `Table` objects importable from `common.db`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_db_metadata.py`:

```python
def test_runtime_state_tables_registered():
    from common.db import METADATA
    names = set(METADATA.tables)
    assert {"approvals", "correlation_baseline"} <= names

    approvals = METADATA.tables["approvals"]
    assert {"id", "situation_id", "playbook_id", "status", "payload", "updated_at"} <= set(
        approvals.columns.keys()
    )
    assert approvals.c.id.primary_key

    baseline = METADATA.tables["correlation_baseline"]
    assert {"metric_name", "n", "mean", "variance", "count", "updated_at"} <= set(
        baseline.columns.keys()
    )
    assert baseline.c.metric_name.primary_key
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_db_metadata.py::test_runtime_state_tables_registered -v`
Expected: FAIL (KeyError / tables not registered).

- [ ] **Step 3: Add the two tables**

In `common/db.py`, after the existing `playbooks` table definition, mirroring the existing style (`Table(..., METADATA, Column(...))`, indexes via `Index(...)`):

```python
approvals = Table(
    "approvals",
    METADATA,
    Column("id", String, primary_key=True),
    Column("situation_id", String, nullable=False),
    Column("playbook_id", String, nullable=False),
    Column("status", String, nullable=False),
    Column("payload", _JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
Index("ix_approvals_status", approvals.c.status)

correlation_baseline = Table(
    "correlation_baseline",
    METADATA,
    Column("metric_name", String, primary_key=True),
    Column("n", Float, nullable=False),
    Column("mean", Float, nullable=False),
    Column("variance", Float, nullable=False),
    Column("count", BigInteger, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
```

Ensure `Float`, `BigInteger`, `Index`, `DateTime`, `String`, `Column`, `Table` are imported at the top of `common/db.py` (check the existing imports — `BigInteger`, `DateTime`, `String`, `Index` are already used by the Tier-1a tables; add `Float` if absent).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_db_metadata.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add common/db.py tests/test_db_metadata.py
git commit -m "feat(db): approvals + correlation_baseline tables in METADATA"
```

---

## Task 3: `ApprovalStore` interface + In-Memory & Postgres adapters

**Files:**
- Modify: `common/interfaces.py`
- Create: `services/governance/adapters/approval_store.py`
- Test: `tests/test_approval_store.py`

**Interfaces:**
- Consumes: `common.db` `approvals` table + `to_payload`/`from_payload`; `common.contracts.ApprovalRequest`.
- Produces: `ApprovalStore` Protocol; `InMemoryApprovalStore()`, `PostgresApprovalStore(engine)` with `create(request) -> ApprovalRequest`, `get(id) -> ApprovalRequest | None`, `decide(id, status, decided_by) -> ApprovalRequest | None`, `list_pending() -> list[ApprovalRequest]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_approval_store.py
import pytest
from common.contracts import ApprovalRequest
from services.governance.adapters.approval_store import InMemoryApprovalStore


def _appr(aid="a1", status="pending"):
    return ApprovalRequest(id=aid, situation_id="s1", playbook_id="restart-pod",
                           requested_by="action-service", status=status)


def test_inmem_create_get():
    s = InMemoryApprovalStore()
    s.create(_appr("a1"))
    got = s.get("a1")
    assert got is not None and got.id == "a1" and got.status == "pending"
    assert s.get("missing") is None


def test_inmem_decide_updates_status_and_decider():
    s = InMemoryApprovalStore()
    s.create(_appr("a1"))
    updated = s.decide("a1", status="approved", decided_by="alice")
    assert updated.status == "approved" and updated.decided_by == "alice"
    assert s.get("a1").status == "approved"
    assert s.decide("missing", "approved", "alice") is None


def test_inmem_list_pending_excludes_decided():
    s = InMemoryApprovalStore()
    s.create(_appr("a1"))
    s.create(_appr("a2"))
    s.decide("a1", status="approved", decided_by="alice")
    pending_ids = {a.id for a in s.list_pending()}
    assert pending_ids == {"a2"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_approval_store.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Add the `ApprovalStore` Protocol**

In `common/interfaces.py`, following the existing Protocol style (check how `AuditSink`/`PlaybookStore` are declared):

```python
class ApprovalStore(Protocol):
    def create(self, request: "ApprovalRequest") -> "ApprovalRequest": ...
    def get(self, approval_id: str) -> "ApprovalRequest | None": ...
    def decide(self, approval_id: str, status: str, decided_by: str) -> "ApprovalRequest | None": ...
    def list_pending(self) -> "list[ApprovalRequest]": ...
```

Ensure `ApprovalRequest` is importable in `interfaces.py` the same way the other contracts are (check the existing imports — add to the `from common.contracts import ...` line if needed).

- [ ] **Step 4: Implement the adapters**

```python
# services/governance/adapters/approval_store.py
"""ApprovalStore implementations: in-memory (tests) and Postgres.

Pending HITL approvals are live runtime state — a plain dict today, lost on
restart. Postgres makes them durable so a human's in-flight decision survives a
governance restart mid-incident. Errors PROPAGATE (a lost approval write is a
correctness failure — same posture as the other stores, contrast the best-effort
baseline snapshot)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from common.contracts import ApprovalRequest
from common.db import approvals, from_payload, to_payload


class InMemoryApprovalStore:
    def __init__(self) -> None:
        self._by_id: dict[str, ApprovalRequest] = {}

    def create(self, request: ApprovalRequest) -> ApprovalRequest:
        self._by_id[request.id] = request
        return request

    def get(self, approval_id: str) -> ApprovalRequest | None:
        return self._by_id.get(approval_id)

    def decide(self, approval_id: str, status: str, decided_by: str) -> ApprovalRequest | None:
        req = self._by_id.get(approval_id)
        if req is None:
            return None
        updated = req.model_copy(update={"status": status, "decided_by": decided_by})
        self._by_id[approval_id] = updated
        return updated

    def list_pending(self) -> list[ApprovalRequest]:
        return [a for a in self._by_id.values() if a.status == "pending"]


class PostgresApprovalStore:
    def __init__(self, engine) -> None:
        self._engine = engine

    @staticmethod
    def _values(request: ApprovalRequest) -> dict:
        return {
            "id": request.id,
            "situation_id": request.situation_id,
            "playbook_id": request.playbook_id,
            "status": request.status,
            "payload": to_payload(request),
            "updated_at": datetime.now(UTC),
        }

    def create(self, request: ApprovalRequest) -> ApprovalRequest:
        stmt = pg_insert(approvals).values(**self._values(request))
        stmt = stmt.on_conflict_do_update(
            index_elements=[approvals.c.id],
            set_={
                "situation_id": stmt.excluded.situation_id,
                "playbook_id": stmt.excluded.playbook_id,
                "status": stmt.excluded.status,
                "payload": stmt.excluded.payload,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)
        return request

    def get(self, approval_id: str) -> ApprovalRequest | None:
        stmt = select(approvals.c.payload).where(approvals.c.id == approval_id)
        with self._engine.connect() as conn:
            row = conn.execute(stmt).first()
        return from_payload(row.payload, ApprovalRequest) if row else None

    def decide(self, approval_id: str, status: str, decided_by: str) -> ApprovalRequest | None:
        current = self.get(approval_id)
        if current is None:
            return None
        updated = current.model_copy(update={"status": status, "decided_by": decided_by})
        self.create(updated)  # upsert
        return updated

    def list_pending(self) -> list[ApprovalRequest]:
        stmt = (
            select(approvals.c.payload)
            .where(approvals.c.status == "pending")
            .order_by(approvals.c.updated_at)
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [from_payload(r.payload, ApprovalRequest) for r in rows]
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_approval_store.py -v`
Expected: PASS (in-memory tests; Postgres tests come in Task 9's contract additions / Task 10).

- [ ] **Step 6: Commit**

```bash
git add common/interfaces.py services/governance/adapters/approval_store.py tests/test_approval_store.py
git commit -m "feat(governance): ApprovalStore interface + in-memory & Postgres adapters"
```

---

## Task 4: Wire governance's approval endpoints to the store

**Files:**
- Modify: `services/governance/app.py`
- Test: `services/governance/tests/` (add an approval-persistence test; check the existing test file names there)

**Interfaces:**
- Consumes: `InMemoryApprovalStore` (until Task 9 routes it through `make_stores`).
- Produces: `app.state.approval_store`; the 4 approval endpoints (`POST /approvals`, `GET /approvals`, `GET /approvals/{id}`, `POST /approvals/{id}/decide`) use it.

- [ ] **Step 1: Write the failing test**

Add a test (in the governance tests dir, following its existing TestClient style) asserting that after `POST /approvals` then a governance restart-equivalent (a fresh `app.state.approval_store` seeded from the same store instance), `GET /approvals` still lists it. Since in-memory doesn't persist across processes, assert instead the behavioral contract: the endpoints delegate to `app.state.approval_store` (create → list_pending shows it; decide → list_pending hides it; decide on missing → 404):

```python
def test_approval_endpoints_use_store(client):
    # client is the governance TestClient fixture (check the existing conftest)
    r = client.post("/approvals", json={
        "id": "appr-1", "situation_id": "s1",
        "playbook_id": "restart-pod", "requested_by": "action-service",
    })
    assert r.status_code == 200
    assert any(a["id"] == "appr-1" for a in client.get("/approvals").json())
    # decide → no longer pending
    d = client.post("/approvals/appr-1/decide", json={"decision": "approved", "decided_by": "alice"})
    assert d.status_code == 200 and d.json()["status"] == "approved"
    assert all(a["id"] != "appr-1" for a in client.get("/approvals").json())
```

(If the governance decide endpoint requires an RBAC actor that passes `check(..., "approve", ...)`, use whatever actor the existing decide tests use — check `services/governance/tests/`.)

- [ ] **Step 2: Run to verify it fails or passes-by-accident**

Run the new test. If it passes against the current dict-based code, that's fine — the point is it must STILL pass after the refactor. If it fails, note why (likely the RBAC actor).

- [ ] **Step 3: Refactor the endpoints to the store**

In `services/governance/app.py`:
- In `_init_state()`, replace `app.state.approvals = {}` with `app.state.approval_store = InMemoryApprovalStore()` (import it). (Task 9 swaps this for `stores.approval_store`.)
- `POST /approvals` (`create_approval`): `app.state.approval_store.create(request)` and return it.
- `GET /approvals` (`list_approvals`): `return app.state.approval_store.list_pending()`.
- `GET /approvals/{id}` (`get_approval`): `req = app.state.approval_store.get(approval_id)`; 404 if None.
- `POST /approvals/{id}/decide` (`decide_approval`): fetch via `get(approval_id)` for the 404 + RBAC check (keep the `rbac.check(decision.decided_by, "approve", f"playbook:{req.playbook_id}")` gate exactly as-is), then `updated = app.state.approval_store.decide(approval_id, status=decision.decision, decided_by=decision.decided_by)` and return it. The RBAC/policy logic stays in the endpoint; the store is dumb persistence.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest services/governance/tests/ tests/test_approval_store.py -v`
Expected: PASS (existing governance tests unaffected; new test green).

- [ ] **Step 5: Commit**

```bash
git add services/governance/app.py services/governance/tests/
git commit -m "feat(governance): approval endpoints use ApprovalStore (in-memory default)"
```

---

## Task 5: `RiverCorrelator.snapshot()` / `load()`

**Files:**
- Modify: `services/correlation/adapters/river_correlator.py`
- Test: `tests/test_baseline_codec.py` (extend with correlator-level round-trip)

**Interfaces:**
- Consumes: the verified `(n, mean, variance)` codec from Task 1.
- Produces: `RiverCorrelator.snapshot() -> list[dict]` (one dict per metric with keys `metric_name`, `n`, `mean`, `variance`, `count`); `RiverCorrelator.load(rows: list[dict]) -> None`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_baseline_codec.py`:

```python
def test_correlator_snapshot_reload_no_warmup_blackout():
    from common.contracts import TelemetryEvent, TelemetryKind
    from datetime import UTC, datetime
    from services.correlation.adapters.river_correlator import RiverCorrelator

    def ev(v):
        # TelemetryEvent requires source + kind (see any correlation test's helper)
        return TelemetryEvent(source="prom", kind=TelemetryKind.METRIC, name="cpu_usage",
                              value=v, labels={}, ts=datetime(2026, 8, 20, tzinfo=UTC),
                              fingerprint="cpu_usage")

    orig = RiverCorrelator(z_threshold=3.0, warmup_samples=50)
    for v in [50.0 + (i % 5) for i in range(60)]:   # settle past warm-up
        orig.detect(ev(v))

    rows = orig.snapshot()
    assert any(r["metric_name"] == "cpu_usage" for r in rows)

    fresh = RiverCorrelator(z_threshold=3.0, warmup_samples=50)
    fresh.load(rows)
    # a genuine outlier fires immediately — NO warm-up blackout after reload
    assert fresh.is_anomaly(ev(500.0))
    # and a normal value does not
    assert not fresh.is_anomaly(ev(51.0))
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_baseline_codec.py::test_correlator_snapshot_reload_no_warmup_blackout -v`
Expected: FAIL — `snapshot`/`load` not defined.

- [ ] **Step 3: Implement snapshot/load**

Add to `RiverCorrelator` (import `from river import stats` is already present):

```python
    def snapshot(self) -> list[dict]:
        """Per-metric baseline as plain scalars (see tests/test_baseline_codec)."""
        out: list[dict] = []
        for name, mean in self._mean.items():
            var = self._var[name]
            out.append({
                "metric_name": name,
                "n": var.mean.n,
                "mean": mean.get(),
                "variance": var.get(),
                "count": self._count.get(name, 0),
            })
        return out

    def load(self, rows: list[dict]) -> None:
        """Rebuild _mean/_var/_count from persisted scalars via river's _from_state."""
        for r in rows:
            n = int(r["n"])
            self._mean[r["metric_name"]] = stats.Mean._from_state(n, r["mean"])
            self._var[r["metric_name"]] = stats.Var._from_state(n, r["mean"], r["variance"], ddof=1)
            self._count[r["metric_name"]] = int(r["count"])
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_baseline_codec.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/correlation/adapters/river_correlator.py tests/test_baseline_codec.py
git commit -m "feat(correlation): RiverCorrelator snapshot/load (lossless z-score state)"
```

---

## Task 6: `CorrelationEngine.snapshot()` / `load()` — lock-safe delegation

**Files:**
- Modify: `services/correlation/engine.py`
- Test: `services/correlation/tests/` (add an engine snapshot/load test; check the existing test file names)

**Interfaces:**
- Consumes: `RiverCorrelator.snapshot()`/`load()` from Task 5.
- Produces: `CorrelationEngine.snapshot() -> list[dict]`, `CorrelationEngine.load(rows) -> None`, both taking `self._lock` (the same lock guarding `add`/`flush`).

- [ ] **Step 1: Write the failing test**

Add (in `services/correlation/tests/`, matching its style):

```python
def test_engine_snapshot_load_roundtrip():
    from common.contracts import TelemetryEvent, TelemetryKind
    from datetime import UTC, datetime
    from services.correlation.adapters.river_correlator import RiverCorrelator
    from services.correlation.engine import CorrelationEngine

    def ev(v):
        return TelemetryEvent(source="prom", kind=TelemetryKind.METRIC, name="cpu_usage",
                              value=v, labels={}, ts=datetime(2026, 8, 20, tzinfo=UTC),
                              fingerprint="cpu_usage")

    e1 = CorrelationEngine(RiverCorrelator(z_threshold=3.0, warmup_samples=50))
    for v in [50.0 + (i % 5) for i in range(60)]:
        e1.add(ev(v))
    rows = e1.snapshot()

    e2 = CorrelationEngine(RiverCorrelator(z_threshold=3.0, warmup_samples=50))
    e2.load(rows)
    # e2's correlator is warmed — a spike is detected (add returns/ buffers it)
    assert e2._correlator.is_anomaly(ev(500.0))
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest services/correlation/tests/ -k snapshot -v`
Expected: FAIL — engine has no snapshot/load.

- [ ] **Step 3: Implement lock-safe delegation**

Add to `CorrelationEngine`:

```python
    def snapshot(self) -> list[dict]:
        with self._lock:
            return self._correlator.snapshot()

    def load(self, rows: list[dict]) -> None:
        with self._lock:
            self._correlator.load(rows)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest services/correlation/tests/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/correlation/engine.py services/correlation/tests/
git commit -m "feat(correlation): CorrelationEngine snapshot/load (lock-safe)"
```

---

## Task 7: `BaselineStore` adapters + flusher snapshot step

**Files:**
- Create: `services/correlation/adapters/baseline_store.py`
- Modify: `services/correlation/consumer.py` (`run_flusher`)
- Test: `tests/test_baseline_store.py`

**Interfaces:**
- Consumes: `common.db` `correlation_baseline` table; `CorrelationEngine.snapshot()`.
- Produces: `InMemoryBaselineStore()`, `PostgresBaselineStore(engine)` with `save(rows: list[dict]) -> None` (upsert per metric) and `load_all() -> list[dict]`. `run_flusher` gains an optional `baseline_store` + `snapshot_period` and periodically snapshots — **best-effort, logged, never raises**.

- [ ] **Step 1: Write the failing test (in-memory store + flusher best-effort behavior)**

```python
# tests/test_baseline_store.py
from services.correlation.adapters.baseline_store import InMemoryBaselineStore


def test_inmem_baseline_save_load_roundtrip():
    s = InMemoryBaselineStore()
    rows = [{"metric_name": "cpu_usage", "n": 60.0, "mean": 52.0, "variance": 4.0, "count": 60}]
    s.save(rows)
    got = {r["metric_name"]: r for r in s.load_all()}
    assert got["cpu_usage"]["variance"] == 4.0 and got["cpu_usage"]["count"] == 60


def test_flusher_snapshot_is_best_effort(monkeypatch):
    """A failing baseline_store.save must NOT crash the flusher — logged & skipped."""
    import threading
    from services.correlation.consumer import _snapshot_baseline_once  # small extracted helper

    class _Boom:
        def save(self, rows):
            raise RuntimeError("db down")

    class _Engine:
        def snapshot(self):
            return [{"metric_name": "x", "n": 1.0, "mean": 1.0, "variance": 0.0, "count": 1}]

    # must return without raising
    _snapshot_baseline_once(_Engine(), _Boom())
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_baseline_store.py -v`
Expected: FAIL — modules/helpers not defined.

- [ ] **Step 3: Implement the baseline store adapters**

```python
# services/correlation/adapters/baseline_store.py
"""BaselineStore: persist the correlator's per-metric z-score baseline.

Unlike the audit/approval stores, a baseline snapshot is best-effort — it is a
slowly-settling statistic, and losing one flush is recoverable. Persistence
errors here are logged and swallowed by the caller (the flusher), never raised
(contrast the propagate-loudly audit sink)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from common.db import correlation_baseline


class InMemoryBaselineStore:
    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}

    def save(self, rows: list[dict]) -> None:
        for r in rows:
            self._rows[r["metric_name"]] = dict(r)

    def load_all(self) -> list[dict]:
        return [dict(r) for r in self._rows.values()]


class PostgresBaselineStore:
    def __init__(self, engine) -> None:
        self._engine = engine

    def save(self, rows: list[dict]) -> None:
        if not rows:
            return
        now = datetime.now(UTC)
        with self._engine.begin() as conn:
            for r in rows:
                stmt = pg_insert(correlation_baseline).values(
                    metric_name=r["metric_name"], n=r["n"], mean=r["mean"],
                    variance=r["variance"], count=r["count"], updated_at=now,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=[correlation_baseline.c.metric_name],
                    set_={
                        "n": stmt.excluded.n, "mean": stmt.excluded.mean,
                        "variance": stmt.excluded.variance, "count": stmt.excluded.count,
                        "updated_at": stmt.excluded.updated_at,
                    },
                )
                conn.execute(stmt)

    def load_all(self) -> list[dict]:
        cols = correlation_baseline.c
        stmt = select(cols.metric_name, cols.n, cols.mean, cols.variance, cols.count)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [
            {"metric_name": r.metric_name, "n": r.n, "mean": r.mean,
             "variance": r.variance, "count": r.count}
            for r in rows
        ]
```

- [ ] **Step 4: Add the flusher snapshot helper + wire it into `run_flusher`**

In `services/correlation/consumer.py`, add the best-effort helper and call it on a period. First read the current `run_flusher` in `services/correlation/app.py` — NOTE the flusher currently lives in `app.py`, not `consumer.py`. Put `_snapshot_baseline_once` in `consumer.py` (importable by the test) and have `run_flusher` call it:

```python
# services/correlation/consumer.py
import logging
logger = logging.getLogger(__name__)

def _snapshot_baseline_once(engine, baseline_store) -> None:
    """Best-effort: snapshot the baseline; log and swallow any error (never raise)."""
    if baseline_store is None:
        return
    try:
        baseline_store.save(engine.snapshot())
    except Exception as exc:  # noqa: BLE001 — best-effort; a missed snapshot is recoverable
        logger.warning("baseline snapshot failed (will retry next period): %s", exc)
```

Then modify `run_flusher` (wherever it lives — currently `services/correlation/app.py:18`) to accept `baseline_store=None` and `snapshot_period` and call `_snapshot_baseline_once(engine, baseline_store)` every `snapshot_period` seconds inside its loop (track the last-snapshot time with `time.monotonic()`; the situation flush keeps its own cadence). Keep the situation-flush behavior unchanged.

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_baseline_store.py services/correlation/tests/ -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/correlation/adapters/baseline_store.py services/correlation/consumer.py services/correlation/app.py tests/test_baseline_store.py
git commit -m "feat(correlation): BaselineStore + best-effort flusher snapshot"
```

---

## Task 8: Reload-on-boot in correlation lifespan

**Files:**
- Modify: `services/correlation/app.py`
- Test: `services/correlation/tests/` (a lifespan/boot test, or assert the reload wiring via a unit test of an extracted `_reload_baseline(engine, baseline_store, training_records)` helper)

**Interfaces:**
- Consumes: `CorrelationEngine.load()`, `BaselineStore.load_all()`, and (for reliability) `RiverCorrelator.retrain()` fed from training records.
- Produces: on boot, when `store_backend == "postgres"`, the engine is loaded from the baseline store AND reliability is recovered from training records, BEFORE the consumer thread starts.

- [ ] **Step 1: Write the failing test**

Extract the reload into a testable helper and test it directly (avoids driving a full lifespan):

```python
def test_reload_baseline_loads_and_retrains():
    from services.correlation.adapters.baseline_store import InMemoryBaselineStore
    from services.correlation.adapters.river_correlator import RiverCorrelator
    from services.correlation.app import _reload_baseline  # extracted helper
    from services.correlation.engine import CorrelationEngine

    store = InMemoryBaselineStore()
    store.save([{"metric_name": "cpu_usage", "n": 60.0, "mean": 52.0, "variance": 4.0, "count": 60}])
    engine = CorrelationEngine(RiverCorrelator(z_threshold=3.0, warmup_samples=50))
    training = [{"signature": "sig-x", "worked": True}, {"signature": "sig-x", "worked": True}]

    _reload_baseline(engine, store, training)

    # baseline loaded → warmed for cpu_usage
    from common.contracts import TelemetryEvent, TelemetryKind
    from datetime import UTC, datetime
    ev = TelemetryEvent(source="prom", kind=TelemetryKind.METRIC, name="cpu_usage",
                        value=500.0, labels={}, ts=datetime(2026, 8, 20, tzinfo=UTC),
                        fingerprint="cpu_usage")
    assert engine._correlator.is_anomaly(ev)
    # reliability recovered from training
    assert engine._correlator.reliability("sig-x") == 1.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest services/correlation/tests/ -k reload -v`
Expected: FAIL — `_reload_baseline` not defined.

- [ ] **Step 3: Implement the reload helper + call it in lifespan**

In `services/correlation/app.py`:

```python
def _reload_baseline(engine, baseline_store, training_records: list[dict]) -> None:
    """On boot: restore the z-score baseline + recover reliability. Best-effort."""
    if baseline_store is not None:
        try:
            engine.load(baseline_store.load_all())
        except Exception as exc:  # noqa: BLE001 — a failed reload just means a cold start
            logger.warning("baseline reload failed, starting cold: %s", exc)
    if training_records:
        engine._correlator.retrain(training_records)
```

In `lifespan`, after building `engine` and BEFORE `thread.start()`: if `settings.store_backend == "postgres"`, build the baseline store + training store (via `make_stores` — see Task 9 for the fields), read the training records (`stores.training_store.read_all()` → list of dicts via `model_dump`), and call `_reload_baseline(engine, stores.baseline_store, training_records)`. Store `stores.baseline_store` on `app.state` and pass it into `run_flusher` so the flusher snapshots. For `store_backend == "file"`, baseline_store is None and reload is a no-op (cold start as today). Add `import logging; logger = logging.getLogger(__name__)` if not present.

(Note: `retrain` expects `list[dict]` with `"signature"`/`"worked"` keys — confirm the training-record dict shape via `TrainingRecord.model_dump()` and map if the key names differ.)

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest services/correlation/tests/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/correlation/app.py services/correlation/tests/
git commit -m "feat(correlation): reload baseline + recover reliability on boot"
```

---

## Task 9: Factory + config + Alembic migration `0002`

**Files:**
- Modify: `common/stores.py`, `common/config.py`
- Create: `alembic/versions/0002_runtime_state.py`
- Test: `tests/test_store_factory.py` (extend), `tests/test_migrations.py` (extend)

**Interfaces:**
- Consumes: all the new adapters.
- Produces: `Stores` grows `approval_store` + `baseline_store`; `make_stores` builds them per backend; `settings.baseline_snapshot_seconds`; migration `0002` creates the two tables.

- [ ] **Step 1: Config**

In `common/config.py`, after `database_url`:
```python
    baseline_snapshot_seconds: float = 30.0
```

- [ ] **Step 2: Extend the factory (write the failing test first)**

Add to `tests/test_store_factory.py`:
```python
def test_file_backend_has_none_runtime_stores():
    s = make_stores(_S())  # _S().store_backend == "file"
    from services.governance.adapters.approval_store import InMemoryApprovalStore
    assert isinstance(s.approval_store, InMemoryApprovalStore)
    assert s.baseline_store is None

def test_postgres_backend_builds_runtime_stores():
    from services.governance.adapters.approval_store import PostgresApprovalStore
    from services.correlation.adapters.baseline_store import PostgresBaselineStore
    class _P(_S):
        store_backend = "postgres"
    s = make_stores(_P())
    assert isinstance(s.approval_store, PostgresApprovalStore)
    assert isinstance(s.baseline_store, PostgresBaselineStore)
```

Then extend `common/stores.py`: add `approval_store: object` and `baseline_store: object | None` to the `Stores` dataclass; in the postgres branch build `PostgresApprovalStore(engine)` + `PostgresBaselineStore(engine)`; in the file branch build `InMemoryApprovalStore()` + `baseline_store=None` (file mode = no durable baseline; correlation cold-starts, which is today's behavior). Import the new adapters at the top of `stores.py`.

- [ ] **Step 3: Migration `0002`**

```python
# alembic/versions/0002_runtime_state.py
"""runtime state tables: approvals + correlation_baseline"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002_runtime_state"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("situation_id", sa.String, nullable=False),
        sa.Column("playbook_id", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_approvals_status", "approvals", ["status"])
    op.create_table(
        "correlation_baseline",
        sa.Column("metric_name", sa.String, primary_key=True),
        sa.Column("n", sa.Float, nullable=False),
        sa.Column("mean", sa.Float, nullable=False),
        sa.Column("variance", sa.Float, nullable=False),
        sa.Column("count", sa.BigInteger, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("correlation_baseline")
    op.drop_table("approvals")
```

- [ ] **Step 4: Extend the migration fidelity test**

In `tests/test_migrations.py`, extend the assertion set so after `alembic upgrade head` the tables `{"approvals", "correlation_baseline"}` and index `{"ix_approvals_status"}` also exist. (Cross-check `0002` against the METADATA tables from Task 2 — they must match column-for-column, same discipline as `0001`.)

- [ ] **Step 5: Run to verify**

Run: `uv run pytest tests/test_store_factory.py tests/test_migrations.py -v -m "not postgres"` (factory) then with `-m postgres` (migration, Docker).
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add common/stores.py common/config.py alembic/versions/0002_runtime_state.py tests/test_store_factory.py tests/test_migrations.py
git commit -m "feat(stores): approval + baseline stores in factory; alembic 0002"
```

---

## Task 10: Postgres adapter tests, contract additions, full verification + docs

**Files:**
- Create: `tests/test_postgres_approvals.py`, `tests/test_baseline_persistence.py`
- Modify: `tests/test_store_contract.py` (add the `approval_store` triple), `docs/PERSISTENCE.md`, `flow.md`, `architectural.md`
- Verification: full suite

- [ ] **Step 1: Postgres approval-store tests** (`@pytest.mark.postgres`, `clean_db`)

`tests/test_postgres_approvals.py`: create/get; decide-upsert (status flips, decided_by set, no duplicate row); list_pending excludes decided; JSONB payload round-trip. Same shape as `tests/test_postgres_playbooks.py`.

- [ ] **Step 2: Baseline persistence + restart-survival test** (`@pytest.mark.postgres`)

`tests/test_baseline_persistence.py`: `PostgresBaselineStore.save`→`load_all` round-trip; and the headline restart-survival test — settle a `CorrelationEngine` baseline, `save` its `snapshot()` to a real container, build a FRESH engine, `load(store.load_all())`, assert a real outlier fires `is_anomaly()` immediately (no warm-up blackout).

- [ ] **Step 3: Extend the cross-adapter contract test**

In `tests/test_store_contract.py`, add an `approval_store` contract: the same create→list_pending→decide→list_pending assertions run against `InMemoryApprovalStore` (unmarked) and `PostgresApprovalStore` (in a `@pytest.mark.postgres` test), via a shared `_assert_approval_contract(store)` helper — same marker-split shape the file already uses for audit/playbook.

- [ ] **Step 4: Full verification**

Run, and record exact counts:
- `uv run pytest -q -m "not postgres"` — all green.
- `uv run pytest -q` (Docker) — all green.
- `uv run ruff check` and `uv run ruff format --check .` — both clean (the repo is now format-clean; keep it that way).
- `docker compose -f deploy/docker-compose.yml config >/dev/null && echo OK`.
If anything is red, STOP and report before writing docs.

- [ ] **Step 5: Docs**

- `docs/PERSISTENCE.md`: add a "Durable runtime state (Tier 1b)" section — the two holders, the two error postures (approvals propagate; baseline best-effort), the snapshot cadence (`baseline_snapshot_seconds`), reload-on-boot, and the read-model-stays-on-replay / reliability-from-training scope notes.
- `flow.md`: note approvals + baseline now durable behind `STORE_BACKEND=postgres`.
- `architectural.md`: add **ADR-015 — Durable runtime state** (matching the existing prose-ADR format: the two-holder/two-pattern decision, the best-effort-vs-propagate posture split, the verified river codec, read-model out of scope). Update the §6 built/deferred lists.

Every doc claim must be TRUE of the committed code (cross-check against config/db/stores/correlation app).

- [ ] **Step 6: Commit**

```bash
git add tests/test_postgres_approvals.py tests/test_baseline_persistence.py tests/test_store_contract.py docs/PERSISTENCE.md flow.md architectural.md
git commit -m "test(db): runtime-state postgres + contract tests; docs (ADR-015)"
```

---

## Self-Review

**1. Spec coverage:** approvals store (T3/T4/T9/T10 ✓); baseline codec (T1/T5 ✓); engine delegation (T6 ✓); flusher snapshot best-effort (T7 ✓); reload-on-boot + reliability recovery (T8 ✓); schema+migration (T2/T9 ✓); config (T9 ✓); factory (T9 ✓); read-model-out / reliability-from-training (documented T8/T10 ✓); testing incl restart-survival (T10 ✓); docs+ADR-015 (T10 ✓). No spec requirement without a task.

**2. Placeholder scan:** every code step has real code; the codec is the verified `(n, mean, variance)` form; no "add error handling"/"similar to" placeholders. The two spots that say "check the existing test style / confirm the dict-key shape" are genuine repo-lookups the implementer must do, not vague hand-waves — each names the exact file to read.

**3. Type consistency:** `ApprovalStore` methods (create/get/decide/list_pending) are identical across the Protocol, both adapters, the factory, and every test. `snapshot()`/`load()` signatures match across RiverCorrelator (T5), CorrelationEngine (T6), and the flusher/reload callers (T7/T8). `BaselineStore.save(rows)`/`load_all()` consistent T7→T8→T9→T10. `Stores.approval_store`/`baseline_store` fields consistent T9→T8/T4. Migration `0002` columns match the METADATA tables from T2 (variance not `_S`). `down_revision="0001_initial"` matches the existing revision id.
