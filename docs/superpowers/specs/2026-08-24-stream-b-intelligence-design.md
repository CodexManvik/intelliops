# Stream B — Intelligence: Detection & RCA — Design Spec

**Date:** 2026-08-24
**Owner (this effort):** Manvik (integration lead, building Stream B on Member A's behalf; handed off via PR)
**Status:** design approved in brainstorming; key coupling anchors verified against the code; ready for an implementation plan.

## Goal

Make the "AIOps" genuinely intelligent and, crucially, **measure the improvement** against the current rule-based baseline: a stronger online detector, a real trainable model with a persisted fine-tune loop, evidence-driven RCA that uses learned reliability, and a reproducible benchmark showing measured gains.

## Non-goals

- No change to the `river` default behavior — `CORRELATOR_KIND=river` (default) is byte-unchanged; `pytest`/CI/compose unaffected.
- No breaking change to any `Correlator` Protocol method or the `rank_hypotheses` signature (additive/optional only).
- No live LLM dependency in tests — the LLM-explanation path is behind an interface, `Null` by default.
- The **sample production system** is a SEPARATE, later effort (explicitly deferred; this spec is Stream B only).
- No real ML infra in CI — sklearn is a normal dependency; the trained path is opt-in and the benchmark runs fast/deterministically.

## Global Constraints

- **Test-safe by default.** `CORRELATOR_KIND=river` and the existing config defaults must leave the full suite green with no new infra. New correlators are opt-in.
- **`uv run pytest -m "not postgres and not kafka"`** green; **`ruff check` + `ruff format --check .`** clean.
- **New dependency:** `scikit-learn` added to `pyproject.toml`. Its import is **lazy** (inside `TrainedCorrelator`), so services that don't select `trained` never import it, and `river`-default tests don't pay for it. `numpy`/`scipy` are already in the tree (transitive via river) — free to use.
- **Additive contracts only.** No change to `common/contracts.py` field meanings; the `Correlator` Protocol methods keep their signatures.
- **Commit trailer:** every commit ends with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Shared-file coordination:** `common/config.py` (new `CORRELATOR_KIND` + tuning fields — additive), `common/interfaces.py` (a new `ExplanationProvider` Protocol — additive), `common/contracts.py` (untouched). All additive; flagged in the PR.

---

## Critical coupling the design must respect (verified against the code)

The `Correlator` Protocol is `detect(event)->float`, `correlate(events)->Situation`, `retrain(data)->None`. But `CorrelationEngine` reaches into correlator **internals** beyond the Protocol — the new correlators MUST honor these or `reset()`/scoring/suppression break:

1. **`engine.py:52`** reads `correlator._z_threshold` (the score cutoff in `add()`).
2. **`engine.py:70`** calls `correlator._severity_band(score)->str`.
3. **`engine.py:75`** calls `correlator.should_suppress(signature, threshold)->bool` (from learned reliability).
4. **`engine.py:28-31`** `_correlator_factory = lambda: type(correlator)(z_threshold=..., warmup_samples=...)` — `reset()` reconstructs the correlator by calling its **class with `z_threshold` and `warmup_samples` kwargs**. Every new correlator's `__init__` MUST accept `z_threshold` and `warmup_samples` (even if it maps them onto its own concepts) so `reset()` works.
5. **`snapshot()->list[dict]` / `load(rows)`** — the engine persists/reloads the baseline through these (via `BaselineStore`). New correlators must implement both (may return `[]` / no-op if they carry no snapshotable scalar baseline, but then must persist their state another way — see the trained-model store).

**Design decision:** introduce a small **`BaseCorrelator`** (ABC or shared mixin) in `services/correlation/adapters/base.py` that defines the common surface the engine relies on (`_z_threshold`, `_warmup_samples`, `_severity_band`, `should_suppress`, `reliability`, `retrain` reliability-aggregation, `snapshot`/`load` defaults) so `RobustCorrelator` and `TrainedCorrelator` inherit the engine-facing contract and only override `detect()` (and, for trained, `retrain`/persistence). `RiverCorrelator` stays as-is (the default; optionally refactored to share the base, but only if it stays byte-behavior-identical — otherwise leave it untouched).

---

## Decision 1 — `CORRELATOR_KIND` switch + the three correlators

New config field (`common/config.py`, additive): `correlator_kind: str = "river"  # "river" | "robust" | "trained"`. Plus tuning fields with test-safe defaults (see each correlator).

A factory in `services/correlation/adapters/__init__.py` (or a `make_correlator(settings)` helper) selects the correlator, wired in `correlation/app.py` where `RiverCorrelator(...)` is currently constructed directly (`app.py:84`):

```python
def make_correlator(settings):
    kind = settings.correlator_kind
    if kind == "river":
        return RiverCorrelator(z_threshold=settings.correlation_z_threshold,
                               warmup_samples=settings.correlation_warmup_samples)
    if kind == "robust":
        return RobustCorrelator(z_threshold=settings.correlation_z_threshold,
                                warmup_samples=settings.correlation_warmup_samples,
                                seasonal_buckets=settings.correlation_seasonal_buckets)
    if kind == "trained":
        return TrainedCorrelator(z_threshold=settings.correlation_z_threshold,
                                 warmup_samples=settings.correlation_warmup_samples,
                                 seasonal_buckets=settings.correlation_seasonal_buckets,
                                 contamination=settings.correlation_contamination)
    raise ValueError(f"Unknown CORRELATOR_KIND: {kind!r}")
```

### `RiverCorrelator` (`kind=river`, default) — UNCHANGED
The baseline. Per-metric online z-score, warm-up gate. Stays the default so everything is byte-unchanged.

### `RobustCorrelator` (`kind=robust`) — the improved online detector
- **Robust z-score:** median + MAD (median absolute deviation) instead of mean/variance, using `river.stats` rolling quantiles / a bounded window. A single spike no longer inflates the baseline and desensitizes the detector (the z-score's known failure). Score = `|value - median| / (1.4826 * MAD)` (the 1.4826 makes MAD a consistent σ estimator for normal data); MAD==0 → score 0 (warm-up-like).
- **Seasonal baseline:** per-metric, `seasonal_buckets` (default 24) time-of-day buckets keyed off `event.ts.hour` (config: `correlation_seasonal_buckets: int = 24`). Score against the *matching bucket's* robust baseline, so normal daily peaks don't score as anomalies — the biggest false-positive source.
- Honors the engine contract: accepts `z_threshold`/`warmup_samples`; implements `_severity_band`, `should_suppress`, `reliability`, `retrain` (reliability aggregation — inherited from base), `snapshot`/`load` (persist per-bucket medians/MADs as scalars, same codec shape as the river baseline).

### `TrainedCorrelator` (`kind=trained`) — the finetuning story
- Wraps a `RobustCorrelator` for the live online path, PLUS a **scikit-learn `IsolationForest`** (lazy import). `detect()` combines the online robust score with the model's anomaly score (e.g. `max` or a weighted blend — the blend is a documented, benchmarked choice, default `max` so the model only *adds* sensitivity).
- **`retrain(training_data, normal_window=None)`:** aggregates per-signature reliability (as today) AND **fits the IsolationForest** on accumulated normal-behavior feature vectors (per-metric value + hour bucket + short rolling stats). `contamination` config: `correlation_contamination: float = 0.02`.
- **Persistence — the visible fine-tune loop:** the fitted model is serialized (joblib/pickle bytes) and stored via a new `ModelStore` (InMemory + Postgres, following the `BaselineStore` best-effort pattern — errors logged and swallowed, a missing model just means "use the online score only"). On boot the model is reloaded, so each retrain **improves on the last** and a restart keeps the trained model. A new `model_artifacts` table (Alembic migration) or a bytea column — additive migration, applied by the existing `migrate` compose service.
- **Graceful cold start:** no model yet → `detect()` uses the online robust score alone. So a fresh `trained` deployment works before any fit.

**Test-safety:** all three honor the engine's internal contract. `river` default unchanged. `robust`/`trained` opt-in. sklearn imported lazily inside `TrainedCorrelator` only.

---

## Decision 2 — Evidence-driven RCA + reliability feedback

### Reliability-weighted, evidence-scored ranking (`services/rca/rank.py`)

Today `rank_hypotheses(situation, context)` uses 3 rules with fixed confidences. Change (backward-compatible):

- Add an **optional** third param: `rank_hypotheses(situation, context, reliability_provider=None)`. When `None`, behavior is unchanged (all existing tests pass). When provided (a callable `signature -> float`), a hypothesis whose `suggested_runbook_id` has a proven track record for this situation's signature gets a confidence boost — so learned reliability feeds RCA. The top suggestion **still resolves to a real playbook id** (acceptance criterion preserved).
- Confidence becomes **evidence-driven**: base confidence + corroboration bonuses (deploy match, topology proximity, metric-signature match, historical reliability) rather than a magic constant — but bounded to [0,1] and deterministic. The existing rules' *relative* ordering must be preserved on the current test scenarios (so `test_rank.py` still passes, or is extended, not broken).

The reliability provider is threaded from the correlation side's learned reliability. **Verified seam:** the RCA service already calls `make_stores(settings)` (`rca/app.py:22`), so it has store access — it just doesn't pull `training_store` yet. RCA computes reliability from `stores.training_store.read_all()` (the same aggregation `RiverCorrelator.retrain` does: per-signature `worked`/total) and passes a `reliability_provider` callable into `rank_hypotheses`. No new cross-service RPC, no new dependency — just add `training_store` to what `rca/app.py` pulls from `make_stores` and thread the callable through `rca/consumer.py:30`. Read the store once at diagnosis (records are durable and demo-scale-small); if reads fail, fall back to `reliability_provider=None` (unchanged behavior) — best-effort, like the baseline reload.

### Richer context + the LLM-explanation interface

- Extend `ContextProvider` usage / add a `MetricContextProvider` that surfaces correlated-metric and topology-neighbor evidence, so hypotheses cite *why* (e.g. co-spiking dependency). Behind the existing `ContextProvider` Protocol — `NullContextProvider` stays the default/test double.
- **New `ExplanationProvider` Protocol** (`common/interfaces.py`, additive): `explain(hypothesis, context) -> str`. Default `NullExplanationProvider` returns the existing deterministic description (no behavior change, no dependency). An LLM-backed impl can slot in later behind the same interface — off by default, so tests need no API. This satisfies the workplan's "optionally an LLM-assisted explanation behind an interface" without a test-time dependency.

---

## Decision 3 — The benchmark harness (the PPO deliverable)

### Scenario generator (`services/correlation/benchmark/scenarios.py`)
A fixed, **seeded** generator producing labeled `TelemetryEvent` streams with ground-truth anomaly labels. `TelemetryEvent` = `{kind, name, value, labels, ts, fingerprint}`. Scenarios (deterministic, no external data):
1. **Normal noise** — Gaussian around a mean; ground truth: all normal.
2. **Seasonal cycle** — daily sinusoid + noise; ground truth: all normal (this is where the z-score baseline false-positives and robust/seasonal wins).
3. **Point anomaly** — a normal stream with injected spikes; ground truth labels the spikes.
4. **Sustained anomaly** — a level shift; ground truth labels the shifted region.
5. **Correlation break** — two metrics normally correlated; one diverges (only the multivariate/trained layer catches this).

### Runner (`scripts/benchmark.py` + `scripts/benchmark.sh`)
Runs each correlator (`river`, `robust`, `trained`) over the scenario set, computes **precision / recall / false-positive-rate / detection latency** vs ground truth, prints a table. Deterministic (fixed seed) → re-runnable to identical numbers.

### CI-enforced improvement (`services/correlation/tests/test_benchmark.py`)
A fast test asserting the new detector **beats the baseline on a documented metric** — e.g. `robust` FPR on the seasonal scenario < `river` FPR; `trained` recall on the correlation-break scenario > `river` recall. So the improvement is enforced, not just a doc that can rot. Keep it fast (small scenario sizes) and deterministic.

### `docs/BENCHMARKS.md`
Methodology (scenarios, metrics, one-command re-run), the **results table** (generated by actually running the harness — not asserted), and an honest reading of where each detector wins/loses and its costs (trained needs a fit; multivariate assumes a stable correlation structure). Numbers, not claims.

---

## Decision 4 — Docs + ADR

- **`docs/BENCHMARKS.md`** (above).
- **ADR-019 — Pluggable detectors + the finetuning loop** (next number is 019, verified — last is 018). Documents `CORRELATOR_KIND`, robust/seasonal + trained rationale, the persisted-model fine-tune loop, sklearn-on-training-path-only, and honest limits (synthetic benchmark; multivariate assumptions; the score-blend choice). Match the existing ADR heading/structure.
- **`flow.md` / `README.md`**: detection section describes the pluggable detector + retrain loop; Stream B marked shipped; ADR count → 19.
- **`docs/OPERATIONS.md`**: `CORRELATOR_KIND` + the new tuning fields added to the env-switch table.

---

## Acceptance criteria (from WORKPLAN Stream B)

1. **New detector selectable via config, defaults off.** `CORRELATOR_KIND=river` default; `pytest` green with the default.
2. **`docs/BENCHMARKS.md` shows a measured improvement** on a documented, re-runnable scenario — numbers, CI-enforced by `test_benchmark.py`.
3. **RCA produces richer, evidence-backed hypotheses;** the top suggestion still resolves to a real playbook id so the downstream action path is unaffected.
4. **The retrain path demonstrably updates the model from real outcomes** — a test + scripted demo showing the model learning a signature-plus-fix pair (reliability climbs AND the IsolationForest re-fits and persists).

## Suggested task ordering (for the plan)

1. `BaseCorrelator` (engine-facing contract) + `CORRELATOR_KIND` switch + `make_correlator` factory + config fields; wire `app.py`. (`river` default unchanged — all existing correlation tests green.)
2. `RobustCorrelator` (robust-z + seasonal) + `snapshot`/`load` + tests.
3. `TrainedCorrelator` (sklearn IsolationForest, lazy import) + `ModelStore` (InMemory + Postgres + migration) + retrain fit/persist/reload + tests.
4. RCA: optional `reliability_provider` in `rank_hypotheses` + evidence-driven scoring + reliability wired from training records in `consumer.py`; richer context + `ExplanationProvider` Protocol (Null default) + tests.
5. Benchmark: scenario generator + runner + `test_benchmark.py` (CI-enforced improvement).
6. Generate real numbers → `docs/BENCHMARKS.md`; ADR-019; flow/README/OPERATIONS updates.

Ordering rationale: the engine-facing base + switch land first (nothing else works without the seam); robust before trained (trained wraps robust); RCA is independent and can follow; the benchmark needs all correlators present; docs last (numbers come from the real harness run).
