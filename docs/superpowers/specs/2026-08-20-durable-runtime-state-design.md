# Durable Runtime State (Tier 1b) — Design

**Date:** 2026-08-20
**Status:** Approved (pending spec review)
**Owner:** Manvik (integration lead), toward "production-credible".
**Depends on:** Postgres persistence (Tier 1a, ADR-014) — reuses `common/db.py`
metadata, `make_engine`, the `STORE_BACKEND` switch, and the Alembic setup.

## Goal

Make the two pieces of **live runtime state** that have no recovery path today
survive a restart:

1. **Governance's pending approvals** — currently `app.state.approvals = {}`, a
   plain dict. On restart a human's in-flight HITL decision vanishes mid-incident.
2. **Correlation's z-score baseline** — the per-metric `river` Mean/Var/count,
   learned from every telemetry event. On restart every metric re-enters the
   ~50-sample warm-up gate, suppressing anomaly detection exactly when an
   incident may be unfolding.

Behind the existing `STORE_BACKEND=file|postgres` switch (default `file`), so the
existing tests, `docker compose up`, and CI are unaffected.

## Scope (what's in, what's deliberately out)

**In:** approvals persistence; z-score baseline snapshot + reload.

**Out, and why:**
- **The read-model projection** already rebuilds from the event stream on restart
  (`services/read/consumer.py` replays from the beginning with no pre-existing
  consumer group). It has a recovery path; persisting it would be redundant work.
- **The correlator's reliability map** (`_reliability`, recomputed by `retrain()`)
  is a pure function of the labeled training records — which are now durable in
  Postgres (Tier 1a). On boot we re-run `retrain()` from those records to recover
  it, rather than storing it separately.

So the genuinely-unrecovered state is exactly: approvals, and the z-score baseline.

## Why this shape

Two holders, two persistence patterns matched to their nature:

- **Approvals** are low-frequency and correctness-critical — a lost decision is a
  real error. → **Synchronous, exact keyed store**, a near-clone of the Tier-1a
  `PlaybookStore` (keyed by id, upsert on decide). Low risk.
- **The baseline** is updated on *every* telemetry event (thousands/sec) and is a
  slowly-settling statistic — losing a few seconds of it is negligible. →
  **Periodic snapshot** (bounded write rate, bounded loss), piggybacking the
  flusher thread correlation already runs. This is the one genuinely new pattern.

## Schema (two new tables, Alembic-managed as `0002`)

**`approvals`** — durable HITL decisions (mirrors the `playbooks` table pattern):
```
id           TEXT PRIMARY KEY        -- ApprovalRequest.id IS the key
situation_id TEXT        NOT NULL    -- promoted (traceability)
playbook_id  TEXT        NOT NULL
status       TEXT        NOT NULL    -- INDEXED: "pending" | "approved" | "rejected"
payload      JSONB       NOT NULL    -- the full ApprovalRequest, lossless
updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
```
`create()`→INSERT; `decide()`→`INSERT ... ON CONFLICT (id) DO UPDATE`;
`list_pending()`→`SELECT payload WHERE status='pending' ORDER BY updated_at`;
`get(id)`→`SELECT payload WHERE id=`. Reconstruct from payload (source of truth).

**`correlation_baseline`** — the per-metric z-score state, one row per metric:
```
metric_name TEXT PRIMARY KEY
n           DOUBLE PRECISION NOT NULL  -- sample count (Var.mean.n)
mean        DOUBLE PRECISION NOT NULL  -- running mean (Mean.get())
variance    DOUBLE PRECISION NOT NULL  -- the VARIANCE (Var.get()), NOT river's raw _S
count       BIGINT           NOT NULL  -- the warm-up counter (_count[name])
updated_at  TIMESTAMPTZ      NOT NULL DEFAULT now()
```
Plain scalars, no pickles — version-tolerant. A snapshot upserts one row per
metric (`ON CONFLICT (metric_name) DO UPDATE`).

## The baseline codec (VERIFIED against the installed river)

**This was empirically pinned during design — do not deviate without re-verifying.**
River's `Var._from_state(n, m, sig, *, ddof=1)` takes `sig = the VARIANCE`
(`var.get()`), NOT the raw sum-of-squared-deviations `_S`. Its source is:
```python
@classmethod
def _from_state(cls, n, m, sig, *, ddof=1):
    new = cls(ddof=ddof)
    new.mean = stats.Mean._from_state(n, m)
    new._S = (n - ddof) * sig      # it recomputes _S from the variance
    return new
```
A verified round-trip (feed values → snapshot → reconstruct → identical z-score on
the next value, and identical variance after further `update()`s) uses:

```python
# snapshot one metric
def _snap(mean: stats.Mean, var: stats.Var) -> dict:
    return {"n": var.mean.n, "mean": mean.get(), "variance": var.get()}

# reload one metric
def _load(n: float, mean: float, variance: float) -> tuple[stats.Mean, stats.Var]:
    m = stats.Mean._from_state(int(n), mean)
    v = stats.Var._from_state(int(n), mean, variance, ddof=1)
    return m, v
```
`Mean._from_state(n, mean)` is exact. `n` is read from `var.mean.n` (equivalently
`mean.n`). Storing `_S` instead of the variance is the trap — it produces a wrong
variance and diverging z-scores (verified: 7.67 vs 2.56). The Task-1 spike
re-confirms this on the exact installed version before anything is built on it.

## Adapters & mechanisms

**`ApprovalStore`** (new Protocol in `common/interfaces.py`):
```python
class ApprovalStore(Protocol):
    def create(self, request: ApprovalRequest) -> ApprovalRequest: ...
    def get(self, approval_id: str) -> ApprovalRequest | None: ...
    def decide(self, approval_id: str, status: str, decided_by: str) -> ApprovalRequest | None: ...
    def list_pending(self) -> list[ApprovalRequest]: ...
```
`InMemoryApprovalStore` (wraps a dict — today's behavior, extracted) and
`PostgresApprovalStore(engine)` live in `services/governance/adapters/approval_store.py`.
The RBAC check and status transition stay in the `/approvals/{id}/decide`
endpoint; the store is dumb persistence (policy stays in the service). Errors
**propagate** (a lost approval write is a correctness failure — same posture as
the Tier-1a stores).

**Baseline snapshot/reload** on `RiverCorrelator`:
- `snapshot() -> list[dict]` — one dict per metric using `_snap` above.
- `load(rows: list[dict]) -> None` — rebuild `_mean`/`_var`/`_count` using `_load`.

**Flush** — piggyback correlation's existing flusher thread (`run_flusher` in
`services/correlation/app.py`, loop in `services/correlation/consumer.py`). Every
`baseline_snapshot_seconds`, call `engine.snapshot()` and upsert the rows.
**No new thread.** The flush is **best-effort with a logged warning, NOT
propagate-and-crash** — deliberately different from the audit sink's loud posture:
a missed baseline snapshot is recoverable (in-memory baseline keeps working, next
flush retries), and crashing the flusher over a transient DB hiccup would be worse
than skipping one snapshot. `try/except → log.warning → continue`. This mirrors the
K8s remediator's fail-safe posture; the contrast with the audit sink is documented.

**Reload** — in correlation's `lifespan`, after building the engine but **before**
starting the consumer thread (so the first event scores against the loaded
baseline, never an empty one):
1. If `store_backend == "postgres"`: `SELECT * FROM correlation_baseline` →
   `engine.load(rows)`; then re-run `retrain()` from the Postgres training records
   (recovers reliability). A fresh DB with no snapshot starts cold as today — the
   warm-up gate handles it.
2. Then start consumer + flusher.

## Config (`common/config.py`)

Reuses `store_backend` / `database_url` (no new switch). One new tunable:
```python
baseline_snapshot_seconds: float = 30.0
```
With `store_backend=file` (default), approvals stay in-memory and the snapshot is a
no-op — every existing test and plain compose run is unaffected.

## Testing

**Fast path (no Docker):**
- `InMemoryApprovalStore` contract (create/get/decide/list_pending).
- **Baseline codec behavioral round-trip** (highest value): build a correlator,
  feed values, `snapshot()`, build a FRESH correlator, `load()`, assert the next
  `detect()` z-score is identical AND a genuine outlier still fires `is_anomaly()`.
  This proves losslessness *behaviorally*, not field-by-field.
- The cross-adapter contract test gains the `approval_store` triple (marker-split).

**Postgres-marked (testcontainers):**
- `PostgresApprovalStore`: create/get/decide-upsert/list_pending, JSONB round-trip.
- `correlation_baseline` upsert + reload.
- **Restart-survival test (headline):** snapshot a settled baseline to a real
  container, construct a fresh correlator, reload from the container, assert NO
  warm-up blackout — a real outlier fires immediately instead of being suppressed.
  Directly proves the gap is closed.
- Migration-fidelity test extends to the two new tables.

## Concrete change list

**New:** `services/governance/adapters/approval_store.py`;
`alembic/versions/0002_runtime_state.py`; tests `test_approval_store.py`,
`test_baseline_codec.py`, `test_postgres_approvals.py`,
`test_baseline_persistence.py`.

**Modified:** `common/db.py` (2 tables); `common/interfaces.py` (`ApprovalStore`);
`common/stores.py` (`make_stores` grows `approval_store`); `common/config.py`
(`baseline_snapshot_seconds`); `services/governance/app.py` (approval endpoints use
the store); `services/correlation/adapters/river_correlator.py`
(`snapshot()`/`load()`); `services/correlation/app.py` (reload-on-boot in lifespan);
`services/correlation/consumer.py` (flusher does the snapshot); `docs/PERSISTENCE.md`
(update); `flow.md`/`architectural.md` (status; **ADR-015 — durable runtime state**).

## Scope discipline (YAGNI)

Read-model stays on event replay. Reliability recovered from training records, not
stored. Baseline snapshot is best-effort/logged, not propagate-loudly (documented
contrast). No new `STORE_BACKEND`, no new thread, no baseline versioning /
retention / compaction machinery (one row per metric name — trivially small). If a
future river upgrade changes `_from_state`, the fallback is reconstructing by
replaying `update()` from `(n, mean, variance)` — noted, not built.
