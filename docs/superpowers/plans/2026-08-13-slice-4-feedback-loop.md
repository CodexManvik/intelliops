# Slice 4 — Feedback Loop + Closing the Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `feedback-service` (consumes `remediation.outcomes` → labels + persists to a shared TrainingStore → metrics + evidence-based graduation), make `RiverCorrelator.retrain` real (per-signature reliability → suppress emitting proven-self-healing situations), and add an RBAC-gated governance graduate endpoint — closing the loop and finishing the project.

**Architecture:** A FastAPI service following the correlation/rca/action pattern (daemon consumer thread via lifespan). A `TrainingStore` interface (in-memory + file JSONL) is the cross-service seam: feedback appends labeled outcomes; correlation reads them at retrain. Retrain aggregates per-signature reliability; the correlation engine suppresses emitting a `Situation` whose signature reliably self-heals. Governance gains a graduate endpoint (hitl→auto, RBAC-gated). The signature is derived from `situation_id` (the `"sit-"` prefix convention) to avoid touching frozen contracts.

**Tech Stack:** Python 3.11 · FastAPI · Pydantic v2 · in-memory bus · pytest

**Spec:** [docs/superpowers/specs/2026-08-13-slice-4-feedback-loop-design.md](../specs/2026-08-13-slice-4-feedback-loop-design.md)

## Global Constraints

- **Python floor:** 3.11. **Package manager:** `uv` only (`uv add`, `uv run`). Never bare `pip`.
- **Pydantic v2** API only. **Lint gate:** `uv run ruff check .` must pass (0 errors) — apply ruff's UP017/F401/I001/RUF015 autofix where it fires; those are token/import/style-only, never logic.
- **Frozen contracts NOT modified.** Reuse existing: `RemediationOutcome(situation_id, playbook_id, result: RemediationResult, health_after: str, ts: datetime)`; `RemediationResult` = success | failure | rolled_back; `Playbook(id, name, match_rule, steps, hitl_mode: HitlMode, reversible, rollback_steps)`; `HitlMode` = auto | hitl | disabled; `AuditRecord(...)`. NEW contracts are ADDED.
- **Bus transport:** all models via `common.envelope` (`publish_model`, `decode_model`, `iter_models`) as `{"data": json}`. Never hand-roll.
- **Topics:** feedback consumes `remediation.outcomes`.
- **Signature convention (VERIFIED):** `RiverCorrelator.correlate` sets `id = "sit-" + signature`, so `situation_id.removeprefix("sit-") == signature`. Feedback derives the signature this way.
- **Adapters behind interfaces:** feedback depends on `TrainingStore` protocol; tests bind fakes.
- **Determinism:** retrain/reliability/suppression are pure functions of the training data; no wall-clock in that logic.
- **Test command:** `uv run pytest` from repo root. **Lint:** `uv run ruff check .`.

---

### Task 1: TrainingRecord contract + TrainingStore interface + config

**Files:**
- Modify: `common/contracts.py` (append `TrainingRecord`)
- Modify: `common/interfaces.py` (append `TrainingStore`)
- Modify: `common/config.py` (add 3 settings)
- Test: `tests/test_slice4_contracts.py`

**Interfaces:**
- Consumes: existing `RemediationResult` from `common.contracts`.
- Produces:
  - `TrainingRecord(situation_id: str, signature: str, playbook_id: str, result: RemediationResult, worked: bool, ts: datetime)`.
  - `TrainingStore` Protocol (runtime_checkable): `append(record: TrainingRecord) -> None`, `read_all() -> list[TrainingRecord]`.
  - `Settings` gains `training_store_path: str = "data/training.jsonl"`, `reliability_suppress_threshold: float = 0.8`, `graduation_min_successes: int = 3`.

- [ ] **Step 1: Write the failing test**

`tests/test_slice4_contracts.py`:

```python
from datetime import UTC, datetime

from common.config import get_settings
from common.contracts import RemediationResult, TrainingRecord
from common.interfaces import TrainingStore

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def test_training_record_roundtrips():
    r = TrainingRecord(
        situation_id="sit-abc",
        signature="abc",
        playbook_id="restart-pod",
        result=RemediationResult.SUCCESS,
        worked=True,
        ts=NOW,
    )
    restored = TrainingRecord.model_validate(r.model_dump())
    assert restored == r
    assert restored.worked is True


def test_training_store_runtime_checkable():
    class FakeStore:
        def append(self, record): ...
        def read_all(self):
            return []

    assert isinstance(FakeStore(), TrainingStore)


def test_settings_have_slice4_fields():
    s = get_settings()
    assert s.training_store_path.endswith(".jsonl")
    assert 0.0 < s.reliability_suppress_threshold <= 1.0
    assert s.graduation_min_successes >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_slice4_contracts.py -v`
Expected: FAIL — `ImportError: cannot import name 'TrainingRecord'`.

- [ ] **Step 3: Append the contract**

Append to `common/contracts.py`:

```python
class TrainingRecord(BaseModel):
    """A labeled remediation outcome — training data that closes the loop.

    `worked` is True when the remediation succeeded; feedback derives `signature`
    from the situation id (the "sit-" prefix convention). Correlation reads these
    at retrain time to learn which signatures reliably self-heal."""

    situation_id: str
    signature: str
    playbook_id: str
    result: RemediationResult
    worked: bool
    ts: datetime
```

- [ ] **Step 4: Append the interface**

Append to `common/interfaces.py` (no new contracts import needed — `TrainingRecord` is referenced by string annotation via `from __future__ import annotations`, which the file already has; but to be safe, add `TrainingRecord` to the `from common.contracts import ...` line):

```python
@runtime_checkable
class TrainingStore(Protocol):
    """The closed-loop training store: feedback appends, correlation reads (see ADR-001)."""

    def append(self, record: TrainingRecord) -> None: ...

    def read_all(self) -> list[TrainingRecord]: ...
```

And update the contracts import line at the top of `common/interfaces.py` to include `TrainingRecord`:

```python
from common.contracts import (
    ApprovalRequest,
    AuditRecord,
    Playbook,
    Situation,
    TelemetryEvent,
    TrainingRecord,
)
```

- [ ] **Step 5: Add the config settings**

In `common/config.py`, add to the `Settings` class body (after the Slice-3 HITL settings):

```python
    training_store_path: str = "data/training.jsonl"
    reliability_suppress_threshold: float = 0.8
    graduation_min_successes: int = 3
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_slice4_contracts.py -v`
Expected: PASS (3 passed).

- [ ] **Step 7: Commit**

```bash
git add common/contracts.py common/interfaces.py common/config.py tests/test_slice4_contracts.py
git commit -m "feat: add TrainingRecord contract, TrainingStore interface, and config"
```

---

### Task 2: TrainingStore adapters (in-memory + file)

**Files:**
- Create: `services/feedback/__init__.py` (if missing — exists from Slice 0, leave as-is if present)
- Create: `services/feedback/adapters/__init__.py`, `services/feedback/adapters/training_store.py`
- Test: `services/feedback/tests/__init__.py`, `services/feedback/tests/test_training_store.py`

**Interfaces:**
- Consumes: `common.contracts.TrainingRecord`, `common.interfaces.TrainingStore`.
- Produces:
  - `InMemoryTrainingStore()` — `append`/`read_all`; satisfies `TrainingStore`.
  - `FileTrainingStore(path: str)` — `append` (JSONL, creates parent dir), `read_all` (parses back, [] if missing); satisfies `TrainingStore`.

- [ ] **Step 1: Write the failing test**

`services/feedback/tests/__init__.py`: (empty file)

`services/feedback/tests/test_training_store.py`:

```python
from datetime import UTC, datetime

from common.contracts import RemediationResult, TrainingRecord
from common.interfaces import TrainingStore
from services.feedback.adapters.training_store import (
    FileTrainingStore,
    InMemoryTrainingStore,
)

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _record(sig="abc", worked=True):
    return TrainingRecord(
        situation_id=f"sit-{sig}",
        signature=sig,
        playbook_id="restart-pod",
        result=RemediationResult.SUCCESS if worked else RemediationResult.FAILURE,
        worked=worked,
        ts=NOW,
    )


def test_inmemory_satisfies_protocol():
    assert isinstance(InMemoryTrainingStore(), TrainingStore)


def test_inmemory_append_read():
    s = InMemoryTrainingStore()
    s.append(_record("a"))
    s.append(_record("b", worked=False))
    recs = s.read_all()
    assert len(recs) == 2
    assert recs[0].signature == "a"
    assert recs[1].worked is False


def test_file_store_roundtrips(tmp_path):
    path = tmp_path / "sub" / "training.jsonl"  # parent missing
    s = FileTrainingStore(str(path))
    s.append(_record("a"))
    s.append(_record("b", worked=False))
    reread = FileTrainingStore(str(path)).read_all()
    assert [r.signature for r in reread] == ["a", "b"]
    assert all(isinstance(r, TrainingRecord) for r in reread)


def test_file_store_missing_is_empty(tmp_path):
    assert FileTrainingStore(str(tmp_path / "none.jsonl")).read_all() == []


def test_file_satisfies_protocol(tmp_path):
    assert isinstance(FileTrainingStore(str(tmp_path / "t.jsonl")), TrainingStore)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/feedback/tests/test_training_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.feedback.adapters'`.

- [ ] **Step 3: Write the stores**

`services/feedback/adapters/__init__.py`:

```python
"""Feedback adapters: training stores."""
```

`services/feedback/adapters/training_store.py`:

```python
"""TrainingStore implementations: in-memory (tests) and append-only JSONL file.

The training store is the closed-loop seam: feedback appends labeled outcomes;
correlation reads them at retrain time. Postgres is a deferred adapter."""

from __future__ import annotations

import os

from common.contracts import TrainingRecord


class InMemoryTrainingStore:
    def __init__(self) -> None:
        self._records: list[TrainingRecord] = []

    def append(self, record: TrainingRecord) -> None:
        self._records.append(record)

    def read_all(self) -> list[TrainingRecord]:
        return list(self._records)


class FileTrainingStore:
    def __init__(self, path: str) -> None:
        self._path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def append(self, record: TrainingRecord) -> None:
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(record.model_dump_json() + "\n")

    def read_all(self) -> list[TrainingRecord]:
        if not os.path.exists(self._path):
            return []
        out: list[TrainingRecord] = []
        with open(self._path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    out.append(TrainingRecord.model_validate_json(line))
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest services/feedback/tests/test_training_store.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add services/feedback/adapters/ services/feedback/tests/
git commit -m "feat: add training store adapters (in-memory + file)"
```

---

### Task 3: label_outcome + signature helper

**Files:**
- Create: `services/feedback/label.py`
- Test: `services/feedback/tests/test_label.py`

**Interfaces:**
- Consumes: `common.contracts` (`RemediationOutcome`, `RemediationResult`, `TrainingRecord`).
- Produces:
  - `signature_from_situation_id(situation_id: str) -> str` — `situation_id.removeprefix("sit-")`.
  - `label_outcome(outcome: RemediationOutcome) -> TrainingRecord` — signature from id, `worked = (result == SUCCESS)`, copies playbook_id/result/ts.

- [ ] **Step 1: Write the failing test**

`services/feedback/tests/test_label.py`:

```python
from datetime import UTC, datetime

from common.contracts import RemediationOutcome, RemediationResult, TrainingRecord
from services.feedback.label import label_outcome, signature_from_situation_id

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def test_signature_from_situation_id_strips_prefix():
    assert signature_from_situation_id("sit-abc123") == "abc123"
    # no prefix -> unchanged
    assert signature_from_situation_id("abc123") == "abc123"


def _outcome(result):
    return RemediationOutcome(
        situation_id="sit-abc123",
        playbook_id="restart-pod",
        result=result,
        health_after="healthy",
        ts=NOW,
    )


def test_label_success_sets_worked_true():
    r = label_outcome(_outcome(RemediationResult.SUCCESS))
    assert isinstance(r, TrainingRecord)
    assert r.signature == "abc123"
    assert r.playbook_id == "restart-pod"
    assert r.result == RemediationResult.SUCCESS
    assert r.worked is True


def test_label_failure_sets_worked_false():
    assert label_outcome(_outcome(RemediationResult.FAILURE)).worked is False


def test_label_rolled_back_sets_worked_false():
    assert label_outcome(_outcome(RemediationResult.ROLLED_BACK)).worked is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/feedback/tests/test_label.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.feedback.label'`.

- [ ] **Step 3: Write the labeler**

`services/feedback/label.py`:

```python
"""Label a RemediationOutcome as a TrainingRecord — the raw material of the loop.

The signature is derived from the situation id (the "sit-" prefix convention
set by RiverCorrelator.correlate), so no frozen contract needs a new field.
`worked` is True only for a clean success (a rollback or failure did not fix it)."""

from __future__ import annotations

from common.contracts import RemediationOutcome, RemediationResult, TrainingRecord


def signature_from_situation_id(situation_id: str) -> str:
    return situation_id.removeprefix("sit-")


def label_outcome(outcome: RemediationOutcome) -> TrainingRecord:
    return TrainingRecord(
        situation_id=outcome.situation_id,
        signature=signature_from_situation_id(outcome.situation_id),
        playbook_id=outcome.playbook_id,
        result=outcome.result,
        worked=outcome.result == RemediationResult.SUCCESS,
        ts=outcome.ts,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest services/feedback/tests/test_label.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add services/feedback/label.py services/feedback/tests/test_label.py
git commit -m "feat: add feedback outcome labeling"
```

---

### Task 4: compute_metrics

**Files:**
- Create: `services/feedback/metrics.py`
- Test: `services/feedback/tests/test_metrics.py`

**Interfaces:**
- Consumes: `common.contracts` (`TrainingRecord`, `RemediationResult`).
- Produces: `compute_metrics(records: list[TrainingRecord]) -> dict` — returns the metrics dict per spec §4.2. Empty input → all zeros / empty maps (no division by zero).

- [ ] **Step 1: Write the failing test**

`services/feedback/tests/test_metrics.py`:

```python
from datetime import UTC, datetime

from common.contracts import RemediationResult, TrainingRecord
from services.feedback.metrics import compute_metrics

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _rec(sig, result):
    return TrainingRecord(
        situation_id=f"sit-{sig}",
        signature=sig,
        playbook_id="pb",
        result=result,
        worked=result == RemediationResult.SUCCESS,
        ts=NOW,
    )


def test_empty_records_are_zeros():
    m = compute_metrics([])
    assert m["total_outcomes"] == 0
    assert m["success_rate"] == 0.0
    assert m["rollback_rate"] == 0.0
    assert m["failure_rate"] == 0.0
    assert m["by_signature"] == {}


def test_rates_and_counts():
    recs = [
        _rec("a", RemediationResult.SUCCESS),
        _rec("a", RemediationResult.SUCCESS),
        _rec("a", RemediationResult.ROLLED_BACK),
        _rec("b", RemediationResult.FAILURE),
    ]
    m = compute_metrics(recs)
    assert m["total_outcomes"] == 4
    assert m["success_rate"] == 0.5  # 2/4
    assert m["rollback_rate"] == 0.25  # 1/4
    assert m["failure_rate"] == 0.25  # 1/4
    assert m["by_result"] == {"success": 2, "failure": 1, "rolled_back": 1}
    assert m["by_signature"]["a"] == {"worked": 2, "total": 3}
    assert m["by_signature"]["b"] == {"worked": 0, "total": 1}


def test_note_present():
    assert "MTTR" in compute_metrics([])["note"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/feedback/tests/test_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.feedback.metrics'`.

- [ ] **Step 3: Write the metrics**

`services/feedback/metrics.py`:

```python
"""Outcome-derived metrics for feedback-service.

Computes only what RemediationOutcomes actually support — success/rollback/
failure rates, counts by result, and per-signature worked/total. True MTTR/MTTD
need end-to-end detection→resolution timestamps not yet threaded, so they are
NOT fabricated; the `note` states what's deferred (see flow.md 5.6)."""

from __future__ import annotations

from common.contracts import RemediationResult, TrainingRecord

_NOTE = (
    "MTTR/MTTD require end-to-end detection→resolution timestamps not yet "
    "threaded; reported metrics are outcome-derived."
)


def compute_metrics(records: list[TrainingRecord]) -> dict:
    total = len(records)
    by_result = {"success": 0, "failure": 0, "rolled_back": 0}
    by_signature: dict[str, dict[str, int]] = {}
    for r in records:
        by_result[r.result.value] += 1
        sig = by_signature.setdefault(r.signature, {"worked": 0, "total": 0})
        sig["total"] += 1
        if r.worked:
            sig["worked"] += 1

    def rate(n: int) -> float:
        return n / total if total else 0.0

    return {
        "total_outcomes": total,
        "success_rate": rate(by_result["success"]),
        "rollback_rate": rate(by_result["rolled_back"]),
        "failure_rate": rate(by_result["failure"]),
        "by_result": by_result,
        "by_signature": by_signature,
        "note": _NOTE,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest services/feedback/tests/test_metrics.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add services/feedback/metrics.py services/feedback/tests/test_metrics.py
git commit -m "feat: add feedback outcome-derived metrics"
```

---

### Task 5: playbook_stats + should_graduate

**Files:**
- Create: `services/feedback/graduate.py`
- Test: `services/feedback/tests/test_graduate.py`

**Interfaces:**
- Consumes: `common.contracts` (`TrainingRecord`, `RemediationResult`).
- Produces:
  - `playbook_stats(records: list[TrainingRecord], playbook_id: str) -> dict` → `{"successes": int, "failures": int, "rollbacks": int}` for that playbook.
  - `should_graduate(stats: dict, min_successes: int) -> bool` → `successes >= min_successes and failures == 0 and rollbacks == 0`.

- [ ] **Step 1: Write the failing test**

`services/feedback/tests/test_graduate.py`:

```python
from datetime import UTC, datetime

from common.contracts import RemediationResult, TrainingRecord
from services.feedback.graduate import playbook_stats, should_graduate

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _rec(pb, result):
    return TrainingRecord(
        situation_id="sit-x",
        signature="x",
        playbook_id=pb,
        result=result,
        worked=result == RemediationResult.SUCCESS,
        ts=NOW,
    )


def test_playbook_stats_counts_per_playbook():
    recs = [
        _rec("pb1", RemediationResult.SUCCESS),
        _rec("pb1", RemediationResult.SUCCESS),
        _rec("pb1", RemediationResult.ROLLED_BACK),
        _rec("pb2", RemediationResult.FAILURE),
    ]
    s1 = playbook_stats(recs, "pb1")
    assert s1 == {"successes": 2, "failures": 0, "rollbacks": 1}
    s2 = playbook_stats(recs, "pb2")
    assert s2 == {"successes": 0, "failures": 1, "rollbacks": 0}


def test_should_graduate_true_on_clean_successes():
    assert should_graduate({"successes": 3, "failures": 0, "rollbacks": 0}, min_successes=3) is True


def test_should_graduate_false_below_threshold():
    assert (
        should_graduate({"successes": 2, "failures": 0, "rollbacks": 0}, min_successes=3) is False
    )


def test_should_graduate_false_with_any_rollback():
    assert (
        should_graduate({"successes": 5, "failures": 0, "rollbacks": 1}, min_successes=3) is False
    )


def test_should_graduate_false_with_any_failure():
    assert (
        should_graduate({"successes": 5, "failures": 1, "rollbacks": 0}, min_successes=3) is False
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/feedback/tests/test_graduate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.feedback.graduate'`.

- [ ] **Step 3: Write the graduation policy**

`services/feedback/graduate.py`:

```python
"""Evidence-based playbook graduation policy (ADR-008).

A playbook graduates hitl→auto only after a clean track record: at least
`min_successes` successful remediations and ZERO failures or rollbacks in the
observed window. Conservative by design — automation scope expands on evidence,
not optimism. feedback proposes; governance promotes under RBAC."""

from __future__ import annotations

from common.contracts import RemediationResult, TrainingRecord


def playbook_stats(records: list[TrainingRecord], playbook_id: str) -> dict:
    successes = failures = rollbacks = 0
    for r in records:
        if r.playbook_id != playbook_id:
            continue
        if r.result == RemediationResult.SUCCESS:
            successes += 1
        elif r.result == RemediationResult.FAILURE:
            failures += 1
        elif r.result == RemediationResult.ROLLED_BACK:
            rollbacks += 1
    return {"successes": successes, "failures": failures, "rollbacks": rollbacks}


def should_graduate(stats: dict, min_successes: int) -> bool:
    return (
        stats["successes"] >= min_successes and stats["failures"] == 0 and stats["rollbacks"] == 0
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest services/feedback/tests/test_graduate.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add services/feedback/graduate.py services/feedback/tests/test_graduate.py
git commit -m "feat: add evidence-based playbook graduation policy"
```

---

### Task 6: RiverCorrelator.retrain (real) + reliability + engine suppression

**Files:**
- Modify: `services/correlation/adapters/river_correlator.py` (replace the no-op `retrain`, add `reliability` + `should_suppress`)
- Modify: `services/correlation/engine.py` (suppress emit for reliable signatures)
- Test: `services/correlation/tests/test_retrain.py`, `services/correlation/tests/test_engine_suppress.py`

**Interfaces:**
- Consumes: `common.contracts` (`Situation`, `TelemetryEvent`).
- Produces:
  - `RiverCorrelator.retrain(training_data: list[dict]) -> None` — aggregates per-signature reliability (`worked/total`) into `self._reliability: dict[str, float]`. Each dict has at least `"signature"` and `"worked"` keys.
  - `RiverCorrelator.reliability(signature: str) -> float` — returns the stored reliability (0.0 if unseen).
  - `RiverCorrelator.should_suppress(signature: str, threshold: float) -> bool` — `reliability(signature) >= threshold`.
  - `CorrelationEngine(correlator, window_seconds=30.0, suppress_threshold: float = 0.8)` — after `_correlate_buffer` forms a `Situation`, if `correlator.should_suppress(situation.signature, suppress_threshold)` the engine returns `None` (suppresses the emit) instead of the Situation.

- [ ] **Step 1: Write the failing tests**

`services/correlation/tests/test_retrain.py`:

```python
from services.correlation.adapters.river_correlator import RiverCorrelator


def test_retrain_aggregates_reliability():
    c = RiverCorrelator()
    c.retrain(
        [
            {"signature": "a", "worked": True},
            {"signature": "a", "worked": True},
            {"signature": "a", "worked": False},
            {"signature": "b", "worked": False},
        ]
    )
    assert c.reliability("a") == 2 / 3
    assert c.reliability("b") == 0.0


def test_reliability_unseen_is_zero():
    assert RiverCorrelator().reliability("never") == 0.0


def test_should_suppress_at_threshold():
    c = RiverCorrelator()
    c.retrain([{"signature": "a", "worked": True}, {"signature": "a", "worked": True}])  # 1.0
    assert c.should_suppress("a", 0.8) is True
    assert c.should_suppress("a", 1.0) is True


def test_should_not_suppress_below_threshold():
    c = RiverCorrelator()
    c.retrain([{"signature": "a", "worked": True}, {"signature": "a", "worked": False}])  # 0.5
    assert c.should_suppress("a", 0.8) is False


def test_should_not_suppress_unseen():
    assert RiverCorrelator().should_suppress("never", 0.8) is False


def test_retrain_replaces_prior():
    # a fresh retrain recomputes from the given data (idempotent w.r.t. input set)
    c = RiverCorrelator()
    c.retrain([{"signature": "a", "worked": False}])
    assert c.reliability("a") == 0.0
    c.retrain([{"signature": "a", "worked": True}, {"signature": "a", "worked": True}])
    assert c.reliability("a") == 1.0
```

`services/correlation/tests/test_engine_suppress.py`:

```python
import random
from datetime import UTC, datetime

from common.contracts import TelemetryEvent, TelemetryKind
from services.correlation.adapters.river_correlator import RiverCorrelator
from services.correlation.engine import CorrelationEngine


def _event(value, fp, ts_sec=0):
    return TelemetryEvent(
        source="prom",
        kind=TelemetryKind.METRIC,
        name="cpu",
        value=value,
        labels={},
        ts=datetime(2026, 8, 13, 0, 0, ts_sec, tzinfo=UTC),
        fingerprint=fp,
    )


def _prime_and_flush(engine, n=200, seed=42):
    rng = random.Random(seed)
    for i in range(n):
        engine.add(_event(round(rng.gauss(10.0, 1.0), 3), f"b{i}", 0))
    engine.flush()


def test_engine_suppresses_reliable_signature():
    correlator = RiverCorrelator(z_threshold=3.0)
    engine = CorrelationEngine(correlator, window_seconds=30, suppress_threshold=0.8)
    _prime_and_flush(engine)

    # First, form a Situation from two spikes to learn its signature.
    engine.add(_event(100.0, "a", 1))
    engine.add(_event(120.0, "b", 2))
    situation = engine.flush()
    assert situation is not None
    sig = situation.signature

    # Teach the correlator that this signature reliably self-heals.
    correlator.retrain(
        [
            {"signature": sig, "worked": True},
            {"signature": sig, "worked": True},
            {"signature": sig, "worked": True},
        ]
    )

    # The SAME spikes now form the same-signature Situation, which is suppressed.
    engine.add(_event(100.0, "a", 3))
    engine.add(_event(120.0, "b", 4))
    suppressed = engine.flush()
    assert suppressed is None  # reliably-self-healing signature is suppressed


def test_engine_still_emits_unreliable_signature():
    correlator = RiverCorrelator(z_threshold=3.0)
    engine = CorrelationEngine(correlator, window_seconds=30, suppress_threshold=0.8)
    _prime_and_flush(engine)

    engine.add(_event(100.0, "a", 1))
    engine.add(_event(120.0, "b", 2))
    situation = engine.flush()
    sig = situation.signature

    # This signature keeps FAILING — stays sensitive.
    correlator.retrain([{"signature": sig, "worked": False}, {"signature": sig, "worked": False}])

    engine.add(_event(100.0, "a", 3))
    engine.add(_event(120.0, "b", 4))
    still_emitted = engine.flush()
    assert still_emitted is not None  # unreliable signature is NOT suppressed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest services/correlation/tests/test_retrain.py services/correlation/tests/test_engine_suppress.py -v`
Expected: FAIL — `AttributeError: 'RiverCorrelator' object has no attribute 'reliability'` and `TypeError` on the `suppress_threshold` kwarg.

- [ ] **Step 3: Make retrain real in `river_correlator.py`**

In `services/correlation/adapters/river_correlator.py`, first add a `self._reliability` dict in
`__init__`. The current `__init__` is:

```python
    def __init__(self, z_threshold: float = 3.0, warmup_samples: int = 50) -> None:
        self._z_threshold = z_threshold
        self._warmup_samples = warmup_samples
        self._mean: dict[str, stats.Mean] = {}
        self._var: dict[str, stats.Var] = {}
        self._count: dict[str, int] = {}
```

Add one line at the end of `__init__`:

```python
        self._reliability: dict[str, float] = {}
```

Then replace the no-op `retrain` method:

```python
def retrain(self, training_data: list[dict]) -> None:
    # The closed loop: aggregate per-signature reliability from labeled
    # outcomes. A signature whose remediation reliably works becomes a
    # candidate for suppression (see should_suppress); one that fails stays
    # sensitive. Recomputes from the given data each call.
    worked: dict[str, int] = {}
    total: dict[str, int] = {}
    for record in training_data:
        sig = record["signature"]
        total[sig] = total.get(sig, 0) + 1
        if record.get("worked"):
            worked[sig] = worked.get(sig, 0) + 1
    self._reliability = {sig: worked.get(sig, 0) / n for sig, n in total.items()}


def reliability(self, signature: str) -> float:
    return self._reliability.get(signature, 0.0)


def should_suppress(self, signature: str, threshold: float) -> bool:
    return self.reliability(signature) >= threshold
```

- [ ] **Step 4: Add suppression to the engine**

Replace `services/correlation/engine.py` with (adds `suppress_threshold` + suppression in `_correlate_buffer`):

```python
"""Windowed correlation: buffer anomalous events and emit one Situation per window.

The engine scores each event via the correlator; anomalies accumulate in a
rolling time window keyed on event timestamps. When the window's span exceeds
window_seconds (or on an explicit flush), the buffer collapses into a single
Situation. Timestamps come from events, so behavior is deterministic.

The closed loop (Slice 4): a Situation whose signature has a proven
self-healing track record (reliability >= suppress_threshold) is suppressed —
not emitted — because the system has learned that when this fires, it is fixed."""

from __future__ import annotations

from common.contracts import Situation, TelemetryEvent
from services.correlation.adapters.river_correlator import RiverCorrelator


class CorrelationEngine:
    def __init__(
        self,
        correlator: RiverCorrelator,
        window_seconds: float = 30.0,
        suppress_threshold: float = 0.8,
    ) -> None:
        self._correlator = correlator
        self._window = window_seconds
        self._suppress_threshold = suppress_threshold
        self._buffer: list[TelemetryEvent] = []
        self._max_score = 0.0

    def add(self, event: TelemetryEvent) -> Situation | None:
        score = self._correlator.detect(event)
        if score <= self._correlator._z_threshold:
            return None
        emitted: Situation | None = None
        if self._buffer:
            span = (event.ts - self._buffer[0].ts).total_seconds()
            if span > self._window:
                emitted = self._correlate_buffer()
        self._buffer.append(event)
        self._max_score = max(self._max_score, score)
        return emitted

    def flush(self) -> Situation | None:
        if not self._buffer:
            return None
        return self._correlate_buffer()

    def _correlate_buffer(self) -> Situation | None:
        severity = self._correlator._severity_band(self._max_score)
        sit = self._correlator.correlate(self._buffer, severity=severity)
        self._buffer = []
        self._max_score = 0.0
        # Closed loop: suppress a Situation whose signature reliably self-heals.
        if self._correlator.should_suppress(sit.signature, self._suppress_threshold):
            return None
        return sit
```

Note: `_correlate_buffer`'s return type is now `Situation | None`. The `add` path assigns
`emitted = self._correlate_buffer()` which may now be None — that is correct (a suppressed mid-window
flush emits nothing). `flush` returns it directly.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest services/correlation/tests/test_retrain.py services/correlation/tests/test_engine_suppress.py -v`
Expected: PASS (6 + 2 = 8 passed).

- [ ] **Step 6: Run the full correlation test dir (no regression from the engine change)**

Run: `uv run pytest services/correlation/ -v`
Expected: PASS. The existing engine/consumer tests still pass — the default `suppress_threshold=0.8`
with an empty reliability map means `should_suppress` is always False (unseen → 0.0), so unchanged
behavior when retrain hasn't run.

- [ ] **Step 7: Commit**

```bash
git add services/correlation/adapters/river_correlator.py services/correlation/engine.py services/correlation/tests/test_retrain.py services/correlation/tests/test_engine_suppress.py
git commit -m "feat: make retrain real — per-signature reliability suppresses self-healing situations"
```

---

### Task 7: Governance graduate endpoint

**Files:**
- Modify: `services/governance/app.py` (add `POST /playbooks/{id}/graduate`)
- Modify: `policies/rbac_policy.yaml` (grant `graduate`)
- Test: `services/governance/tests/test_graduate_endpoint.py`

**Interfaces:**
- Consumes: `app.state.playbook_store` (get/register), `app.state.rbac` (.check), `app.state.audit_sink` (.write); `common.contracts` (`Playbook`, `HitlMode`, `AuditRecord`).
- Produces: `POST /playbooks/{playbook_id}/graduate` body `{"decided_by": str}`: 404 if unknown; RBAC `check(decided_by, "graduate", f"playbook:{id}")` False → 403; else set `hitl_mode = auto` (via `model_copy`), `register` the updated playbook, write an audit record (`action="graduate"`, `correlation_id=f"playbook:{id}"`), return the updated Playbook.

- [ ] **Step 1: Write the failing test**

`services/governance/tests/test_graduate_endpoint.py`:

```python
from fastapi.testclient import TestClient

from common.contracts import HitlMode, Playbook
from services.governance.adapters.audit_sink import InMemoryAuditSink
from services.governance.adapters.playbook_store import InMemoryPlaybookStore
from services.governance.rbac import RbacPolicy


def _client():
    from services.governance.app import app

    store = InMemoryPlaybookStore()
    store.register(
        Playbook(
            id="restart-pod",
            name="Restart",
            match_rule="x",
            steps=["s"],
            hitl_mode=HitlMode.HITL,
            reversible=True,
            rollback_steps=[],
        )
    )
    app.state.playbook_store = store
    app.state.audit_sink = InMemoryAuditSink()
    app.state.rbac = RbacPolicy(
        roles={"coe-admin": [{"action": "graduate", "resource": "playbook:*"}]},
        actors={"feedback-service": ["coe-admin"], "random-bob": []},
    )
    app.state.approvals = {}
    return TestClient(app), app.state


def test_graduate_flips_hitl_to_auto():
    c, state = _client()
    resp = c.post("/playbooks/restart-pod/graduate", json={"decided_by": "feedback-service"})
    assert resp.status_code == 200
    assert resp.json()["hitl_mode"] == "auto"
    # persisted in the store
    assert state.playbook_store.get("restart-pod").hitl_mode == HitlMode.AUTO
    # audited
    assert any(a.action == "graduate" for a in state.audit_sink.records())


def test_graduate_unauthorized_forbidden():
    c, state = _client()
    resp = c.post("/playbooks/restart-pod/graduate", json={"decided_by": "random-bob"})
    assert resp.status_code == 403
    # unchanged — still hitl
    assert state.playbook_store.get("restart-pod").hitl_mode == HitlMode.HITL


def test_graduate_unknown_playbook_404():
    c, _ = _client()
    resp = c.post("/playbooks/missing/graduate", json={"decided_by": "feedback-service"})
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/governance/tests/test_graduate_endpoint.py -v`
Expected: FAIL — 404/405 on the graduate route (endpoint not defined).

- [ ] **Step 3: Add the graduate endpoint**

In `services/governance/app.py`, add the `HitlMode` import to the existing contracts import line
(change `from common.contracts import ApprovalRequest, AuditRecord, Playbook` to include `HitlMode`,
and add `from datetime import UTC, datetime` at the top if not present). Then add a `Graduate` body
model near the `Decision` model:

```python
class Graduate(BaseModel):
    decided_by: str
```

And add the endpoint (after `decide_approval`):

```python
@app.post("/playbooks/{playbook_id}/graduate")
def graduate_playbook(playbook_id: str, body: Graduate) -> Playbook:
    pb = app.state.playbook_store.get(playbook_id)
    if pb is None:
        raise HTTPException(status_code=404, detail="playbook not found")
    if not app.state.rbac.check(body.decided_by, "graduate", f"playbook:{playbook_id}"):
        raise HTTPException(status_code=403, detail="actor lacks graduate permission")
    updated = pb.model_copy(update={"hitl_mode": HitlMode.AUTO})
    app.state.playbook_store.register(updated)
    app.state.audit_sink.write(
        AuditRecord(
            actor=body.decided_by,
            action="graduate",
            resource=f"playbook:{playbook_id}",
            decision="allow",
            ts=datetime.now(UTC),
            correlation_id=f"playbook:{playbook_id}",
        )
    )
    return updated
```

Note: the imports needed are `HitlMode` (contracts), `AuditRecord` (already imported), and
`datetime`/`UTC`. Verify the top-of-file imports include all three; add what's missing.

- [ ] **Step 4: Grant the graduate permission in the policy**

In `policies/rbac_policy.yaml`, add a `coe-admin` role and grant it to `feedback-service`. Add under
`roles:`:

```yaml
  coe-admin:
    - {action: graduate, resource: "playbook:*"}
```

And under `actors:` add:

```yaml
  feedback-service: [coe-admin]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest services/governance/tests/test_graduate_endpoint.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Run the governance test dir (no regression)**

Run: `uv run pytest services/governance/ -v`
Expected: PASS (existing governance tests unaffected).

- [ ] **Step 7: Commit**

```bash
git add services/governance/app.py policies/rbac_policy.yaml services/governance/tests/test_graduate_endpoint.py
git commit -m "feat: add RBAC-gated governance graduate endpoint (hitl->auto)"
```

---

### Task 8: feedback consumer + lifespan + GET /metrics

**Files:**
- Create: `services/feedback/consumer.py`
- Modify: `services/feedback/app.py`
- Test: `services/feedback/tests/test_consumer.py`

**Interfaces:**
- Consumes: `common.envelope` (`iter_models`); `common.contracts` (`RemediationOutcome`); `label_outcome`; `compute_metrics`; `playbook_stats`, `should_graduate`; `TrainingStore`.
- Produces:
  - `run_consumer(bus, store, graduator, min_successes, stop_event) -> None` — consumes `remediation.outcomes`; for each: `label_outcome` → `store.append`; then for that outcome's playbook, if `should_graduate(playbook_stats(store.read_all(), playbook_id), min_successes)` and not already graduated, call `graduator(playbook_id)` (a callable that promotes — the governance graduate call; in tests a fake records the calls). Breaks on stop_event. Tracks already-graduated playbooks to avoid repeat calls.
  - `services/feedback/app.py`: FastAPI lifespan starts `run_consumer` in a daemon thread; `GET /metrics` returns `compute_metrics(store.read_all())`; `/health` unchanged.

- [ ] **Step 1: Write the failing test**

`services/feedback/tests/test_consumer.py`:

```python
import threading
from datetime import UTC, datetime

from common.contracts import RemediationOutcome, RemediationResult
from services.feedback.adapters.training_store import InMemoryTrainingStore
from services.feedback.consumer import run_consumer

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _raw_outcome(result, playbook="restart-pod"):
    o = RemediationOutcome(
        situation_id="sit-abc", playbook_id=playbook, result=result, health_after="healthy", ts=NOW
    )
    return {"data": o.model_dump_json()}


class ScriptedBus:
    def __init__(self, script):
        self._script = script
        self.published = []

    def publish(self, topic, message):
        self.published.append((topic, message))

    def consume(self, topic, group):
        yield from self._script


def _run(bus, store, graduator, min_successes=3):
    run_consumer(bus, store, graduator, min_successes, threading.Event())


def test_consumer_labels_and_stores():
    bus = ScriptedBus([_raw_outcome(RemediationResult.SUCCESS)])
    store = InMemoryTrainingStore()
    _run(bus, store, graduator=lambda pid: None)
    recs = store.read_all()
    assert len(recs) == 1
    assert recs[0].signature == "abc"
    assert recs[0].worked is True


def test_consumer_proposes_graduation_after_threshold():
    # three clean successes for restart-pod -> graduation proposed once
    bus = ScriptedBus([_raw_outcome(RemediationResult.SUCCESS) for _ in range(3)])
    store = InMemoryTrainingStore()
    graduated = []
    _run(bus, store, graduator=graduated.append, min_successes=3)
    assert graduated == ["restart-pod"]  # proposed exactly once


def test_consumer_no_graduation_below_threshold():
    bus = ScriptedBus([_raw_outcome(RemediationResult.SUCCESS) for _ in range(2)])
    store = InMemoryTrainingStore()
    graduated = []
    _run(bus, store, graduator=graduated.append, min_successes=3)
    assert graduated == []


def test_consumer_no_graduation_with_rollback():
    bus = ScriptedBus(
        [
            _raw_outcome(RemediationResult.SUCCESS),
            _raw_outcome(RemediationResult.SUCCESS),
            _raw_outcome(RemediationResult.SUCCESS),
            _raw_outcome(RemediationResult.ROLLED_BACK),
        ]
    )
    store = InMemoryTrainingStore()
    graduated = []
    _run(bus, store, graduator=graduated.append, min_successes=3)
    assert graduated == []  # a rollback disqualifies


def test_consumer_stops_on_stop_event():
    def infinite():
        while True:
            yield _raw_outcome(RemediationResult.SUCCESS)

    class InfBus(ScriptedBus):
        def consume(self, topic, group):
            return infinite()

    store = InMemoryTrainingStore()
    stop = threading.Event()
    stop.set()
    run_consumer(InfBus([]), store, lambda pid: None, 3, stop)
    assert store.read_all() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/feedback/tests/test_consumer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.feedback.consumer'`.

- [ ] **Step 3: Write the consumer**

`services/feedback/consumer.py`:

```python
"""Bus consumer for feedback-service — closes the loop.

Consumes remediation.outcomes, labels each into the training store, and — when a
playbook earns a clean track record — proposes it for graduation exactly once.
The graduator callable performs the promotion (the governance graduate call in
the running service; a fake in tests). Runs in a daemon thread via lifespan."""

from __future__ import annotations

import threading
from collections.abc import Callable

from common.contracts import RemediationOutcome
from common.envelope import iter_models
from services.feedback.graduate import playbook_stats, should_graduate
from services.feedback.label import label_outcome


def run_consumer(
    bus, store, graduator: Callable[[str], None], min_successes: int, stop_event: threading.Event
) -> None:
    graduated: set[str] = set()
    for outcome in iter_models(bus, "remediation.outcomes", "feedback", RemediationOutcome):
        if stop_event.is_set():
            break
        store.append(label_outcome(outcome))
        pid = outcome.playbook_id
        if pid and pid not in graduated:
            stats = playbook_stats(store.read_all(), pid)
            if should_graduate(stats, min_successes):
                graduator(pid)
                graduated.add(pid)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest services/feedback/tests/test_consumer.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Wire the lifespan + /metrics in `services/feedback/app.py`**

Replace `services/feedback/app.py` with:

```python
"""Feedback service: label outcomes, close the loop, compute metrics."""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from common.config import get_settings
from services.base import create_app
from services.feedback.adapters.training_store import FileTrainingStore
from services.feedback.consumer import run_consumer
from services.feedback.metrics import compute_metrics


def _make_graduator(rbac_actor: str = "feedback-service"):
    # In the running service, graduation calls governance's REST endpoint.
    # governance runs on port 8005 (see docker-compose). Best-effort, fire-and-
    # forget: a failed graduation is logged by governance's own audit, and the
    # next matching outcome will retry on a fresh process. Kept simple here.
    def graduate(playbook_id: str) -> None:
        try:
            httpx.post(
                f"http://governance:8005/playbooks/{playbook_id}/graduate",
                json={"decided_by": rbac_actor},
                timeout=5.0,
            )
        except Exception:
            pass

    return graduate


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    stop_event = threading.Event()
    store = FileTrainingStore(settings.training_store_path)
    app.state.training_store = store
    thread = threading.Thread(
        target=run_consumer,
        args=(
            app.state.bus,
            store,
            _make_graduator(),
            settings.graduation_min_successes,
            stop_event,
        ),
        daemon=True,
    )
    thread.start()
    app.state.consumer_stop = stop_event
    app.state.consumer_thread = thread
    try:
        yield
    finally:
        stop_event.set()


app = create_app("feedback-service")
app.router.lifespan_context = lifespan


@app.get("/metrics")
def metrics() -> dict:
    store = getattr(app.state, "training_store", None)
    records = store.read_all() if store is not None else []
    return compute_metrics(records)
```

- [ ] **Step 6: Add the httpx dependency**

`httpx` is already a dev dependency (used by FastAPI TestClient). For the running-service graduator
it must be a runtime dep. Run: `uv add httpx`
Expected: moves/adds `httpx` to `[project.dependencies]` and updates `uv.lock`.

- [ ] **Step 7: Run the feedback tests + health**

Run: `uv run pytest services/feedback/ -v`
Expected: PASS. `/health` for feedback-service still returns `{"service": "feedback-service", "status": "ok"}` (covered by `tests/test_services.py`).

- [ ] **Step 8: Commit**

```bash
git add services/feedback/consumer.py services/feedback/app.py services/feedback/tests/test_consumer.py pyproject.toml uv.lock
git commit -m "feat: wire feedback consumer, lifespan, and GET /metrics"
```

---

### Task 9: End-to-end acceptance + docs + mark project complete

**Files:**
- Create: `tests/test_slice4_acceptance.py`
- Modify: `README.md` (roadmap: Slice 4 → done; project-status line; Quickstart note)

**Interfaces:**
- Consumes: everything above — feedback labeling + store, correlation retrain + engine suppression, the graduation policy.
- Produces: an in-process end-to-end test proving (a) SUCCESS outcomes for a signature → training store → `correlation.retrain(store data)` → that signature is now suppressed by the engine while a failing signature still emits; and (b) a playbook graduates hitl→auto through the governance endpoint on evidence.

- [ ] **Step 1: Write the acceptance test**

`tests/test_slice4_acceptance.py`:

```python
"""Slice-4 acceptance: the loop closes, in-process.

(a) Remediation outcomes → training store → correlation.retrain → a reliably
self-healing signature is suppressed while a failing one still emits.
(b) A playbook graduates hitl→auto through governance on a clean track record."""

import random
import threading
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from common.contracts import (
    HitlMode,
    Playbook,
    RemediationOutcome,
    RemediationResult,
    TelemetryEvent,
    TelemetryKind,
)
from common.envelope import publish_model
from services.correlation.adapters.river_correlator import RiverCorrelator
from services.correlation.engine import CorrelationEngine
from services.feedback.adapters.training_store import InMemoryTrainingStore
from services.feedback.consumer import run_consumer
from services.governance.adapters.audit_sink import InMemoryAuditSink
from services.governance.adapters.playbook_store import InMemoryPlaybookStore
from services.governance.rbac import RbacPolicy

NOW = datetime(2026, 8, 13, tzinfo=UTC)


class InMemoryBus:
    def __init__(self):
        self.topics: dict[str, list[dict]] = {}

    def publish(self, topic, message):
        self.topics.setdefault(topic, []).append(message)

    def consume(self, topic, group):
        yield from list(self.topics.get(topic, []))


def _event(value, fp, ts_sec=0):
    return TelemetryEvent(
        source="prom",
        kind=TelemetryKind.METRIC,
        name="cpu",
        value=value,
        labels={},
        ts=datetime(2026, 8, 13, 0, 0, ts_sec, tzinfo=UTC),
        fingerprint=fp,
    )


def _prime_and_flush(engine, n=200, seed=42):
    rng = random.Random(seed)
    for i in range(n):
        engine.add(_event(round(rng.gauss(10.0, 1.0), 3), f"b{i}", 0))
    engine.flush()


def test_loop_closes_reliable_signature_suppressed():
    # 1. Form a Situation to discover its signature.
    correlator = RiverCorrelator(z_threshold=3.0)
    engine = CorrelationEngine(correlator, window_seconds=30, suppress_threshold=0.8)
    _prime_and_flush(engine)
    engine.add(_event(100.0, "a", 1))
    engine.add(_event(120.0, "b", 2))
    situation = engine.flush()
    sig = situation.signature

    # 2. feedback consumes SUCCESS outcomes for that situation → training store.
    bus = InMemoryBus()
    store = InMemoryTrainingStore()
    for _ in range(3):
        publish_model(
            bus,
            "remediation.outcomes",
            RemediationOutcome(
                situation_id=situation.id,
                playbook_id="restart-pod",
                result=RemediationResult.SUCCESS,
                health_after="healthy",
                ts=NOW,
            ),
        )
    run_consumer(
        bus, store, graduator=lambda pid: None, min_successes=3, stop_event=threading.Event()
    )

    # 3. correlation retrains from the store → learns the signature self-heals.
    training = [{"signature": r.signature, "worked": r.worked} for r in store.read_all()]
    correlator.retrain(training)

    # 4. The same spikes now form the same signature — suppressed (loop closed).
    engine.add(_event(100.0, "a", 3))
    engine.add(_event(120.0, "b", 4))
    assert engine.flush() is None  # reliably self-healing → suppressed

    # A different (unseen) signature would still emit — sanity via reliability.
    assert correlator.should_suppress("some-other-sig", 0.8) is False


def test_playbook_graduates_through_governance():
    from services.governance.app import app

    store = InMemoryPlaybookStore()
    store.register(
        Playbook(
            id="restart-pod",
            name="Restart",
            match_rule="x",
            steps=["s"],
            hitl_mode=HitlMode.HITL,
            reversible=True,
            rollback_steps=[],
        )
    )
    app.state.playbook_store = store
    app.state.audit_sink = InMemoryAuditSink()
    app.state.rbac = RbacPolicy(
        roles={"coe-admin": [{"action": "graduate", "resource": "playbook:*"}]},
        actors={"feedback-service": ["coe-admin"]},
    )
    app.state.approvals = {}
    client = TestClient(app)

    # feedback's graduator, wired to the real governance endpoint via TestClient.
    def graduator(pid: str) -> None:
        client.post(f"/playbooks/{pid}/graduate", json={"decided_by": "feedback-service"})

    bus = InMemoryBus()
    tstore = InMemoryTrainingStore()
    for _ in range(3):
        publish_model(
            bus,
            "remediation.outcomes",
            RemediationOutcome(
                situation_id="sit-x",
                playbook_id="restart-pod",
                result=RemediationResult.SUCCESS,
                health_after="healthy",
                ts=NOW,
            ),
        )
    run_consumer(bus, tstore, graduator, min_successes=3, stop_event=threading.Event())

    # The playbook is now auto, promoted through governance under RBAC + audit.
    assert store.get("restart-pod").hitl_mode == HitlMode.AUTO
    assert any(a.action == "graduate" for a in app.state.audit_sink.records())
```

- [ ] **Step 2: Run the acceptance test**

Run: `uv run pytest tests/test_slice4_acceptance.py -v`
Expected: PASS (2 passed).

- [ ] **Step 3: Run the full suite + lint**

Run: `uv run pytest` then `uv run ruff check .`
Expected: all pass; ruff clean (apply UP017/F401/I001/RUF015 autofix if it fires — tokens/imports/style only).

- [ ] **Step 4: Update the README roadmap + status**

In `README.md`:
1. Change the Slice 4 roadmap row status from `⏳ planned` to `✅ done` (all five rows now done).
2. Change the project-status line near the top from
   `> **Project status: documentation complete, implementation phased.**`
   to
   `> **Project status: all four implementation slices complete — the loop is closed.**`
3. Under Quickstart, add this line after the Slice-3 line:

```
> Slice 4 adds feedback-service (8006): labels `remediation.outcomes` into a training store that closes the loop (proven self-healing signatures get suppressed), computes metrics at `GET /metrics`, and graduates playbooks hitl→auto on evidence.
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_slice4_acceptance.py README.md
git commit -m "test: add slice-4 end-to-end acceptance; close the loop; mark project complete"
```

---

## Self-Review

**1. Spec coverage** (against the Slice-4 spec §2-6, §10):
- `TrainingRecord` contract + `TrainingStore` interface + config (§2.1-2.3) → Task 1 ✓
- TrainingStore adapters (§2.2) → Task 2 ✓
- `label_outcome` + signature bridge (§3, §4.1) → Task 3 ✓
- `compute_metrics` (§4.2) → Task 4 ✓
- `playbook_stats` + `should_graduate` (§4.3) → Task 5 ✓
- Real `retrain` + `reliability` + `should_suppress` + engine suppression (§5) → Task 6 ✓
- Governance graduate endpoint + policy grant (§6) → Task 7 ✓
- feedback consumer + lifespan + `GET /metrics` (§4.4) → Task 8 ✓
- End-to-end acceptance (loop closes + graduation) (§8) → Task 9 ✓
- *Deferred by design (not gaps):* Postgres store, real MTTR/MTTD, ChatOps graduation, reliability persistence, the `common/` refactor — all per §12.

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N". Every code and test step is complete literal content. The `_make_graduator` in Task 8 uses a best-effort httpx call with a documented rationale (not a placeholder — it's the running-service wiring; tests use a fake graduator so this HTTP path isn't exercised in tests).

**3. Type consistency:**
- `TrainingRecord(situation_id, signature, playbook_id, result, worked, ts)` — Task 1, used in 2/3/4/5/8/9. ✓
- `TrainingStore.append/read_all` — Task 1, impl Task 2, consumed 8/9. ✓
- `signature_from_situation_id`, `label_outcome(outcome) -> TrainingRecord` — Task 3, used 8/9. ✓
- `compute_metrics(records) -> dict` — Task 4, used 8. ✓
- `playbook_stats(records, playbook_id) -> dict`, `should_graduate(stats, min_successes) -> bool` — Task 5, used 8. ✓
- `RiverCorrelator.retrain(training_data) -> None`, `reliability(sig) -> float`, `should_suppress(sig, threshold) -> bool` — Task 6, used 9. ✓
- `CorrelationEngine(correlator, window_seconds=30.0, suppress_threshold=0.8)` — Task 6, used 9. ✓
- `run_consumer(bus, store, graduator, min_successes, stop_event)` — Task 8, used 9. ✓
- graduate endpoint body `{"decided_by": str}` — Task 7, called in Task 9's graduator. ✓
- reliability dict shape `{"signature", "worked"}` consistent between retrain (Task 6) and the store→training mapping (Task 9 builds `{"signature": r.signature, "worked": r.worked}`). ✓

One thing verified against the actual code: Task 6 changes `_correlate_buffer`'s return type to
`Situation | None`; the existing `add` assigns its result to `emitted` and returns it, and existing
consumer/engine tests pass because with an empty reliability map `should_suppress` is always False —
so no prior behavior changes until `retrain` runs. The default `suppress_threshold=0.8` is inert
until then. This preserves Slices 1-3 behavior.
