# Stream B — Intelligence: Detection & RCA — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make detection & RCA genuinely intelligent and *measure* it: a `CORRELATOR_KIND` switch with a robust/seasonal online detector and a trainable IsolationForest (persisted fine-tune loop), evidence-driven RCA that uses learned reliability, an on-by-default (config-selected) LLM explanation, and a reproducible CI-enforced benchmark.

**Architecture:** A `BaseCorrelator` ABC carries the engine-facing contract; `RiverCorrelator` (default, unchanged behavior) is refactored onto it by a pure move; `RobustCorrelator` (numpy window + seasonal MAD) and `TrainedCorrelator` (sklearn IsolationForest, persisted) subclass it, selected by `CORRELATOR_KIND`. RCA gains reliability-weighted ranking + an `ExplanationProvider` (template default, OpenAI-compatible when configured). A benchmark harness proves measured gains.

**Tech Stack:** Python + uv, river 0.25 / numpy 2.4 / scipy 1.17 (present), **scikit-learn (new dep)**, httpx 0.28 (present), SQLAlchemy + Alembic, pytest.

**Spec:** [docs/superpowers/specs/2026-08-24-stream-b-intelligence-design.md](../specs/2026-08-24-stream-b-intelligence-design.md)

## Global Constraints

- **Test-safe by default.** `CORRELATOR_KIND=river` (default) + existing config defaults leave the full suite green with no new infra. `robust`/`trained` opt-in. LLM explanation on-by-default but the *provider* is template (no network) unless `llm_explanation_endpoint` is set.
- **Gates:** `uv run pytest -m "not postgres and not kafka"` green; `uv run ruff check` + `uv run ruff format --check .` clean.
- **New deps:** `scikit-learn` + `numpy` (numpy currently transitive-only — declare it) added to `pyproject.toml`. **Task 3 must first prove `uv add` resolves against locked numpy 2.4.6 / scipy 1.17.1 without downgrading them or breaking river, and that the suite stays green — BEFORE writing TrainedCorrelator code.** sklearn/joblib imported **lazily inside TrainedCorrelator methods only**.
- **Additive contracts only.** `explanation: str | None = None` added to `RootCauseHypothesis` (additive). No field-meaning changes.
- **The engine reaches correlator internals** (`_z_threshold`, `_warmup_samples`, `_severity_band`, `should_suppress`, `snapshot`/`load`, and a `type(correlator)(z_threshold=…, warmup_samples=…)` reset factory). **Every new correlator MUST subclass `BaseCorrelator`, store `_warmup_samples` (exact name), and give every extra `__init__` kwarg a default** or `reset()` crashes.
- **Commit trailer:** every commit ends with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## Task 1: `BaseCorrelator` ABC + refactor `RiverCorrelator` onto it (pure move, behavior-identical)

**Files:**
- Create: `services/correlation/adapters/base_correlator.py`
- Modify: `services/correlation/adapters/river_correlator.py` (inherit base, delete hoisted methods)
- Test: existing correlation tests must stay green (no new test file; this is a refactor proven by the existing suite)

**Interfaces:**
- Produces: `BaseCorrelator(ABC)` with concrete `_severity_band`/`reliability`/`should_suppress`/`retrain`/`_signature`/`is_anomaly` and `@abstractmethod` `detect`/`correlate`/`snapshot`/`load`; `__init__(z_threshold=3.0, warmup_samples=50)` setting `_z_threshold`/`_warmup_samples`/`_reliability`.
- Consumes: nothing new.

- [ ] **Step 1: Write `base_correlator.py` — hoist the verbatim-identical shared logic**

```python
"""Engine-facing contract for pluggable correlators.

CorrelationEngine depends on more than the Correlator Protocol (detect/correlate/
retrain): it reads _z_threshold/_warmup_samples, calls _severity_band, should_suppress,
snapshot, load, and reconstructs the correlator via type(correlator)(z_threshold=,
warmup_samples=) on reset(). This ABC makes that implicit contract explicit and shared.

Subclasses MUST accept z_threshold and warmup_samples (the reset factory passes exactly
those); any extra __init__ kwargs MUST have defaults or reset() raises TypeError.
A subclass that overrides retrain to also train a model MUST call super().retrain(data)
to preserve the reliability map, or closed-loop suppression silently stops working.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod

from common.contracts import Situation, TelemetryEvent


class BaseCorrelator(ABC):
    def __init__(self, z_threshold: float = 3.0, warmup_samples: int = 50) -> None:
        self._z_threshold = z_threshold
        self._warmup_samples = warmup_samples
        self._reliability: dict[str, float] = {}

    @abstractmethod
    def detect(self, event: TelemetryEvent) -> float: ...

    @abstractmethod
    def correlate(self, events: list[TelemetryEvent], severity: str = "low") -> Situation: ...

    @abstractmethod
    def snapshot(self) -> list[dict]: ...

    @abstractmethod
    def load(self, rows: list[dict]) -> None: ...

    def is_anomaly(self, event: TelemetryEvent) -> bool:
        return self.detect(event) > self._z_threshold

    def retrain(self, training_data: list[dict]) -> None:
        # REPLACE semantics (recompute from scratch each call) — pinned by test_retrain.py.
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

    def _severity_band(self, score: float) -> str:
        if score >= 8:
            return "high"
        if score >= 5:
            return "medium"
        return "low"

    @staticmethod
    def _signature(events: list[TelemetryEvent]) -> str:
        joined = "|".join(sorted(e.fingerprint for e in events))
        return hashlib.sha1(joined.encode()).hexdigest()[:16]
```

- [ ] **Step 2: Refactor `RiverCorrelator` to inherit `BaseCorrelator`**

In `river_correlator.py`: `class RiverCorrelator(BaseCorrelator):`. In `__init__`, call `super().__init__(z_threshold, warmup_samples)` then set the river-specific `self._mean`/`self._var`/`self._count` (KEEP those). **DELETE** the now-inherited: local `_z_threshold`/`_warmup_samples`/`_reliability` assignments, `_severity_band`, `reliability`, `should_suppress`, `retrain`, `_signature`, `is_anomaly`. **KEEP** (as overrides): `detect`, `correlate`, `snapshot`, `load`. Add `from services.correlation.adapters.base_correlator import BaseCorrelator`.

- [ ] **Step 3: Run the full correlation suite — prove no regression**

Run: `uv run pytest services/correlation/tests tests/test_baseline_codec.py tests/test_baseline_persistence.py -q`
Expected: all PASS (test_river_correlator, test_retrain, test_engine, test_engine_suppress, test_reset — which directly exercises the reset factory —, test_suppressed_signal which subclasses RiverCorrelator, test_reload). If any fail, the hoist changed behavior — diff the deleted method against the base method for byte-identity.

- [ ] **Step 4: ruff + commit**

Run: `uv run ruff check services/correlation/ && uv run ruff format --check services/correlation/`

```bash
git add services/correlation/adapters/base_correlator.py services/correlation/adapters/river_correlator.py
git commit -m "refactor(correlation): BaseCorrelator ABC; RiverCorrelator inherits (pure move)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `CORRELATOR_KIND` switch + `RobustCorrelator` (numpy window + seasonal MAD)

**Files:**
- Modify: `common/config.py` (add `correlator_kind` + robust tuning fields)
- Create: `services/correlation/adapters/robust_correlator.py`
- Create: `services/correlation/adapters/seasonal_store.py` (new store — the windowed state CANNOT reuse `correlation_baseline`)
- Modify: `common/db.py` (new `seasonal_baseline` table) + `alembic/versions/0003_*.py` (migration) — OR keep robust snapshot in-memory-only for v1 (see Step note)
- Modify: `services/correlation/adapters/__init__.py` (add `make_correlator(settings)`)
- Modify: `services/correlation/app.py` (use `make_correlator`)
- Test: `services/correlation/tests/test_robust_correlator.py` (new)

**Interfaces:**
- Consumes: `BaseCorrelator` (Task 1).
- Produces: `RobustCorrelator(BaseCorrelator)`; `make_correlator(settings) -> BaseCorrelator`; `settings.correlator_kind`.

- [ ] **Step 1: Config fields (additive, test-safe defaults)**

`common/config.py`, add to `Settings`:
```python
    correlator_kind: str = "river"          # "river" | "robust" | "trained"
    correlation_seasonal_buckets: int = 24
    correlation_robust_window: int = 128
    correlation_robust_warmup: int = 30
```

- [ ] **Step 2: Write failing tests for `RobustCorrelator`**

`services/correlation/tests/test_robust_correlator.py`: assert (a) a flat metric never flags (MAD==0 → score 0), (b) a spike after a stable window scores high, (c) **a single earlier spike does NOT desensitize** (robustness — the whole point: after one spike, a second same-size spike still scores high, unlike the z-score), (d) two different hour buckets keep independent baselines, (e) snapshot→load round-trips the windows (reconstruct identical scores), (f) `reset()` via `type(c)(z_threshold=…, warmup_samples=…)` works (extra kwargs default). Use `datetime(..., tzinfo=UTC)` timestamps.

- [ ] **Step 3: Implement `RobustCorrelator` (inherit BaseCorrelator; numpy batch MAD)**

Add `numpy>=2` to `pyproject.toml` `[project].dependencies` (currently transitive-only). `robust_correlator.py`:

```python
from __future__ import annotations

import collections

import numpy as np

from common.contracts import Situation, SituationStatus, TelemetryEvent
from services.correlation.adapters.base_correlator import BaseCorrelator

_MAD_C = 1.4826  # MAD -> sigma consistency constant for normal data


class RobustCorrelator(BaseCorrelator):
    def __init__(self, z_threshold: float = 3.0, warmup_samples: int = 30,
                 seasonal_buckets: int = 24, window_size: int = 128) -> None:
        super().__init__(z_threshold, warmup_samples)   # sets _z_threshold/_warmup_samples/_reliability
        self._n_buckets = seasonal_buckets
        self._window_size = window_size
        self._windows: dict[tuple[str, int], collections.deque] = {}

    def _bucket(self, event: TelemetryEvent) -> int:
        return event.ts.hour % self._n_buckets

    def detect(self, event: TelemetryEvent) -> float:
        if event.value is None:
            return 0.0
        key = (event.name, self._bucket(event))
        win = self._windows.setdefault(key, collections.deque(maxlen=self._window_size))
        if len(win) < self._warmup_samples:
            score = 0.0
        else:
            arr = np.fromiter(win, dtype=float, count=len(win))
            med = np.median(arr)
            mad = np.median(np.abs(arr - med))
            score = 0.0 if mad == 0.0 else abs(event.value - med) / (_MAD_C * mad)
        win.append(event.value)          # score-before-fold (matches RiverCorrelator ordering)
        return float(score)

    def correlate(self, events: list[TelemetryEvent], severity: str = "low") -> Situation:
        if not events:
            raise ValueError("cannot correlate an empty event list")
        signature = self._signature(events)
        return Situation(id="sit-" + signature, status=SituationStatus.DETECTED,
                         member_events=list(events), severity=severity,
                         first_seen=min(e.ts for e in events), last_seen=max(e.ts for e in events),
                         signature=signature)

    def snapshot(self) -> list[dict]:
        out: list[dict] = []
        for (name, bucket), win in list(self._windows.items()):   # list() = live-resize guard
            out.append({"metric_name": name, "bucket": bucket, "n": len(win), "window": list(win)})
        return out

    def load(self, rows: list[dict]) -> None:
        for r in rows:
            key = (r["metric_name"], int(r["bucket"]))
            self._windows[key] = collections.deque(
                (float(x) for x in r["window"]), maxlen=self._window_size)
```

- [ ] **Step 4: `make_correlator` factory + wire `app.py`**

`services/correlation/adapters/__init__.py`:
```python
def make_correlator(settings):
    kind = settings.correlator_kind
    if kind == "river":
        return RiverCorrelator(z_threshold=settings.correlation_z_threshold,
                               warmup_samples=settings.correlation_warmup_samples)
    if kind == "robust":
        return RobustCorrelator(z_threshold=settings.correlation_z_threshold,
                                warmup_samples=settings.correlation_robust_warmup,
                                seasonal_buckets=settings.correlation_seasonal_buckets,
                                window_size=settings.correlation_robust_window)
    if kind == "trained":
        raise NotImplementedError("trained correlator lands in Task 3")
    raise ValueError(f"Unknown CORRELATOR_KIND: {kind!r}")
```
In `correlation/app.py:84`, replace the direct `RiverCorrelator(...)` with `make_correlator(settings)`.

- [ ] **Step 5: Robust seasonal persistence — DECISION**

The robust window state (`{metric_name, bucket, window}`) **cannot** use `correlation_baseline` (PK on `metric_name` alone → 24 buckets collide; no window column) or the existing `BaselineStore`. **Ruling for v1:** robust snapshot/load stays **in-process** (the engine's `snapshot()`/`load()` still work in-memory for `reset()` round-trip; the *durable* seasonal store is deferred). The `robust` correlator therefore cold-starts on restart — acceptable and documented (it warms per-bucket). *(A durable `seasonal_baseline(metric_name,bucket,window)` table + store + `0003` migration can be added later; do NOT block Stream B on it.)* If the implementer finds durable robust state is cheap to add, they may — but it is NOT required and must not touch the `trained` migration (Task 3 owns `0003`).

- [ ] **Step 6: Run robust tests + full correlation suite (river default still green)**

Run: `uv run pytest services/correlation/tests/ -q && uv run ruff check services/correlation/ common/config.py`
Expected: robust tests PASS; all prior correlation tests still PASS (river default unchanged).

- [ ] **Step 7: Commit**

```bash
git add common/config.py services/correlation/adapters/robust_correlator.py services/correlation/adapters/__init__.py services/correlation/app.py services/correlation/tests/test_robust_correlator.py pyproject.toml
git commit -m "feat(correlation): CORRELATOR_KIND switch + RobustCorrelator (seasonal robust-MAD)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: `TrainedCorrelator` (sklearn IsolationForest, persisted fine-tune loop)

**Files:**
- Modify: `pyproject.toml` (add `scikit-learn`, `joblib`)
- Modify: `common/db.py` (new `model_artifacts` table) + Create `alembic/versions/0003_model_artifacts.py`
- Create: `services/correlation/adapters/model_store.py` (InMemory + Postgres, best-effort like BaselineStore)
- Create: `services/correlation/adapters/trained_correlator.py`
- Modify: `common/stores.py` (thread `model_store` through `make_stores` + the `Stores` dataclass)
- Modify: `services/correlation/adapters/__init__.py` (`make_correlator` `trained` branch)
- Modify: `services/correlation/app.py` + a flusher hook (the real fit trigger) + `POST /retrain`
- Test: `services/correlation/tests/test_trained_correlator.py` (new)

**Interfaces:**
- Consumes: `BaseCorrelator`, `RobustCorrelator` (composes the online path).
- Produces: `TrainedCorrelator(BaseCorrelator)`; `ModelStore` (save/load_latest); `POST /retrain` trigger.

- [ ] **Step 1: PROVE the dependency resolves BEFORE writing code (mandatory gate)**

Run: `uv add scikit-learn joblib` then `uv run pytest -m "not postgres and not kafka" -q`
Expected: resolution succeeds WITHOUT downgrading numpy (must stay 2.4.x) or scipy (1.17.x), and the full fast suite stays green. Verify: `uv run python -c "import numpy, scipy, sklearn, river; print(numpy.__version__, scipy.__version__, sklearn.__version__)"`. **If resolution downgrades numpy/scipy or reddens the suite, STOP and report** — do not proceed; the trained path is not viable as-is and needs a version-pin discussion.

- [ ] **Step 2: `model_artifacts` table + `0003` migration**

`common/db.py`: `from sqlalchemy import LargeBinary` and
```python
model_artifacts = Table(
    "model_artifacts", METADATA,
    Column("name", String, primary_key=True),
    Column("artifact", LargeBinary, nullable=False),   # -> bytea on postgres
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
```
`alembic/versions/0003_model_artifacts.py` (follow `0002`'s shape; `down_revision = "0002_runtime_state"`, `revision = "0003_model_artifacts"`), `create_table(model_artifacts)` in `upgrade`, `drop_table` in `downgrade`.

- [ ] **Step 3: `ModelStore` (mirror `BaselineStore`, best-effort)**

`model_store.py`: `InMemoryModelStore` + `PostgresModelStore`, interface `save(name, blob)` / `load_latest(name) -> bytes | None`, using `pg_insert(...).on_conflict_do_update` on the `name` PK (see the verified sketch in the spec/findings). Errors are swallowed by the *caller* (the flusher/reload), matching BaselineStore's posture.

- [ ] **Step 4: `TrainedCorrelator` — write failing tests first**

`test_trained_correlator.py`: (a) cold-start (no model) → `detect` returns the online score alone, byte-identical to composing RobustCorrelator; (b) `reset()` via `type(c)(z_threshold=, warmup_samples=)` works (extra kwargs default); (c) after enough observed events, `fit()` produces a model and a planted outlier scores ABOVE a cluster point (the sign-convention test — `-score_samples`); (d) `serialize()`→`load_model()` round-trips (a loaded model scores identically); (e) feature-name drift on load → refuse (stay cold); (f) `snapshot`/`load` (the engine baseline path) delegate to the composed robust correlator and do NOT collide with `load_model`.

- [ ] **Step 5: Implement `TrainedCorrelator(BaseCorrelator)` — ALL logic in `detect()`, lazy sklearn**

Key corrections from the verified findings, MUST honor:
- **Subclass `BaseCorrelator`**; `__init__(self, z_threshold=3.0, warmup_samples=30, seasonal_buckets=24, window_size=128, min_fit_samples=200, contamination=0.02)` — every extra kwarg defaulted (reset factory).
- **Compose a `RobustCorrelator`** internally for the online path (spec says trained wraps robust).
- **`detect(event)`**: featurize → append to the feature deque → online = `self._robust.detect(event)` → if model fitted, `model_score = _rescale(-model.score_samples([row])[0])` → return `max(online, model_score)`. NO `observe()` method — the engine only calls `detect()`.
- **Model methods renamed to avoid the `load()` collision**: `serialize() -> bytes | None`, `load_model(blob)`, `fit()`. Keep `snapshot()`/`load(rows)` delegating to the composed robust correlator (the engine baseline path).
- **Lazy imports**: `from sklearn.ensemble import IsolationForest` and `import joblib` INSIDE `fit`/`serialize`/`load_model` only.
- `FEATURE_NAMES` frozen module-level; `featurize(event, z_online)` per the verified 9-column sketch; persist `feature_names` with the blob and refuse-load on drift.

- [ ] **Step 6: The REAL fit trigger — `POST /retrain` + optional flusher hook**

The verified finding proved `retrain()`-at-boot has an empty deque → never fits. Add a real trigger:
- `POST /retrain` on `correlation/app.py`: calls `engine._correlator.fit()` then persists via `model_store.save(...)` (best-effort). This is what the demo/benchmark fires to show learning.
- Thread `model_store` through `make_stores`/`Stores` (file mode → `InMemoryModelStore` or None; postgres → `PostgresModelStore`); on boot, `load_model(model_store.load_latest(...))` in the reload path (best-effort, cold-start on failure).
- `make_correlator` `trained` branch constructs `TrainedCorrelator(...)`.

- [ ] **Step 7: Run trained tests + full suite + lint; commit**

Run: `uv run pytest services/correlation/tests/ -q && uv run ruff check services/correlation/ common/ && uv run ruff format --check .`
Expected: all PASS, lint clean, river default suite unaffected.

```bash
git add pyproject.toml uv.lock common/db.py alembic/versions/0003_model_artifacts.py services/correlation/adapters/model_store.py services/correlation/adapters/trained_correlator.py common/stores.py services/correlation/adapters/__init__.py services/correlation/app.py services/correlation/tests/test_trained_correlator.py
git commit -m "feat(correlation): TrainedCorrelator (sklearn IsolationForest) + persisted fit/retrain loop

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Evidence-driven RCA + reliability + on-by-default LLM explanation

**Files:**
- Modify: `common/contracts.py` (additive `explanation: str | None = None` on `RootCauseHypothesis`)
- Modify: `common/interfaces.py` (`ExplanationProvider` Protocol)
- Modify: `common/config.py` (`llm_explanation_*` fields)
- Create: `services/rca/adapters/explanation_provider.py` (Template + OpenAICompatible + `make_explanation_provider`)
- Modify: `services/rca/rank.py` (optional `reliability_provider`)
- Modify: `services/rca/consumer.py` (thread `explainer`; advisory-only wiring)
- Modify: `services/rca/app.py` (build explainer + reliability from `training_store`)
- Modify: `services/rca/tests/test_consumer.py` (**patch the 3 existing calls** — REQUIRED)
- Test: `services/rca/tests/test_explanation_provider.py` (new); extend `test_rank.py`

**Interfaces:**
- Produces: `ExplanationProvider.explain(hypothesis, context, situation) -> str`; `rank_hypotheses(situation, context, reliability_provider=None)`.

- [ ] **Step 1: Additive contract field + Protocol + config**

`common/contracts.py` — `RootCauseHypothesis` gains `explanation: str | None = None` (after `suggested_runbook_id`). `common/interfaces.py` — add `ExplanationProvider` Protocol (`explain(hypothesis, context, situation) -> str`). `common/config.py` — add `llm_explanation_endpoint: str = ""`, `llm_explanation_model: str = "gpt-4o-mini"`, `llm_explanation_timeout_seconds: float = 10.0`, `llm_explanation_api_key: str = ""`.

- [ ] **Step 2: `explanation_provider.py` (write tests first)**

`test_explanation_provider.py` (mirror `services/ingestion/tests/test_prometheus_source.py` — its REAL location — for the httpx.MockTransport pattern): template-is-offline-deterministic; posts-openai-shape (assert url ends `/chat/completions`, model, system message); five error→fallback cases (ConnectError, non-200, non-JSON, missing choices, empty content) all return the template output and NONE raise; factory selection (empty endpoint → Template, set endpoint → OpenAICompatible). Then implement the two providers + `make_explanation_provider` per the verified sketch (sync `httpx.Client`, `http_client` injection, every failure path → template fallback).

- [ ] **Step 3: Reliability-weighted ranking (backward-compatible)**

`rank.py`: `rank_hypotheses(situation, context, reliability_provider=None)`. When `None`, behavior UNCHANGED (existing `test_rank.py` passes). When provided (callable `signature -> float`), boost a hypothesis whose `suggested_runbook_id` has a proven track record for `situation.signature` — bounded to [0,1], deterministic, and the top suggestion still resolves to a real playbook id. Extend `test_rank.py` with a reliability-provided case; keep the no-provider cases green.

- [ ] **Step 4: Wire consumer + app (advisory-only) — and FIX the 3 existing test calls**

`consumer.py`: `diagnose(situation, provider, store, explainer)` and `run_consumer(..., explainer, stop_event)` (explainer BEFORE stop_event). After `surface_runbook`, set advisory text on the top hypothesis ONLY via `model_copy(update={"explanation": explainer.explain(top, context, situation)})` (preserves confidence/order/runbook). `app.py`: build `explainer = make_explanation_provider(settings)`; pull `training_store` from the `make_stores` it already calls, build a `reliability_provider` from `training_store.read_all()` (per-signature worked/total), pass it into `diagnose`→`rank_hypotheses`; best-effort (read fails → `None`).

**REQUIRED (else the suite reddens):** update the 3 existing calls in `services/rca/tests/test_consumer.py` (≈ lines 55, 66, 92) to pass a provider — `diagnose(..., TemplateExplanationProvider())` and `run_consumer(..., TemplateExplanationProvider(), threading.Event())` (explainer before stop_event).

- [ ] **Step 5: Run the RCA suite + full fast suite + lint; commit**

Run: `uv run pytest services/rca/tests/ -q && uv run pytest -m "not postgres and not kafka" -q && uv run ruff check services/rca/ common/`
Expected: all PASS (incl. the 3 patched calls), full suite green, lint clean.

```bash
git add common/contracts.py common/interfaces.py common/config.py services/rca/adapters/explanation_provider.py services/rca/rank.py services/rca/consumer.py services/rca/app.py services/rca/tests/test_consumer.py services/rca/tests/test_explanation_provider.py services/rca/tests/test_rank.py
git commit -m "feat(rca): reliability-weighted ranking + on-by-default LLM explanation (template fallback)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Benchmark harness (scenario generator + runner + CI-enforced improvement)

**Files:**
- Create: `services/correlation/benchmark/__init__.py`, `services/correlation/benchmark/scenarios.py`, `services/correlation/benchmark/runner.py`
- Create: `scripts/benchmark.py` + `scripts/benchmark.sh`
- Test: `services/correlation/tests/test_benchmark.py` (new, fast, CI-enforced)

**Interfaces:** Consumes the three correlators + `TelemetryEvent`. Produces labeled scenarios + a metrics table.

- [ ] **Step 1: Seeded scenario generator (write the scenario test first)**

`scenarios.py`: a seeded generator (fixed seed, no `random` without seed — deterministic) producing labeled `TelemetryEvent` streams: normal-noise (all normal), seasonal-cycle (all normal — where z false-positives), point-anomaly (labeled spikes), sustained-anomaly (labeled level shift), correlation-break (two metrics; one diverges). Each yields `(events, labels)` with ground-truth per-event anomaly labels. Test: the generator is deterministic (same seed → identical streams) and labels line up.

- [ ] **Step 2: Runner — precision/recall/FPR/latency per correlator**

`runner.py`: run a correlator over a scenario, threshold at its `_z_threshold` (or `is_anomaly`), compare to ground truth, compute precision/recall/false-positive-rate/detection-latency. Return a dict per (correlator, scenario).

- [ ] **Step 3: CI-enforced improvement test**

`test_benchmark.py` (fast — small scenario sizes, deterministic): assert the new detector BEATS the baseline on a documented metric — e.g. `robust` FPR on the seasonal scenario < `river` FPR; (if trained is fittable in-test cheaply) `trained` recall on correlation-break ≥ `river` recall. Keep it fast and deterministic so CI enforces the win.

- [ ] **Step 4: CLI runner**

`scripts/benchmark.py` (runs all correlators × all scenarios, prints a markdown table) + `scripts/benchmark.sh` (thin `uv run python scripts/benchmark.py` wrapper).

- [ ] **Step 5: Run + commit**

Run: `uv run pytest services/correlation/tests/test_benchmark.py -q && uv run ruff check services/correlation/benchmark/ scripts/`

```bash
git add services/correlation/benchmark/ scripts/benchmark.py scripts/benchmark.sh services/correlation/tests/test_benchmark.py
git commit -m "feat(correlation): benchmark harness — labeled scenarios + CI-enforced detector gains

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Generate real numbers → docs + ADR-019

**Files:**
- Create: `docs/BENCHMARKS.md`
- Modify: `architectural.md` (ADR-019), `flow.md`, `README.md`, `docs/OPERATIONS.md`

**Interfaces:** None (docs). **The numbers in BENCHMARKS.md come from actually running `scripts/benchmark.py` — not asserted.**

- [ ] **Step 1: Run the harness, capture real numbers**

Run: `uv run python scripts/benchmark.py` — capture the actual table output.

- [ ] **Step 2: `docs/BENCHMARKS.md`**

Methodology (the 5 scenarios, the 4 metrics, one-command re-run), the **real results table** (from Step 1), and an honest reading of where each detector wins/loses and its costs (trained needs a fit; multivariate/correlation-break assumptions; seasonal needs per-bucket warmup). Numbers, not claims.

- [ ] **Step 3: ADR-019 + flow/README/OPERATIONS**

`architectural.md` — **ADR-019 — Pluggable detectors, the finetuning loop, and LLM-assisted RCA** (verify next number is 019; match existing ADR heading/structure): `CORRELATOR_KIND`, robust/seasonal + trained rationale, the persisted-model fit/retrain loop (with the honest note that fit is triggered by `POST /retrain`, not automatic), sklearn-on-training-path-only + lazy import, on-by-default LLM explanation (config-selected, advisory-only — never touches ranking/runbook), and honest limits (synthetic benchmark; robust seasonal state is in-process for v1). `flow.md` — detection section describes the pluggable detector + retrain + LLM explanation. `README.md` — Stream B shipped; ADR count → 19. `docs/OPERATIONS.md` — `CORRELATOR_KIND` + correlator tuning + `LLM_EXPLANATION_*` in the env table.

- [ ] **Step 4: Commit**

```bash
git add docs/BENCHMARKS.md architectural.md flow.md README.md docs/OPERATIONS.md
git commit -m "docs(stream-b): BENCHMARKS.md (real numbers), ADR-019, flow/README/OPERATIONS

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-review notes (author)

- **Spec coverage:** all 4 acceptance criteria map — detector-selectable-config-default-off (T2 switch, river default), measured-benchmark (T5 CI-enforced + T6 real numbers), richer-RCA-with-real-runbook (T4 reliability ranking, top still resolves to a playbook id), retrain-demonstrably-learns (T3 fit/persist + `POST /retrain` trigger + T3 tests).
- **Verified corrections baked in:** `BaseCorrelator` ABC (engine coupling); numpy-window robust (NOT river estimators) + `_warmup_samples` exact name + robust needs its own state (can't reuse `correlation_baseline`); trained all-logic-in-`detect()` + renamed model methods + real fit trigger (retrain-at-boot has empty deque) + prove-`uv add`-resolves gate; LLM patch-the-3-existing-test-calls + additive field + sync httpx + template fallback.
- **YAGNI / risk control:** robust durable seasonal store deferred to in-process for v1 (T2 Step 5 ruling) so Stream B isn't blocked on a second migration; `0003` migration owned solely by T3 (trained). The `uv add` resolution is a hard STOP-and-report gate (T3 Step 1) so a bad dependency resolution can't silently corrupt the tree.
- **Type consistency:** every correlator subclasses `BaseCorrelator` and honors `_z_threshold`/`_warmup_samples` + the 4 abstract methods; `rank_hypotheses`'s new param is optional (backward-compatible); `explanation` field is additive-optional.
