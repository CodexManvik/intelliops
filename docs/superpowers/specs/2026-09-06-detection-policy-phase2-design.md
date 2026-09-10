# Detection policy per metric kind — Design Spec (Metrics Phase 2)

**Date:** 2026-09-06
**Owner:** Manvik
**Status:** design (architectural — adds a metric-kind-aware detection policy layered over the existing per-metric z-score, shared across all three correlators via `BaseCorrelator`, consulted by the engine's anomaly decision, config-switched off by default). **Phase 2 of a 4-phase metrics arc** (P1: rich metrics + typed faults ✅ #38 · **P2: detection policy** · P3: RCA metric→runbook rules · P4: per-metric health verification).

**Depends on:** Phase 1 (#38, merged) — the rich USE+RED metric surface gives Phase 2 the metric families to apply policy to. Branch off master.

## The problem

Detection today is **one z-score threshold for every metric**: `CorrelationEngine.add` flags an event when `correlator.detect(event) > _z_threshold` (default 3.0). A z-score is the right tool for an unbounded utilization-ish signal against a stable baseline — but it is *wrong* for some metric kinds Phase 1 introduced:

- **Ratios / error rates** (`meridian_error_rate`): a jump from 0.1% to 5% is a real incident, but if the baseline error rate has been noisy the standard deviation is large and the z-score may not clear the threshold — the incident is *missed*. Conversely a tiny absolute wiggle on a near-zero baseline can score a huge z and *false-fire*. What matters for a ratio is an **absolute** level, not standard deviations.
- **Latency** (`latency_p99_ms`): latency is **seasonal** — normal p99 at peak hours differs from off-hours. A z-score against a 24h-flat mean over-fires at peak and under-fires off-peak.

So Phase 2 adds a **detection policy per metric kind**: classify each metric by name into a kind, and apply the right rule — absolute thresholds for ratio/saturation kinds, the seasonal statistical path for latency, and the unchanged z-score for everything else.

## Goal

A `DetectionPolicy` that decides, per event, whether it is anomalous — overriding the pure z-score where it misleads — **shared by all three correlators** (river / robust / trained) via `BaseCorrelator`, and consulted by `CorrelationEngine` in place of its inlined `score <= _z_threshold` check. **Config-switched off by default** (`DETECTION_POLICY=off` → today's z-score behavior exactly, base suite + Phase-1 diagnoses byte-identical). No RCA / health / remediation change (those are P3/P4).

## Key decisions (locked with the user)

1. **Name-pattern classification.** A built-in metric-name→kind map (config-overridable) classifies each metric:
   - **`ratio`** — names matching error/ratio (e.g. `*error_rate*`, `*_ratio`): absolute threshold.
   - **`saturation`** — names matching saturation/utilization/percent (e.g. `saturation`, `cpu_usage`, `disk_usage_percent`, `*_percent`): absolute threshold.
   - **`latency`** — names matching latency/duration (e.g. `latency_*`, `*_ms`, `*_duration_*`): seasonal statistical path.
   - **`default`** — everything else: the unchanged z-score.
   (Chosen over an explicit exhaustive per-metric table, which is more setup, and over Prometheus-metadata inference, which is unreliable on synthetic metrics and reduces to name-patterns anyway.)
2. **Absolute thresholds decide for ratio/saturation kinds.** For a threshold-kind metric, an **absolute cutoff** decides anomaly, not the z-score (a z-score simply doesn't apply to a bounded ratio). Config-defined per kind with sensible defaults: `ratio` fires above e.g. `0.02` (2%), `saturation` fires above e.g. `0.80` for 0..1-scaled and a percent cutoff (e.g. `90`) for `*_percent` names. (`cpu_usage` is a 0..100 percent-style gauge, so the saturation kind must handle both 0..1 and 0..100 scales — see Design.) The z-score is NOT also required for these kinds (the "absolute replaces z-score for those kinds" flavor) — cleanest semantics; a sub-threshold statistical blip on a ratio is not an incident.
3. **Latency uses the seasonal statistical path.** `RobustCorrelator.detect()` ALREADY computes a per-(metric, hour-bucket) median/MAD z-score — so for `robust`/`trained` correlators, latency detection through the normal statistical path is already seasonal-aware, judged against the same hour's baseline. The policy routes latency to the correlator's statistical score (its `detect()`), compared to `z_threshold` — for `robust`/`trained` this is seasonal for free; for `river` (flat mean/var, not seasonal) latency additionally gets an **absolute p99 ceiling fallback** (fire if the statistical path OR the ceiling trips) so a `river`-mode deployment still catches a latency incident. Document that true seasonality requires `CORRELATOR_KIND=robust|trained`.
4. **Off by default, shared, additive.** `DetectionPolicy` defaults to a `NullDetectionPolicy` (or an "off" mode) that reproduces `detect() > z_threshold` exactly. It lives on `BaseCorrelator` so all three correlators share it; the engine consults a policy-aware method (`is_anomaly`) instead of inlining the threshold. Existing behavior is byte-identical when off.

## Non-goals / constraints

- **No change to the z-score math or the warm-up gate.** `detect()` still returns the same score; the policy only changes the **anomaly DECISION** (and, for threshold kinds, uses the event value directly rather than the score). The warm-up gate and zero-std guard in `detect()` are untouched.
- **No RCA / health / remediation change** (Phases 3/4). The policy changes *what counts as an anomaly*, which flows into which metrics enter a Situation — but the RCA rules, the runbook mapping, and health verification are unchanged. A Phase-1 fault scenario should still produce the same Situation/diagnosis when the policy is *off*; when *on*, the policy may catch incidents the z-score missed (e.g. a small error-rate rise) — which is the point, and is exercised by tests, not left implicit.
- **Preserve the Phase-1 cross-metric invariant's downstream effect.** Phase 1 ensured `error`/`dependency_outage` faults keep cpu flat so RCA maps them to restart-pod, not scale-service. Phase 2's policy must not accidentally re-introduce a cpu anomaly for those (it doesn't — cpu isn't moved by those faults; the policy only decides on the metrics that *are* moved). But: the `ratio` absolute threshold means an `error` fault now fires on `meridian_error_rate` via the threshold even if its z-score was marginal — good, and it still doesn't touch cpu. Tests confirm the diagnosis is unchanged/sharpened, never inverted.
- **Config-switched, test-safe.** `DETECTION_POLICY` defaults off; the ~510 base suite + CI byte-identical on the default path. The engine's reset-factory contract (`type(c)(z_threshold=, warmup_samples=)`) must still hold — any new correlator/base kwargs get defaults.
- **All three correlators, one policy.** The policy lives in `BaseCorrelator`, so `river`/`robust`/`trained` behave consistently. No per-correlator policy divergence except the documented latency-seasonality difference (which follows from each correlator's own `detect()`).
- **Slim-boundary holds.** No new heavy deps; the policy is pure Python (name matching + comparisons) in `common`/`correlation`. `numpy` stays where it is (robust/trained only).

## Global Constraints

- **Gates:** `uv run pytest -m "not postgres and not kafka"` green (~510 base + new tests); `ruff check .` + `ruff format --check .` clean; `npm --prefix frontend run build` clean (only if UI touched — likely not).
- **Slim-boundary CI green:** the policy must not pull heavy deps into slim services. It lives in correlation (already ml-target) or `common` as pure Python.
- **Env:** `uv sync --extra ml --extra k8s` at setup.
- **Safety invariant:** `DETECTION_POLICY=off` (default) reproduces `detect() > z_threshold` EXACTLY — the base suite + Phase-1 fault diagnoses are byte-identical. The policy only ever changes the anomaly decision when enabled; it never changes `detect()`'s returned score, the warm-up gate, RCA, or remediation.
- **Commit trailer** on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **git:** branch `feat/detection-policy-phase2` off master. PR; user merges. Never merge to master.
- **Shared files:** `common/config.py`, `common/interfaces.py` (maybe — a `DetectionPolicy` protocol), `services/correlation/adapters/base_correlator.py` (the shared home), `services/correlation/engine.py` (the anomaly-decision call site), possibly a new `services/correlation/detection_policy.py`, and the correlation tests.

---

## Design

### 1. Metric-kind classification (`services/correlation/detection_policy.py`, new)

A pure function `classify(metric_name: str) -> str` returning `"ratio" | "saturation" | "latency" | "default"`, using an ordered name-pattern map (first match wins). Built-in patterns (config can extend/override):
```python
_KIND_PATTERNS = [
    ("ratio", ("error_rate", "_ratio", "error_ratio")),
    ("latency", ("latency", "_ms", "duration")),
    ("saturation", ("saturation", "_percent", "cpu_usage", "utilization", "disk_usage")),
    # else -> "default"
]
```
(Order matters: `latency` before `saturation` so `latency_p99_ms` doesn't match a stray percent rule, etc. The final pinned patterns are in the plan.)

### 2. `DetectionPolicy` (`services/correlation/detection_policy.py`)

```python
class DetectionPolicy:
    """Decides anomaly per event by metric kind. `enabled=False` reproduces the
    pure z-score decision (detect() > z_threshold) exactly."""
    def __init__(self, enabled: bool, thresholds: dict | None = None): ...
    def is_anomaly(self, event, score: float, z_threshold: float) -> bool: ...
```
- `enabled=False`: `return score > z_threshold` (identical to today).
- `enabled=True`, by kind:
  - `ratio`: `return event.value > thresholds["ratio"]` (default 0.02). (z-score ignored.)
  - `saturation`: normalize scale — if the name ends `_percent` or is `cpu_usage` (0..100), compare to `thresholds["saturation_percent"]` (default 90); if 0..1-scaled (`saturation`), compare to `thresholds["saturation_ratio"]` (default 0.80). `return value > cutoff`.
  - `latency`: `anomalous = score > z_threshold` (the correlator's statistical path — seasonal for robust/trained) **OR** (for the river fallback) `event.value > thresholds["latency_ceiling_ms"]` (default e.g. 500). Since the policy can't tell which correlator it's in, the OR-with-ceiling is safe for all: robust/trained already catch it seasonally via `score`, and the ceiling is a high backstop.
  - `default`: `return score > z_threshold` (unchanged).
- Thresholds come from config (a dict with defaults); `None` uses the built-in defaults.

### 3. Wire into `BaseCorrelator` + the engine

- `BaseCorrelator.__init__` gains a `detection_policy: DetectionPolicy | None = None` (default `None` → a disabled policy, i.e. today's behavior). It's stored; `is_anomaly(event)` becomes: `score = self.detect(event); return self._policy.is_anomaly(event, score, self._z_threshold)`. (The existing `is_anomaly` already exists — this upgrades it to be policy-aware; with a disabled policy it's identical to `detect() > _z_threshold`.)
- **`CorrelationEngine.add`** currently inlines `score = detect(event); if score <= _z_threshold: <not anomaly>`. Change it to consult the correlator's policy-aware decision — BUT the engine also needs the raw `score` for `_max_score`/severity. So: compute `score = self._correlator.detect(event)` once, then `if not self._correlator._policy.is_anomaly(event, score, self._correlator._z_threshold): <skip>` (or a small `BaseCorrelator.is_anomaly_scored(event, score)` helper to avoid double-`detect()`). Keep using `score` for `_max_score` and `_severity_band`. **`detect()` must be called exactly once per event** (it mutates the baseline) — the refactor must not double-call it.
- The engine's reset-factory (`type(c)(z_threshold=, warmup_samples=)`) must still work — so `detection_policy` is an optional kwarg with a default, and reset either passes the policy through or reconstructs it from config. Pin this in the plan (the engine reads `correlator._z_threshold` etc.; it may need to also carry `_policy` across reset).

### 4. Config (`common/config.py`)

```python
    detection_policy: str = "off"        # "off" | "on"
    # thresholds (used only when detection_policy == "on"); sensible defaults:
    detection_ratio_threshold: float = 0.02
    detection_saturation_ratio_threshold: float = 0.80
    detection_saturation_percent_threshold: float = 90.0
    detection_latency_ceiling_ms: float = 500.0
```
A factory (`services/correlation/app.py` or where the correlator is built) constructs the `DetectionPolicy` from settings and passes it into the correlator. Default `off` → disabled policy.

### 5. No UI change (confirm)

Detection policy is server-side; the console shows the resulting Situations/anomalies the same way. Optionally the System view could show "detection policy: on/off" (like the LLM badge) — a nicety, not required. If cheap, add it; else defer.

---

## Acceptance criteria

1. **Off is byte-identical:** with `detection_policy="off"` (default), `is_anomaly` == `detect() > z_threshold` for every metric; the ~510 base suite passes unchanged; a Phase-1 fault scenario produces the same anomaly/Situation it did before. Unit test: a disabled policy returns `score > z_threshold` for all kinds.
2. **Ratio threshold catches a sub-z error rise:** unit test — with the policy on, an `error_rate` event whose value crosses the absolute threshold (e.g. 0.05) is flagged anomalous EVEN IF its z-score is below `z_threshold` (simulate a noisy baseline where the z-score wouldn't fire); and a tiny ratio wiggle below the threshold is NOT flagged even if its z-score is high.
3. **Saturation threshold + scale handling:** `cpu_usage` (0..100) fires above the percent cutoff; `saturation` (0..1) fires above the ratio cutoff; `disk_usage_percent` above the percent cutoff. Below → not anomalous. Unit tests per scale.
4. **Latency uses the statistical path + ceiling fallback:** unit test — a latency event with a high statistical `score` fires (seasonal path); a latency event with a low score but a value above the ceiling fires (river fallback); a normal latency value (low score, below ceiling) does not.
5. **`detect()` called exactly once per event:** the engine refactor must not double-call `detect()` (it mutates the baseline). Test/inspect: a spy correlator counts one `detect` call per `add`.
6. **All three correlators share the policy:** the policy lives on `BaseCorrelator`; a test constructs each of river/robust/trained with the policy and confirms the anomaly decision routes through it. The reset-factory (`type(c)(...)`) still works with the policy present.
7. **Config-switched:** `detection_policy` defaults off; the factory builds a disabled policy by default and an enabled one when `"on"`; thresholds come from config.
8. **Gates green + slim-boundary:** ~510 + new tests; ruff clean; slim-boundary CI green (no heavy dep leak); frontend build clean if touched.

## Suggested task ordering (for the plan)

1. **`DetectionPolicy` + `classify` (pure, standalone):** `services/correlation/detection_policy.py` — the classifier + the policy with `is_anomaly(event, score, z_threshold)`, disabled and enabled paths, all four kinds + scale handling + the latency ceiling. Config fields. Fully unit-tested in isolation (no correlator/engine needed — pass events + scores directly). This is the heart, and it's pure/deterministic. (AC 1-4, 7.)
2. **Wire into `BaseCorrelator`:** add the optional `detection_policy` kwarg (default → disabled), store it, make `is_anomaly` policy-aware without double-calling `detect()`. Unit test each correlator (river/robust/trained) routes through the policy + the reset-factory still constructs them. (AC 6.)
3. **Wire into `CorrelationEngine.add`:** replace the inlined `score <= _z_threshold` with the policy-aware decision, calling `detect()` exactly once, keeping `_max_score`/severity on the raw score. Unit test: a spy confirms one `detect` per `add`; an enabled policy changes which events accumulate; a disabled policy reproduces today's buffering exactly. (AC 5, 1.)
4. **Factory + config wiring:** build the `DetectionPolicy` from settings where the correlator is constructed (`correlation/app.py`), pass it in; default off. Confirm the full live path. (AC 7.)
5. **Docs (+ optional System-view badge):** `docs/BENCHMARKS.md` or a detection section — how the policy works (kinds, thresholds, the seasonal-latency-needs-robust note, off-by-default); update the OPERATIONS.md env table with the new vars; note in flow.md/architectural.md (an ADR-027 for the detection policy). Optionally the System-view "detection policy: on/off" badge. Commit spec + plan. Final gates.

Rationale: the pure policy first (fully testable alone, the heart), then BaseCorrelator (shared wiring), then the engine (the once-only detect() refactor — the riskiest integration), then the factory/config, then docs. The off-is-byte-identical invariant is provable at every step, and `detect()`-called-once is explicitly tested.
