# Per-Metric Health Verification (Metrics Phase 4) — Design

**Status:** approved (brainstorm 2026-09-06)
**Phase:** 4 of 4 in the metrics arc (P1 rich metrics + typed faults #38 · P2 detection policy #39 · P3 RCA metric→runbook rules + AI-computed confidence #40 · **P4 per-metric health verification**).

## Problem

Post-remediation health verification always asks Prometheus one hardcoded question — `cpu_usage < 50` — regardless of which metric actually fired the incident.

`services/action/app.py` `_make_health_checker` (the live k8s path) builds a `metric_healthy` predicate that queries `cpu_usage` and returns healthy when every returned series is `< 50`. `services/action/adapters/k8s_health.py` `KubernetesHealthChecker.check` requires BOTH pod-readiness AND `metric_healthy()` before declaring success; otherwise the caller rolls back (ADR-007). So the *metric* half of the verdict is always about cpu.

Consequences:
- A **memory-leak** incident is "verified fixed" by checking cpu. If the leak is unresolved but cpu was never high, the check passes trivially → a **false success**.
- An **error-rate spike**, a **latency** regression, a **db-pool exhaustion** — all verified against cpu, none against the metric that broke.
- The `worked` flag derived from this verdict feeds **reliability scoring** and **suppression** (a signature that "reliably self-heals" gets suppressed/auto-remediated). A false success here corrupts those downstream safety signals.

Phase 3 made RCA diagnose the *right* metric family. Phase 4 is its honest counterpart: **verify the metric(s) that actually fired have recovered**, not cpu-by-default.

Additionally, the **sandbox pre-flight rehearsal** (`services/action/adapters/sandbox.py`) currently verifies the clone on **pod-readiness only** — its `KubernetesHealthChecker` is constructed with `metric_healthy` left at the default `lambda: True` (see its inline comment "metric predicate left at default"). So the rehearsal never checks a metric at all. Phase 4 gives it the same per-metric recovery check as the live path.

## Approach: detect-and-verify symmetry

A metric is **recovered** when it is **no longer anomalous by the same `DetectionPolicy` rule that detected it** (Phase 2, `services/correlation/detection_policy.py`). One rule governs both "is this firing?" and "has this recovered?".

For a firing metric `m`:

```
recovered(m) = not policy.is_anomaly(
    TelemetryEvent(name=m, value=<current value re-queried from Prometheus>),
    score=<z-score computed from situation.baseline[m], when the kind needs it>,
    z_threshold,
)
```

`DetectionPolicy.is_anomaly(event, score, z_threshold)` classifies `m` by name and applies the kind's rule:
- **ratio** (`error_rate`, `_ratio`): `value > ratio_threshold` (absolute). **Ignores `score`** → needs only the re-queried current value; **no baseline required**.
- **saturation** (`cpu_usage`, `saturation`, `_percent`, `disk_usage`, `utilization`): `value > cutoff` (percent-scale → 90, ratio-scale → 0.80). Absolute. **No baseline required**.
- **latency** (`latency`, `duration`, `_ms`): `score > z_threshold OR value > latency_ceiling_ms`. The absolute ceiling fires with just the value; the z-score half needs a baseline.
- **default** (everything else, e.g. `memory_usage_mb`, `queue_depth`, `db_pool_in_use`, `request_rate`): pure `score > z_threshold`. **Needs a baseline.**

So the metric families split cleanly:
- **Absolute-rule metrics** (ratio, saturation) verify from the current value alone.
- **Score-rule metrics** (default; latency's z-half) need a z-score, computed from the **baseline the Situation already carries** (`Situation.baseline: dict | None`, per-metric `{mean, std}` captured at detection time). `z = (value - mean) / std` (guard `std <= 0`).

### Missing baseline → fail-safe not-recovered (for metrics with no absolute rule)

The rule turns on whether the metric's kind has an **absolute** component:
- **ratio / saturation** — purely absolute; never need a baseline. Always judged by the current value.
- **latency** — has an absolute ceiling (`value > latency_ceiling_ms`). With a baseline, the z-half additionally applies (`score > z_threshold OR value > ceiling`). **Without** a baseline, latency is judged by the **ceiling alone** — it is NOT fail-safed, because the ceiling is a real, sufficient recovery test. (Skipping the z-half can only make the test *more* lenient — it can miss a still-elevated-but-under-ceiling latency — which is acceptable: latency below its absolute ceiling is a defensible "recovered", and we prefer a real check to a spurious rollback.)
- **default** (memory_usage_mb, queue_depth, db_pool_in_use, request_rate, …) — has **only** the z-score rule, no absolute component. With **no usable baseline** (older Situation, metric absent from the snapshot, or `std <= 0`), recovery **cannot be proven at all** → `recovered(m) = False` → the predicate returns False → the caller rolls back.

This matches the existing ADR-007 bias ("unknown → not-yet-healthy → roll back"): **never declare a success we cannot verify.** Only the case with *no* usable test (a `default`-kind metric lacking a baseline) fail-safes; a metric that still has an absolute test uses it.

### All firing metrics must recover

The predicate is healthy only when **every** metric in `situation.member_events` that carries a value passes `recovered(m)`. A partial fix — error rate down but latency still breaching — is **not** healthy. This keeps the `worked` signal (→ reliability, → suppression) honest: we only claim success when everything that broke is back to normal.

Events with `value is None` (e.g. pure log/trace events) are skipped — they have no metric to verify. If, after skipping, there are **no metric events to judge at all**, the predicate returns True (the metric half is vacuously satisfied) and pod-readiness remains the effective verdict — this is the one case that preserves today's behavior for a metric-less Situation.

## New unit — `services/action/verify.py`

A pure, dependency-light helper, fully unit-testable without Prometheus or k8s:

```python
def build_metric_healthy(
    situation: Situation,
    query_value,          # Callable[[str], float | None] — current value for a metric name, or None
    policy: DetectionPolicy,
    z_threshold: float = 3.0,
) -> Callable[[], bool]:
    """Return a metric_healthy() predicate: True iff every firing metric in the
    situation has recovered (no longer anomalous by `policy`). Fail-safe: any
    query error/None, or a score-rule metric with no usable baseline, makes that
    metric not-recovered (predicate False). Metric events with value None are
    skipped; a situation with no judgeable metric events yields True."""
```

Internals:
- Collect the firing metric names from `situation.member_events` (kind is metric / value is not None).
- For each, `current = query_value(name)`; if `None` → not-recovered (fail-safe).
- Compute `score`: look up `situation.baseline.get(name)`; if present with `std > 0`, `score = (current - mean) / std` and `has_baseline = True`; else `score = 0.0` and `has_baseline = False`.
- **Fail-safe guard (only for the no-absolute-rule case):** if `classify(name) == "default"` AND `not has_baseline` → **not-recovered** immediately (a default-kind metric has no absolute test, so a z of 0 is untrustworthy and recovery is unprovable). ratio/saturation/latency never hit this guard — they each have an absolute component that decides from the current value.
- Otherwise `anomalous = policy.is_anomaly(TelemetryEvent(name=name, kind=METRIC, value=current, ts=..., source=..., fingerprint=...), score, z_threshold)` and `recovered = not anomalous`. (For latency with no baseline, `score = 0.0` means the z-half `0.0 > z_threshold` is False, so `is_anomaly` reduces to the ceiling test alone — exactly the intended "ceiling-only" behavior, no special-casing needed.)
- Wrap each metric's evaluation in try/except → exception ⇒ not-recovered. The returned predicate ANDs all recoveries; empty judgeable set ⇒ True.

The helper takes `query_value` as an injected callable, so tests pass a fake `{name: value}` lookup and fake baselines with zero I/O. Slim-boundary: `verify.py` imports only `common.contracts` and `services.correlation.detection_policy` (both pure) — **no httpx, no kubernetes**.

## Wiring

### Live path — `services/action/app.py` `_make_health_checker`
Replace the single-metric `metric_healthy` closure with one built via `build_metric_healthy`:
- Build a `DetectionPolicy` from the **same** settings correlation uses: `DetectionPolicy(enabled=(settings.detection_policy == "on"), thresholds={...from settings.detection_*})`.
- Define `query_value(name)` = the existing Prometheus instant-query, parameterized by metric name instead of the hardcoded `cpu_usage` (re-query `{query: name}`, take the latest returned series value, or `None`).
- `metric_healthy = build_metric_healthy(situation, query_value, policy, settings.correlation_z_threshold)`.
- `KubernetesHealthChecker(metric_healthy=metric_healthy)` — unchanged; it still ANDs metric recovery with pod-readiness.

The Situation is available where the checker runs (the action consumer already carries it into remediation — the checker's `.check(situation, target)` receives it, and `_make_health_checker` runs per-remediation where the Situation is in scope). *Plan note:* if `_make_health_checker` is currently built once at app-init without the Situation, the plan restructures it to build the predicate per-remediation (or passes the Situation through), so the predicate can read `situation.member_events`. This is the one structural change and the plan spells out the exact call path.

### Sandbox path — `services/action/adapters/sandbox.py`
The post-fix clone health check (currently `KubernetesHealthChecker(...)` with default metric predicate) gets the same per-metric `metric_healthy`, built from the **same** Situation and a `query_value` that targets the **sandbox namespace's** Prometheus series (the sandbox already knows `prometheus_url`; the clone emits the same metric names). The rollout-wait check (pre-fix) stays pod-readiness only — we're waiting for the clone to come up, not verifying recovery.

*Design note:* if the clone's metrics are not independently scrapable per-namespace in the demo/Meridian setup, the sandbox `query_value` may not resolve real per-clone series. In that case the sandbox check degrades **fail-safe** (query returns None → not-recovered → rehearsal fails closed), which is the safe direction for a rehearsal gate. The plan verifies what the sandbox can actually query and documents the resulting behavior honestly; it must not fake a pass.

## Config

No new settings. Phase 4 reuses:
- `detection_policy` + `detection_ratio_threshold` / `detection_saturation_ratio_threshold` / `detection_saturation_percent_threshold` / `detection_latency_ceiling_ms` — the action service builds the identical policy correlation uses, so **detect and verify agree by construction**.
- `correlation_z_threshold` (3.0) — the z-threshold for score-rule recovery, the same number the correlation engine detects with.
- `prometheus_url` — already used by the health path.

When `detection_policy == "off"`, `is_anomaly` reduces to pure `score > z_threshold` for **every** metric → verification then needs a baseline for every firing metric, and any without one fail-safes to not-recovered. This is acceptable and honest: off-policy is the legacy z-only mode, and Phase 1's Situations do carry baselines for the metrics they fire on.

## Off-is-not-always-identical — an intended correction

This is a **behavior change**, and that is the point. Two shifts, both intended:
- **The cpu threshold moves from the old hardcoded `< 50` to the policy's saturation cutoff (90 percent-scale / 0.80 ratio-scale) when `detection_policy=on`.** The old `50` was an arbitrary literal, never policy-derived; verification now uses the **same** rule detection uses, so the number is whatever the policy says — this is the decisive resolution (no "preserve 50" option). When `detection_policy=off`, cpu (a saturation-named metric) is judged by pure z-score and needs a baseline like any score-rule metric.
- **Every non-cpu firing metric** changes from "cpu-based" to "the-right-metric-based."

We do **not** gate this behind a flag: a correctness fix to a safety signal ships on by default (within the existing `health_check_mode == "k8s"` path). The spec and ADR document it as an intended correction, and every existing action test that assumed the cpu check is updated to the per-metric behavior (or shown to be unaffected).

The `health_check_mode` gate is unchanged: `always` (dry-run) still uses `AlwaysHealthyChecker` (no metric check — nothing to verify against in dry-run), and only `health_check_mode == "k8s"` runs the per-metric verification. So the default dev/test posture is untouched; the change is live only in the real-cluster path, exactly like today's cpu check.

## Fail-safe summary (ADR-007 alignment)

Every uncertain path resolves to **not-recovered → the caller rolls back**:
- Prometheus query error or no series → the metric is not-recovered.
- A score-rule metric with no usable baseline → not-recovered.
- Any exception evaluating a metric → not-recovered.
- The predicate never raises out of `check()` (the existing `_safe_metric` in `KubernetesHealthChecker` already wraps `metric_healthy()` in try/except → False; `build_metric_healthy` adds its own internal guards as defense-in-depth).

We only ever declare success when every firing metric is provably back to normal.

## Acceptance criteria

1. **Per-metric recovery via the detection policy:** for a Situation firing on metric `m`, the health predicate is healthy iff `not policy.is_anomaly(current_value_of_m, z_from_baseline, z_threshold)`. Unit tests per kind: a recovered ratio (value below ratio_threshold) → healthy; a still-high ratio → not; saturation recovered/not; latency recovered via z AND via ceiling; a recovered default (z back under threshold) → healthy.
2. **All firing metrics must recover:** a multi-metric Situation (e.g. error + latency) is healthy only when BOTH recover; one still-anomalous → not healthy. Unit test.
3. **Missing baseline → fail-safe:** a score-rule metric (default, or latency needing its z-half) with no usable baseline in `situation.baseline` → not-recovered → predicate False. Unit test.
4. **Fail-safe on query failure:** `query_value` returning None or raising → that metric not-recovered → predicate False; the predicate never raises. Unit test with a raising query.
5. **No metric events to judge → vacuous True:** a Situation whose member_events are all value-None (log/trace) → predicate True (pod-readiness is then the sole verdict). Unit test.
6. **Live wiring:** `services/action/app.py` builds the per-metric predicate from the Situation's firing metrics + baseline and the config-built DetectionPolicy (not a hardcoded `cpu_usage` query). Test that the constructed predicate queries the firing metric name(s), not cpu.
7. **Sandbox wiring:** the sandbox pre-flight post-fix health check uses the same per-metric predicate (built from the same Situation), replacing the default `lambda: True`. Test the sandbox constructs it; document honestly what the clone can actually query and that un-queryable → fail-safe fail.
8. **Config reuse + agreement:** the action service's DetectionPolicy is built from the same `detection_*` + `correlation_z_threshold` settings as correlation; no new settings. Unit test the policy is built from config.
9. **Gates + slim-boundary:** full suite green; `services/action/verify.py` imports no httpx/kubernetes (pure); the action service's slim import stays clean (httpx/k8s remain lazy in app.py/sandbox.py as today).
10. **Intended correction documented:** ADR-029 records the detect-and-verify symmetry, the fail-safe-to-rollback bias, and that the non-cpu behavior change is an intended correction shipped on by default (in the k8s health path). README ADR count 28→29; flow.md §5.4 (action-service) updated; MERIDIAN/OPERATIONS note the per-metric verification.

## Files (indicative — the plan finalizes)

- **Create:** `services/action/verify.py` (the pure `build_metric_healthy` helper); `services/action/tests/test_verify.py`.
- **Modify:** `services/action/app.py` (`_make_health_checker` → per-metric predicate; per-remediation build path if needed); `services/action/adapters/sandbox.py` (post-fix health check gets the predicate).
- **Modify (tests):** `services/action/tests/test_health.py` / `test_k8s_health.py` / sandbox tests as needed for the wiring; any existing test asserting the cpu check updated to per-metric.
- **Docs:** `architectural.md` (ADR-029), `README.md` (28→29), `flow.md` (§5.4), `docs/MERIDIAN.md` + `docs/OPERATIONS.md` (per-metric verification note).

## Out of scope

- No change to detection itself (Phase 2), RCA (Phase 3), or the remediation actions (the closed `RemediationStep` Literal).
- No new metric families (Phase 1 set them).
- No change to the dry-run health path (`AlwaysHealthyChecker`).
- No new config surface.
- Not a general Prometheus client abstraction — `query_value` is a minimal injected callable; a broader metrics-client refactor is a separate concern.
