# Detection policy per metric kind Implementation Plan (Metrics Phase 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a metric-kind-aware detection policy layered over the existing per-metric z-score, shared by all three correlators via `BaseCorrelator` and consulted by `CorrelationEngine`, so ratios/saturation fire on absolute thresholds (where a z-score misleads) and latency uses the seasonal statistical path. **Config-switched off by default** — the z-score behavior is byte-identical unless enabled.

**Architecture:** A pure `DetectionPolicy` (+ a `classify(name)->kind` function) in a new `services/correlation/detection_policy.py` decides anomaly per event by kind (`ratio`/`saturation`/`latency`/`default`). `BaseCorrelator` gains an optional `detection_policy` kwarg (default → disabled = today's behavior) and a policy-aware `is_anomaly`. `CorrelationEngine.add` swaps its inlined `score <= _z_threshold` check for the policy-aware decision (calling `detect()` exactly once, as it already does), and the engine's reset-factory carries the policy across `reset()`. A factory builds the policy from config.

**Tech Stack:** Python 3.11/3.12, pytest. Pure Python (name matching + comparisons) — no new deps.

**Spec:** `docs/superpowers/specs/2026-09-06-detection-policy-phase2-design.md` (read alongside). **Phase 2 of a 4-phase metrics arc** (P1 #38 merged; P3: RCA rules; P4: per-metric health).

## Global Constraints

- **Branch `feat/detection-policy-phase2` off current master** (Phase 1 #38 merged — the rich metric families exist).
- **Gates:** `uv run pytest -m "not postgres and not kafka"` green (~510 base + new tests); `ruff check .` + `ruff format --check .` clean.
- **Env:** `uv sync --extra ml --extra k8s` once.
- **SAFETY INVARIANT (the load-bearing property):** `detection_policy="off"` (the default) reproduces `detect() > z_threshold` EXACTLY — the ~510 base suite + the Phase-1 fault-scenario diagnoses are byte-identical. The policy ONLY changes the anomaly DECISION when enabled; it NEVER changes `detect()`'s returned score, the warm-up gate, the zero-std guard, RCA, or remediation. Tests must assert the off path is identical.
- **`detect()` called EXACTLY once per event** (it mutates the per-metric baseline). The engine already computes `score = detect(event)` once (engine.py:51); the refactor only swaps the `if score <= _z_threshold` comparison (line 52) for the policy-aware decision using that same `score` — do NOT introduce a second `detect()` call.
- **Reset-factory must carry the policy:** `CorrelationEngine._correlator_factory` (engine.py:28-31) reconstructs the correlator with only `z_threshold`/`warmup_samples`. It must ALSO pass `detection_policy=` so `reset()` doesn't silently drop the policy.
- **Slim-boundary holds:** the policy is pure Python in `services/correlation/` — no new heavy deps, no import into slim services. CI slim-boundary must stay green.
- **All three correlators, one policy:** the policy lives on `BaseCorrelator` so river/robust/trained behave consistently. The reset-factory contract `type(c)(z_threshold=, warmup_samples=, detection_policy=)` must hold for all three (extra kwargs need defaults).
- **Commit trailer** on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **git:** push; open a PR against master; the USER merges. Never merge to master.

---

## File Structure

- `services/correlation/detection_policy.py` (new) — `classify(name) -> kind` + `DetectionPolicy`.
- `services/correlation/adapters/base_correlator.py` — add `detection_policy` kwarg + policy-aware `is_anomaly`.
- `services/correlation/engine.py` — swap the anomaly check in `add`; carry the policy in `_correlator_factory`.
- `services/correlation/app.py` — build the `DetectionPolicy` from settings, pass into the correlator.
- `common/config.py` — `detection_policy` + threshold fields.
- Docs: `docs/OPERATIONS.md` (env table), `architectural.md` (ADR-027), maybe `docs/BENCHMARKS.md`/flow.md.
- Tests: `services/correlation/tests/` (existing dir — `test_detection_policy.py` new, + extend engine/correlator tests).

**PINNED classification patterns (first match wins, order matters):**
```python
_KIND_PATTERNS = [
    ("ratio",      ("error_rate", "error_ratio", "_ratio")),
    ("latency",    ("latency", "duration", "_ms")),
    ("saturation", ("saturation", "utilization", "disk_usage", "cpu_usage", "_percent")),
]  # no match -> "default"
```
(latency before saturation so `latency_p99_ms` matches latency, not a percent rule.)

**PINNED default thresholds (config-overridable):** `ratio` > 0.02; `saturation` 0..1 > 0.80; `saturation` percent (name ends `_percent` or == `cpu_usage`) > 90.0; `latency` ceiling 500.0 ms.

---

## Task 1: `DetectionPolicy` + `classify` (pure, standalone)

**Files:**
- Create: `services/correlation/detection_policy.py`
- Modify: `common/config.py` (add the config fields)
- Test: `services/correlation/tests/test_detection_policy.py` (new)

**Interfaces:**
- Produces:
  - `classify(metric_name: str) -> str` → `"ratio" | "saturation" | "latency" | "default"`.
  - `DetectionPolicy(enabled: bool, thresholds: dict | None = None)` with `is_anomaly(self, event, score: float, z_threshold: float) -> bool`.
  - `Settings.detection_policy: str = "off"` + `detection_ratio_threshold=0.02`, `detection_saturation_ratio_threshold=0.80`, `detection_saturation_percent_threshold=90.0`, `detection_latency_ceiling_ms=500.0`.

- [ ] **Step 1: Write the failing test**

Create `services/correlation/tests/test_detection_policy.py`. `event` only needs `.name` and `.value` — build a minimal `TelemetryEvent` (check `common/contracts.py` for required fields; reuse the correlation tests' event helper if one exists).

```python
from datetime import UTC, datetime
from common.contracts import TelemetryEvent, TelemetryKind
from services.correlation.detection_policy import DetectionPolicy, classify


def _ev(name, value):
    return TelemetryEvent(source="p", kind=TelemetryKind.METRIC, name=name, value=value,
                          labels={}, ts=datetime(2026, 9, 6, 12, tzinfo=UTC), fingerprint="fp")


def test_classify_kinds():
    assert classify("meridian_error_rate") == "ratio"
    assert classify("latency_p99_ms") == "latency"
    assert classify("cpu_usage") == "saturation"
    assert classify("saturation") == "saturation"
    assert classify("disk_usage_percent") == "saturation"
    assert classify("request_rate") == "default"
    assert classify("queue_depth") == "default"


def test_disabled_policy_is_pure_zscore():
    p = DetectionPolicy(enabled=False)
    # exactly detect() > z_threshold for every kind, value irrelevant
    assert p.is_anomaly(_ev("meridian_error_rate", 0.001), score=5.0, z_threshold=3.0) is True
    assert p.is_anomaly(_ev("meridian_error_rate", 0.99), score=1.0, z_threshold=3.0) is False
    assert p.is_anomaly(_ev("cpu_usage", 10.0), score=4.0, z_threshold=3.0) is True


def test_ratio_fires_on_absolute_even_with_low_zscore():
    p = DetectionPolicy(enabled=True)  # default thresholds
    # error rate 5% with a marginal z-score (noisy baseline) STILL fires
    assert p.is_anomaly(_ev("meridian_error_rate", 0.05), score=1.0, z_threshold=3.0) is True
    # tiny wiggle below 2% does NOT fire even with a high z-score
    assert p.is_anomaly(_ev("meridian_error_rate", 0.005), score=9.0, z_threshold=3.0) is False


def test_saturation_scale_handling():
    p = DetectionPolicy(enabled=True)
    assert p.is_anomaly(_ev("cpu_usage", 95.0), score=0.0, z_threshold=3.0) is True   # 0..100 > 90
    assert p.is_anomaly(_ev("cpu_usage", 40.0), score=9.0, z_threshold=3.0) is False
    assert p.is_anomaly(_ev("saturation", 0.9), score=0.0, z_threshold=3.0) is True    # 0..1 > 0.80
    assert p.is_anomaly(_ev("saturation", 0.2), score=9.0, z_threshold=3.0) is False
    assert p.is_anomaly(_ev("disk_usage_percent", 95.0), score=0.0, z_threshold=3.0) is True


def test_latency_statistical_or_ceiling():
    p = DetectionPolicy(enabled=True)
    assert p.is_anomaly(_ev("latency_p99_ms", 120.0), score=5.0, z_threshold=3.0) is True   # seasonal score fires
    assert p.is_anomaly(_ev("latency_p99_ms", 700.0), score=1.0, z_threshold=3.0) is True   # ceiling fallback
    assert p.is_anomaly(_ev("latency_p99_ms", 120.0), score=1.0, z_threshold=3.0) is False  # normal


def test_default_kind_unchanged():
    p = DetectionPolicy(enabled=True)
    assert p.is_anomaly(_ev("request_rate", 999.0), score=4.0, z_threshold=3.0) is True   # z-score
    assert p.is_anomaly(_ev("request_rate", 999.0), score=1.0, z_threshold=3.0) is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest services/correlation/tests/test_detection_policy.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `classify` + `DetectionPolicy`**

Create `services/correlation/detection_policy.py`:

```python
"""Metric-kind-aware anomaly decision, layered over the per-metric z-score.

A z-score is right for an unbounded utilization signal against a stable
baseline, but wrong for a bounded ratio (an error rate must fire on an
absolute level, not standard deviations) and for seasonal latency. This
policy classifies a metric by name and applies the right rule. `enabled=False`
reproduces the pure z-score decision (detect() > z_threshold) EXACTLY —
config-switched off is byte-identical to pre-policy behavior."""

from __future__ import annotations

from common.contracts import TelemetryEvent

_KIND_PATTERNS = [
    ("ratio", ("error_rate", "error_ratio", "_ratio")),
    ("latency", ("latency", "duration", "_ms")),
    ("saturation", ("saturation", "utilization", "disk_usage", "cpu_usage", "_percent")),
]

_DEFAULTS = {
    "ratio": 0.02,
    "saturation_ratio": 0.80,
    "saturation_percent": 90.0,
    "latency_ceiling_ms": 500.0,
}


def classify(metric_name: str) -> str:
    n = metric_name.lower()
    for kind, tokens in _KIND_PATTERNS:
        if any(tok in n for tok in tokens):
            return kind
    return "default"


def _is_percent_scale(name: str) -> bool:
    n = name.lower()
    return n.endswith("_percent") or n == "cpu_usage"


class DetectionPolicy:
    def __init__(self, enabled: bool, thresholds: dict | None = None) -> None:
        self._enabled = enabled
        self._t = {**_DEFAULTS, **(thresholds or {})}

    def is_anomaly(self, event: TelemetryEvent, score: float, z_threshold: float) -> bool:
        if not self._enabled:
            return score > z_threshold
        if event.value is None:
            return False
        kind = classify(event.name)
        if kind == "ratio":
            return event.value > self._t["ratio"]
        if kind == "saturation":
            cutoff = self._t["saturation_percent"] if _is_percent_scale(event.name) else self._t["saturation_ratio"]
            return event.value > cutoff
        if kind == "latency":
            return score > z_threshold or event.value > self._t["latency_ceiling_ms"]
        return score > z_threshold  # default
```

- [ ] **Step 4: Add the config fields**

In `common/config.py`:
```python
    detection_policy: str = "off"  # "off" | "on"
    detection_ratio_threshold: float = 0.02
    detection_saturation_ratio_threshold: float = 0.80
    detection_saturation_percent_threshold: float = 90.0
    detection_latency_ceiling_ms: float = 500.0
```

- [ ] **Step 5: Run tests + full suite + lint**

Run: `uv run pytest services/correlation/tests/test_detection_policy.py -v && uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check .`
Expected: new tests pass; base suite green (nothing uses the policy yet); lint clean.

- [ ] **Step 6: Commit**

```bash
git add services/correlation/detection_policy.py common/config.py services/correlation/tests/test_detection_policy.py
git commit -m "feat(detection): DetectionPolicy + metric-kind classify (pure, off-by-default)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Wire the policy into `BaseCorrelator`

**Files:**
- Modify: `services/correlation/adapters/base_correlator.py`
- Test: `services/correlation/tests/` (extend — a test that each correlator routes through the policy + reset works)

**Interfaces:**
- Consumes: `DetectionPolicy` (Task 1).
- Produces: `BaseCorrelator.__init__(z_threshold=3.0, warmup_samples=50, detection_policy: DetectionPolicy | None = None)` — stores `self._policy = detection_policy or DetectionPolicy(enabled=False)`. `is_anomaly(event)` becomes policy-aware; a `is_anomaly_scored(event, score)` helper lets the engine avoid a double `detect()`.

**RULING:** `detection_policy` is an OPTIONAL kwarg defaulting `None` → a disabled policy (today's behavior). This keeps the reset-factory contract (`type(c)(z_threshold=, warmup_samples=)`) working AND lets it pass `detection_policy=` (Task 3). All three correlators (`RiverCorrelator`/`RobustCorrelator`/`TrainedCorrelator`) inherit `BaseCorrelator.__init__` (or call `super().__init__` — verify each; if any overrides `__init__`, it must accept + forward `detection_policy`).

- [ ] **Step 1: Write the failing test**

Extend correlation tests (put in `test_detection_policy.py` or a correlator test). Verify: a disabled-policy correlator's `is_anomaly` == `detect() > z_threshold`; an enabled-policy correlator flags a ratio by absolute threshold; each of the 3 correlators constructs with `detection_policy=` and routes through it.

```python
from services.correlation.adapters.river_correlator import RiverCorrelator
from services.correlation.detection_policy import DetectionPolicy

def test_correlator_default_is_disabled_policy():
    c = RiverCorrelator()  # no policy -> disabled
    # warm it so detect() returns a real score, then is_anomaly == detect() > z_threshold
    # (reuse the existing correlator-test warm-up helper / jittered baseline pattern)
    ...

def test_correlator_with_enabled_policy_uses_thresholds():
    c = RiverCorrelator(detection_policy=DetectionPolicy(enabled=True))
    # an error_rate event above 0.02 is is_anomaly True regardless of z-score warm-up
    ...

def test_reset_factory_signature_accepts_policy():
    for Cls in (RiverCorrelator, RobustCorrelator, TrainedCorrelator):
        c = Cls(z_threshold=3.0, warmup_samples=50, detection_policy=DetectionPolicy(enabled=True))
        assert c._policy is not None
```

(Fill the `...` using the existing correlation tests' warm-up/jitter helpers — grep `services/correlation/tests/` for how they prime a baseline so `detect()` returns a meaningful score. Import `RobustCorrelator`/`TrainedCorrelator` from their modules.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest services/correlation/tests/ -k "policy or reset_factory" -v`
Expected: FAIL — `detection_policy` kwarg / `_policy` / policy-aware `is_anomaly` don't exist.

- [ ] **Step 3: Wire into `BaseCorrelator`**

In `base_correlator.py`:
- `__init__` signature: add `detection_policy: DetectionPolicy | None = None`; set `self._policy = detection_policy if detection_policy is not None else DetectionPolicy(enabled=False)`. Import `DetectionPolicy` (from `services.correlation.detection_policy`). (Watch for import cycles — `detection_policy.py` imports only `common.contracts`, so importing it into `base_correlator` is fine.)
- Replace `is_anomaly`:
  ```python
  def is_anomaly(self, event: TelemetryEvent) -> bool:
      return self.is_anomaly_scored(event, self.detect(event))

  def is_anomaly_scored(self, event: TelemetryEvent, score: float) -> bool:
      return self._policy.is_anomaly(event, score, self._z_threshold)
  ```
- Verify `RiverCorrelator`/`RobustCorrelator`/`TrainedCorrelator`: if any defines its own `__init__`, it must accept `detection_policy=None` and pass it to `super().__init__(...)`. (Check each — `RobustCorrelator` has extra seasonal kwargs; `TrainedCorrelator` composes a Robust. Both must forward `detection_policy`.)

- [ ] **Step 4: Run the tests**

Run: `uv run pytest services/correlation/tests/ -v`
Expected: PASS (new + all existing correlator tests).

- [ ] **Step 5: Full suite + lint**

Run: `uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check .`
Expected: green. (Existing correlator tests must pass — a default correlator has a disabled policy, so `is_anomaly`/detection is byte-identical.)

- [ ] **Step 6: Commit**

```bash
git add services/correlation/adapters/ services/correlation/tests/
git commit -m "feat(detection): BaseCorrelator carries a DetectionPolicy (default disabled); all 3 correlators forward it

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Wire the policy into `CorrelationEngine`

**Files:**
- Modify: `services/correlation/engine.py`
- Test: `services/correlation/tests/` (extend — spy on detect() count; policy changes buffering; off is identical)

**Interfaces:**
- Consumes: `BaseCorrelator.is_anomaly_scored` (Task 2).
- Produces: `CorrelationEngine.add` uses the policy-aware decision (one `detect()` call); `_correlator_factory` carries `detection_policy` across `reset()`.

**RULING (the once-only detect + reset carry — load-bearing):** `add` already computes `score = self._correlator.detect(event)` ONCE (engine.py:51). Change ONLY the next line: `if score <= self._correlator._z_threshold: return None` → `if not self._correlator.is_anomaly_scored(event, score): return None`. Do NOT add a second `detect()`. AND change `_correlator_factory` (engine.py:28-31) to also pass `detection_policy=correlator._policy` so `reset()` preserves the policy.

- [ ] **Step 1: Write the failing test**

Extend engine tests (`services/correlation/tests/` — grep for the existing engine test file, likely `test_engine.py`). Add:
- A spy correlator wrapping/counting `detect` calls → assert exactly ONE `detect` per `add`.
- With an enabled policy, an `error_rate` event above threshold (but with a warm-up-suppressed/low z-score) DOES accumulate in the buffer (would NOT have, pre-policy).
- With the default (disabled) policy, buffering is byte-identical to today (an existing engine test already covers this — confirm it still passes; add one asserting a sub-z event is NOT buffered under the disabled policy).
- After `reset()`, the new correlator still has the policy (`engine._correlator._policy` enabled).

```python
def test_detect_called_once_per_add():
    calls = {"n": 0}
    class _Spy(RiverCorrelator):
        def detect(self, event):
            calls["n"] += 1
            return super().detect(event)
    eng = CorrelationEngine(_Spy(), window_seconds=30.0)
    eng.add(_ev("cpu_usage", 95.0))
    assert calls["n"] == 1

def test_enabled_policy_buffers_subz_ratio():
    eng = CorrelationEngine(RiverCorrelator(detection_policy=DetectionPolicy(enabled=True)),
                            window_seconds=30.0)
    # feed an error_rate at 5% cold (z-score warm-up -> score 0), policy still flags it
    eng.add(_ev("meridian_error_rate", 0.05))
    eng.flush()  # should emit a Situation (buffer non-empty), whereas disabled would not
    # assert a Situation was produced / buffer had the event (match the engine test's assertion style)

def test_reset_preserves_policy():
    eng = CorrelationEngine(RiverCorrelator(detection_policy=DetectionPolicy(enabled=True)))
    eng.reset()
    assert eng._correlator._policy._enabled is True
```

(Adapt to the engine test file's actual helpers/assertion style — grep it first.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest services/correlation/tests/ -k "detect_called_once or enabled_policy or reset_preserves" -v`
Expected: FAIL — engine still inlines the z-threshold check + reset drops the policy.

- [ ] **Step 3: Refactor `engine.py`**

- `add` (engine.py:51-53): keep `score = self._correlator.detect(event)`; change line 52 to `if not self._correlator.is_anomaly_scored(event, score): return None`. Everything else in `add` unchanged (still uses `score` for `_max_score`).
- `_correlator_factory` (engine.py:28-31): add `detection_policy=correlator._policy` to the `type(correlator)(...)` call.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest services/correlation/tests/ -v`
Expected: PASS (new + all existing engine/correlator tests — the disabled-policy default keeps existing engine behavior identical).

- [ ] **Step 5: Full suite + lint + slim-boundary sanity**

Run: `uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check . && uv run python -c "import sys; import services.action.app; print('numpy' in sys.modules)"`
Expected: green; the last check prints `False` (the policy didn't leak a heavy dep into a slim service — it's pure Python in correlation, and action doesn't import it).

- [ ] **Step 6: Commit**

```bash
git add services/correlation/engine.py services/correlation/tests/
git commit -m "feat(detection): engine consults the DetectionPolicy (one detect() per add); reset carries it

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Factory + config wiring (build the policy from settings)

**Files:**
- Modify: `services/correlation/app.py` (where the correlator is constructed — build the `DetectionPolicy` from settings, pass it in)
- Test: `services/correlation/tests/` (a factory test: off→disabled, on→enabled with config thresholds)

**Interfaces:**
- Consumes: `DetectionPolicy` (Task 1), the settings fields (Task 1).
- Produces: the live correlator is built with a `DetectionPolicy` reflecting `settings.detection_policy` + the threshold fields; default off.

- [ ] **Step 1: Find the correlator construction site + write the failing test**

Grep `services/correlation/app.py` (and `common/stores.py` / wherever `make_correlator` lives) for how the correlator + engine are built (`CORRELATOR_KIND`, `make_correlator`, `CorrelationEngine(...)`). Add a factory helper `_make_detection_policy(settings) -> DetectionPolicy` and thread it into the correlator construction. Test it: `detection_policy="off"` → `enabled False`; `"on"` → `enabled True` with the config thresholds populated.

```python
def test_make_detection_policy_off_by_default(monkeypatch):
    # default settings -> disabled
    from services.correlation.app import _make_detection_policy  # or wherever it lands
    from common.config import get_settings
    get_settings.cache_clear()
    p = _make_detection_policy(get_settings())
    assert p._enabled is False

def test_make_detection_policy_on(monkeypatch):
    monkeypatch.setenv("INTELLIOPS_DETECTION_POLICY", "on")
    monkeypatch.setenv("INTELLIOPS_DETECTION_RATIO_THRESHOLD", "0.05")
    get_settings.cache_clear()
    p = _make_detection_policy(get_settings())
    assert p._enabled is True and p._t["ratio"] == 0.05
    get_settings.cache_clear()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest services/correlation/tests/ -k detection_policy_o -v`
Expected: FAIL — the factory doesn't exist.

- [ ] **Step 3: Implement the factory + thread it in**

Add `_make_detection_policy(settings)` building `DetectionPolicy(enabled=(settings.detection_policy == "on"), thresholds={"ratio": settings.detection_ratio_threshold, "saturation_ratio": settings.detection_saturation_ratio_threshold, "saturation_percent": settings.detection_saturation_percent_threshold, "latency_ceiling_ms": settings.detection_latency_ceiling_ms})`. Pass its result as `detection_policy=` into the correlator construction (wherever `make_correlator`/the correlator is built for the engine). Confirm the engine's reset-factory still carries it (Task 3 handles the carry via `correlator._policy`).

- [ ] **Step 4: Run + full suite + lint**

Run: `uv run pytest services/correlation/tests/ -v && uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check .`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add services/correlation/app.py services/correlation/tests/
git commit -m "feat(detection): build DetectionPolicy from config; wire into the live correlator (off by default)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Docs + final gates

**Files:**
- Modify: `docs/OPERATIONS.md` (env-switch rows), `architectural.md` (ADR-027), `flow.md` (a note on the detection decision)
- Commit the spec + this plan (untracked) onto the branch.

- [ ] **Step 1: OPERATIONS.md env rows**

Add rows for `INTELLIOPS_DETECTION_POLICY` (off/on, default off — metric-kind-aware anomaly decision layered over the z-score) and the four threshold vars (`INTELLIOPS_DETECTION_RATIO_THRESHOLD`, `_SATURATION_RATIO_THRESHOLD`, `_SATURATION_PERCENT_THRESHOLD`, `_LATENCY_CEILING_MS`), matching the table's existing format. Note the seasonal-latency-needs-`CORRELATOR_KIND=robust|trained` caveat.

- [ ] **Step 2: architectural.md ADR-027**

Add `### ADR-027 — Detection policy per metric kind` in the established Context/Decision/Why style (after ADR-026, before the `## 4. Cross-cutting concerns` section — the same insertion pattern the prior ADRs used). Cover: the z-score-is-wrong-for-ratios/latency problem; the name-pattern classification; absolute thresholds for ratio/saturation; latency via the seasonal statistical path (robust/trained) + river ceiling fallback; off-by-default + shared via BaseCorrelator; the honest limit (true seasonality needs robust/trained). Update the README ADR count references (twenty-six → twenty-seven) if the README states a count.

- [ ] **Step 3: flow.md note**

In flow.md's correlation section (§5.2), add a line that the anomaly decision is now policy-aware (kind-based thresholds when `DETECTION_POLICY=on`, else the z-score) — brief; ADR-027 carries the detail.

- [ ] **Step 4: Final gates**

Run: `uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check .`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add docs/OPERATIONS.md architectural.md flow.md README.md docs/superpowers/specs/2026-09-06-detection-policy-phase2-design.md docs/superpowers/plans/2026-09-06-detection-policy-phase2.md
git commit -m "docs(detection): ADR-027 + OPERATIONS env rows for the detection policy; spec + plan

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review Notes (author)

- **Spec coverage:** §1 policy+classify → Task 1; §3 BaseCorrelator → Task 2; §3 engine → Task 3; §4 config+factory → Task 1 (config) + Task 4 (factory); docs (ADR-027) → Task 5. Acceptance criteria 1-8 mapped.
- **Off-is-byte-identical proven at each step:** Task 1's `test_disabled_policy_is_pure_zscore`; Task 2's default-disabled correlator; Task 3's disabled-policy engine buffering identical + existing engine tests unchanged.
- **detect()-called-once:** Task 3's `test_detect_called_once_per_add` (spy) + the refactor only swaps the comparison, not the detect call (engine already computes score once at :51).
- **Reset carries the policy:** Task 3's `test_reset_preserves_policy` + the `_correlator_factory` change.
- **All 3 correlators:** Task 2's `test_reset_factory_signature_accepts_policy` loops river/robust/trained; the ruling flags that any correlator overriding `__init__` must forward `detection_policy`.
- **Type consistency:** `is_anomaly(event, score, z_threshold)` on DetectionPolicy; `is_anomaly_scored(event, score)` on BaseCorrelator; `classify(name)->str`. Consistent across tasks.
- **Known soft spot (flag for the executor):** the correlator construction site (Task 4) isn't pinned — the executor greps `correlation/app.py` + `common/stores.py` for `make_correlator`/`CorrelationEngine(...)` and threads the policy in there. If `make_correlator` lives in `common/stores.py` (shared), keep the policy build in `correlation/app.py` and pass it down, OR add the param to `make_correlator` with a default — whichever keeps the slim-boundary (don't make `common/stores.py` import correlation-only code eagerly).
