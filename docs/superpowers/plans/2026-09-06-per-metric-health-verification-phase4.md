# Per-Metric Health Verification (Metrics Phase 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verify a remediation worked on the metric(s) that actually fired, not always `cpu_usage`, by re-applying Phase 2's `DetectionPolicy` at verify time.

**Architecture:** A new pure helper `services/action/verify.py` builds a `metric_healthy()` predicate from a Situation's firing metrics + baseline and a config-built `DetectionPolicy`; a metric is "recovered" when it is no longer anomalous by the same rule that detected it. `KubernetesHealthChecker` calls this helper inside `.check()` (where the Situation is in scope), replacing the hardcoded `cpu_usage < 50` query. Both the live k8s path (`app.py`) and the sandbox pre-flight (`sandbox.py`) use it. Fail-safe throughout: any un-verifiable metric → not-recovered → rollback (ADR-007).

**Tech Stack:** Python 3.12, pydantic contracts, pytest, uv, ruff. Reuses `services/correlation/detection_policy.py` (DetectionPolicy, classify) and `Situation.member_events` / `Situation.baseline` (`common/contracts.py`).

**Spec:** docs/superpowers/specs/2026-09-06-per-metric-health-verification-phase4-design.md

## Global Constraints

- **Fail-safe → rollback everywhere uncertain:** a query error/None, or a `default`-kind metric with no usable baseline (missing, or `std <= 0`), makes that metric NOT-recovered → predicate False → caller rolls back. The predicate NEVER raises out of `check()`.
- **All firing metrics must recover:** the predicate is healthy only when every metric event (kind metric / value not None) in the Situation passes its recovery test. Value-None events (log/trace) are skipped; a Situation with no judgeable metric events → predicate True (vacuous; pod-readiness is then the sole verdict).
- **Recovery = `not policy.is_anomaly(event, score, z_threshold)`** using the SAME DetectionPolicy config as correlation. `score = (current - mean)/std` from `situation.baseline[name]` when present with `std > 0`, else `0.0`. Only `classify(name) == "default"` with no baseline fail-safes to not-recovered; ratio/saturation/latency each have an absolute component (latency's `score=0.0` naturally reduces `is_anomaly` to its ceiling test).
- **No new config; no contract changes.** Reuse `detection_policy`, `detection_ratio_threshold`, `detection_saturation_ratio_threshold`, `detection_saturation_percent_threshold`, `detection_latency_ceiling_ms`, `correlation_z_threshold`, `prometheus_url`.
- **Slim-boundary:** `services/action/verify.py` imports ONLY `common.contracts` and `services.correlation.detection_policy` — NO httpx, NO kubernetes. The action service's heavy imports (httpx in app.py, kubernetes in k8s_health/sandbox) stay lazy exactly as today. CI slim-guard must stay green.
- **Off-is-not-always-identical is INTENDED** (per spec): the cpu verification threshold moves from the old hardcoded `< 50` to the policy cutoff when `detection_policy=on`; non-cpu metrics change from cpu-based to right-metric-based. Only the `health_check_mode == "k8s"` path changes; `always` (dry-run → AlwaysHealthyChecker) is untouched. Update any existing test that assumed the cpu check.
- Every commit ends with the trailer exactly: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Gate command (run from the worktree): `uv run pytest -m "not postgres and not kafka" -q` then `uv run ruff check .` and `uv run ruff format --check .`. Baseline before Task 1 is recorded by the executor.

---

### Task 1: The pure `build_metric_healthy` helper

**Files:**
- Create: `services/action/verify.py`
- Test: `services/action/tests/test_verify.py`

**Interfaces:**
- Consumes: `Situation`, `TelemetryEvent`, `TelemetryKind` (`common/contracts.py`); `DetectionPolicy`, `classify` (`services/correlation/detection_policy.py`).
- Produces: `build_metric_healthy(situation: Situation, query_value: Callable[[str], float | None], policy: DetectionPolicy, z_threshold: float = 3.0) -> Callable[[], bool]`.

- [ ] **Step 1: Write the failing tests**

```python
from datetime import UTC, datetime

from common.contracts import Situation, SituationStatus, TelemetryEvent, TelemetryKind
from services.action.verify import build_metric_healthy
from services.correlation.detection_policy import DetectionPolicy

NOW = datetime(2026, 9, 6, tzinfo=UTC)


def _sit(metrics, baseline=None):
    """metrics: list of (name, value) that fired. baseline: {name: {mean, std}}."""
    events = [
        TelemetryEvent(
            source="test", kind=TelemetryKind.METRIC, name=n, value=v, ts=NOW,
            fingerprint=f"fp-{n}",
        )
        for n, v in metrics
    ]
    return Situation(
        id="sit-x", status=SituationStatus.ACTING, member_events=events,
        severity="high", first_seen=NOW, last_seen=NOW, signature="sig-x",
        baseline=baseline,
    )


ON = DetectionPolicy(enabled=True)  # policy thresholds: ratio 0.02, sat% 90, sat_ratio 0.80, latency 500


def test_ratio_recovered_is_healthy():
    # error_rate now 0.005 < 0.02 threshold -> recovered -> healthy. No baseline needed.
    sit = _sit([("error_rate", 0.005)])
    healthy = build_metric_healthy(sit, lambda name: 0.005, ON)
    assert healthy() is True


def test_ratio_still_high_is_not_healthy():
    sit = _sit([("error_rate", 0.09)])
    healthy = build_metric_healthy(sit, lambda name: 0.09, ON)
    assert healthy() is False


def test_saturation_cpu_recovered_by_policy_cutoff():
    # cpu_usage 40 < 90 (percent-scale saturation cutoff) -> recovered. Note: the OLD
    # hardcoded check was <50; the policy cutoff is 90 -> intended shift.
    sit = _sit([("cpu_usage", 40.0)])
    healthy = build_metric_healthy(sit, lambda name: 40.0, ON)
    assert healthy() is True


def test_latency_recovered_via_ceiling_without_baseline():
    # latency 300ms < 500 ceiling, no baseline -> ceiling-only judges it recovered
    # (NOT fail-safed, because latency has an absolute rule).
    sit = _sit([("latency_p99_ms", 300.0)], baseline=None)
    healthy = build_metric_healthy(sit, lambda name: 300.0, ON)
    assert healthy() is True


def test_latency_breaching_ceiling_is_not_healthy():
    sit = _sit([("latency_p99_ms", 700.0)], baseline=None)
    healthy = build_metric_healthy(sit, lambda name: 700.0, ON)
    assert healthy() is False


def test_default_metric_recovered_via_zscore_with_baseline():
    # memory_usage_mb is 'default' kind (pure z-score). current 210, baseline mean 200 std 20
    # -> z = 0.5 < 3.0 -> not anomalous -> recovered.
    sit = _sit([("memory_usage_mb", 210.0)], baseline={"memory_usage_mb": {"mean": 200.0, "std": 20.0}})
    healthy = build_metric_healthy(sit, lambda name: 210.0, ON, z_threshold=3.0)
    assert healthy() is True


def test_default_metric_still_high_via_zscore():
    # current 300, mean 200 std 20 -> z = 5.0 > 3.0 -> still anomalous -> not recovered.
    sit = _sit([("memory_usage_mb", 300.0)], baseline={"memory_usage_mb": {"mean": 200.0, "std": 20.0}})
    healthy = build_metric_healthy(sit, lambda name: 300.0, ON, z_threshold=3.0)
    assert healthy() is False


def test_default_metric_missing_baseline_fails_safe():
    # memory_usage_mb (default kind) with NO baseline -> cannot prove recovery -> not healthy.
    sit = _sit([("memory_usage_mb", 210.0)], baseline=None)
    healthy = build_metric_healthy(sit, lambda name: 210.0, ON)
    assert healthy() is False


def test_default_metric_zero_std_baseline_fails_safe():
    sit = _sit([("memory_usage_mb", 210.0)], baseline={"memory_usage_mb": {"mean": 200.0, "std": 0.0}})
    healthy = build_metric_healthy(sit, lambda name: 210.0, ON)
    assert healthy() is False


def test_all_firing_metrics_must_recover():
    # dependency_outage: error_rate recovered (0.005) BUT latency still high (700) -> NOT healthy.
    sit = _sit([("error_rate", 0.005), ("latency_p99_ms", 700.0)])
    values = {"error_rate": 0.005, "latency_p99_ms": 700.0}
    healthy = build_metric_healthy(sit, lambda name: values.get(name), ON)
    assert healthy() is False


def test_all_recovered_is_healthy():
    sit = _sit([("error_rate", 0.005), ("latency_p99_ms", 300.0)])
    values = {"error_rate": 0.005, "latency_p99_ms": 300.0}
    healthy = build_metric_healthy(sit, lambda name: values.get(name), ON)
    assert healthy() is True


def test_query_returns_none_fails_safe():
    sit = _sit([("cpu_usage", 40.0)])
    healthy = build_metric_healthy(sit, lambda name: None, ON)
    assert healthy() is False


def test_query_raises_fails_safe():
    def boom(name):
        raise RuntimeError("prometheus down")

    sit = _sit([("cpu_usage", 40.0)])
    healthy = build_metric_healthy(sit, boom, ON)
    assert healthy() is False  # never raises


def test_no_metric_events_is_vacuously_healthy():
    # a situation whose only events are value-None (log/trace) -> nothing to verify -> True.
    ev = TelemetryEvent(source="t", kind=TelemetryKind.LOG, name="app.error", value=None, ts=NOW, fingerprint="fp-l")
    sit = Situation(id="s", status=SituationStatus.ACTING, member_events=[ev], severity="high",
                    first_seen=NOW, last_seen=NOW, signature="sg", baseline=None)
    healthy = build_metric_healthy(sit, lambda name: None, ON)
    assert healthy() is True


def test_policy_off_needs_baseline_for_every_metric():
    # detection_policy off -> pure z-score for all. cpu with no baseline -> fail-safe not-recovered.
    off = DetectionPolicy(enabled=False)
    sit = _sit([("cpu_usage", 40.0)], baseline=None)
    healthy = build_metric_healthy(sit, lambda name: 40.0, off, z_threshold=3.0)
    assert healthy() is False  # off-policy treats cpu as score-rule; no baseline -> not provable
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest services/action/tests/test_verify.py -v`
Expected: FAIL — `services.action.verify` does not exist.

- [ ] **Step 3: Implement `services/action/verify.py`**

```python
"""Per-metric recovery check for post-remediation verification.

A metric is 'recovered' when it is no longer anomalous by the SAME DetectionPolicy
rule that detected it (Phase 2). Pure: no Prometheus, no k8s — the current value
arrives via an injected `query_value` callable, so this is fully unit-testable.
Fail-safe: any query error/None, or a score-only ('default'-kind) metric with no
usable baseline, makes that metric not-recovered → the predicate returns False →
the caller rolls back (ADR-007). Never raises out of the returned predicate."""

from __future__ import annotations

from collections.abc import Callable

from common.contracts import Situation, TelemetryEvent, TelemetryKind
from services.correlation.detection_policy import DetectionPolicy, classify


def _baseline_score(situation: Situation, name: str, value: float) -> tuple[float, bool]:
    """Return (z_score, has_usable_baseline). z is 0.0 when no usable baseline."""
    b = (situation.baseline or {}).get(name)
    if not b:
        return 0.0, False
    mean = b.get("mean")
    std = b.get("std")
    if mean is None or std is None or std <= 0:
        return 0.0, False
    return (value - mean) / std, True


def build_metric_healthy(
    situation: Situation,
    query_value: Callable[[str], float | None],
    policy: DetectionPolicy,
    z_threshold: float = 3.0,
) -> Callable[[], bool]:
    # Firing metrics = metric-kind events that carry a value. Log/trace (value None) skipped.
    names = [
        e.name
        for e in situation.member_events
        if e.kind == TelemetryKind.METRIC and e.value is not None
    ]

    def _recovered(name: str) -> bool:
        try:
            current = query_value(name)
        except Exception:  # noqa: BLE001 — a failed query is 'not recovered'
            return False
        if current is None:
            return False
        score, has_baseline = _baseline_score(situation, name, current)
        # A 'default'-kind metric has no absolute rule; with no baseline its z is
        # untrustworthy and recovery is unprovable -> fail-safe not-recovered.
        if classify(name) == "default" and not has_baseline:
            return False
        try:
            probe = TelemetryEvent(
                source="verify",
                kind=TelemetryKind.METRIC,
                name=name,
                value=current,
                ts=situation.last_seen,
                fingerprint=f"verify-{name}",
            )
            anomalous = policy.is_anomaly(probe, score, z_threshold)
        except Exception:  # noqa: BLE001 — any policy error is 'not recovered'
            return False
        return not anomalous

    def metric_healthy() -> bool:
        # Vacuously healthy when there is nothing to verify (pod-readiness then decides).
        return all(_recovered(n) for n in names)

    return metric_healthy
```

- [ ] **Step 4: Run tests + lint**

Run: `uv run pytest services/action/tests/test_verify.py -v && uv run ruff check services/action/verify.py && uv run ruff format --check services/action/verify.py`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add services/action/verify.py services/action/tests/test_verify.py
git commit -m "feat(action): per-metric recovery check reusing the detection policy (pure, fail-safe)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Teach `KubernetesHealthChecker` to build the per-metric predicate from the Situation

**Files:**
- Modify: `services/action/adapters/k8s_health.py`
- Test: `services/action/tests/test_k8s_health.py`

**Interfaces:**
- Consumes: Task 1's `build_metric_healthy`.
- Produces: `KubernetesHealthChecker(apps_v1=None, metric_healthy=None, policy=None, query_value=None, z_threshold=3.0, timeout_seconds=30.0, poll_interval_seconds=2.0, exc_type=None)`. When `policy` and `query_value` are both provided, `.check()` builds the per-metric predicate from the Situation via `build_metric_healthy(situation, query_value, policy, z_threshold)` and uses it as the metric signal; otherwise it falls back to the injected `metric_healthy` (default `lambda: True`) exactly as today (back-compat for callers that pass a pre-built predicate or none).

Rationale: `.check(situation, target)` already receives the Situation, so the per-metric predicate can only be built here (it needs `situation.member_events`). The existing `metric_healthy` param stays for back-compat (the sandbox rollout-wait check and any test that injects a fixed predicate keep working).

- [ ] **Step 1: Write the failing tests**

Reuse the file's EXISTING helpers (confirmed present): `FakeApps(ready, desired=1, fail=False)` — its `read_namespaced_deployment_status(...).status` exposes `ready_replicas`/`replicas`; `_tgt()` → a `RemediationTarget`; `_sit()` → a Situation builder. Add a small memory-firing Situation builder alongside `_sit()` (a `member_events=[TelemetryEvent(name="memory_usage_mb", kind=METRIC, value=..., ...)]` with `baseline={"memory_usage_mb": {"mean": 200, "std": 20}}`, status ACTING). Do NOT invent `_ready_apps_v1`/`_acting_situation_memory` fixtures — use `FakeApps(ready=1)` and the new builder.

```python
from services.correlation.detection_policy import DetectionPolicy
# existing in file: FakeApps, _tgt, Situation/TelemetryEvent imports, KubernetesHealthChecker

def _sit_memory(value):
    # model on the existing _sit(); one memory_usage_mb metric event + baseline.
    ...

def test_check_builds_per_metric_predicate_from_situation():
    # memory_usage_mb=300, baseline mean 200 std 20 (z=5 -> still anomalous). policy on +
    # query_value returning 300 -> NOT recovered -> check() False even though pods ready.
    calls = []
    def query_value(name):
        calls.append(name)
        return 300.0
    checker = KubernetesHealthChecker(
        apps_v1=FakeApps(ready=1), policy=DetectionPolicy(enabled=True),
        query_value=query_value, z_threshold=3.0, timeout_seconds=0.1, poll_interval_seconds=0.01,
    )
    assert checker.check(_sit_memory(300.0), _tgt()) is False
    assert "memory_usage_mb" in calls  # queried the firing metric, NOT cpu_usage

def test_check_healthy_when_metric_recovered():
    # query returns 205 (z=0.25 < 3) -> recovered -> pods ready -> True.
    checker = KubernetesHealthChecker(
        apps_v1=FakeApps(ready=1), policy=DetectionPolicy(enabled=True),
        query_value=lambda name: 205.0, z_threshold=3.0, timeout_seconds=0.1, poll_interval_seconds=0.01,
    )
    assert checker.check(_sit_memory(205.0), _tgt()) is True

def test_back_compat_injected_metric_healthy_still_used():
    # no policy/query_value -> falls back to the injected metric_healthy (today's behavior).
    checker = KubernetesHealthChecker(
        apps_v1=FakeApps(ready=1), metric_healthy=lambda: False,
        timeout_seconds=0.1, poll_interval_seconds=0.01,
    )
    assert checker.check(_sit(), _tgt()) is False  # injected predicate honored
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest services/action/tests/test_k8s_health.py -v`
Expected: FAIL — `KubernetesHealthChecker` has no `policy`/`query_value` params; still queries the injected predicate.

- [ ] **Step 3: Modify `k8s_health.py`**

Add the params and build-from-situation logic. Keep the lazy k8s import and the `_pod_ready`/`_safe_metric` structure. Changes:
- `__init__` gains `policy=None, query_value=None, z_threshold: float = 3.0`; store them.
- In `check(self, situation, target)`, compute the metric predicate once at the top:
  ```python
  if self._policy is not None and self._query_value is not None:
      from services.action.verify import build_metric_healthy
      metric_healthy = build_metric_healthy(situation, self._query_value, self._policy, self._z_threshold)
  else:
      metric_healthy = self._metric_healthy  # back-compat (default lambda: True)
  ```
  then use `metric_healthy` where `self._metric_healthy` / `_safe_metric` was used. Wrap its call in the existing try/except → False guard (rename `_safe_metric` to take the predicate, or inline a local safe-call). The `import` of `build_metric_healthy` is module-level-safe (verify.py is pure) but do it lazily inside `check` to keep k8s_health import-light and consistent with the file's lazy-import discipline; verify.py has no heavy deps so a top-of-file import is also acceptable — pick one and be consistent. Prefer a top-of-file `from services.action.verify import build_metric_healthy` since verify.py is pure (no slim-boundary risk).
- Update the class docstring to note the two-signal check now verifies the FIRING metrics (per-metric recovery) when a policy+query_value are supplied, else the injected predicate.

- [ ] **Step 4: Run tests + lint**

Run: `uv run pytest services/action/tests/test_k8s_health.py services/action/tests/test_verify.py -v && uv run ruff check services/action/ && uv run ruff format --check services/action/`
Expected: green. Existing k8s_health tests still pass (back-compat path).

- [ ] **Step 5: Commit**

```bash
git add services/action/adapters/k8s_health.py services/action/tests/test_k8s_health.py
git commit -m "feat(action): KubernetesHealthChecker builds per-metric recovery from the Situation

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Wire the live path — `app.py` `_make_health_checker` builds policy + query_value

**Files:**
- Modify: `services/action/app.py`
- Test: `services/action/tests/test_health.py` (or `test_adapter_selection.py` — wherever `_make_health_checker` is unit-tested; if untested, add coverage in `test_health.py`)

**Interfaces:**
- Consumes: Task 2's `KubernetesHealthChecker(policy=, query_value=, z_threshold=)`.
- Produces: `_make_health_checker(settings)` returns a `KubernetesHealthChecker` configured with a config-built `DetectionPolicy` and a `query_value` closure over `settings.prometheus_url` that instant-queries a metric BY NAME (not the hardcoded `cpu_usage`).

- [ ] **Step 1: Write the failing test**

```python
# test_health.py — add. Verify the built checker carries a policy + a query_value that
# queries by metric name, and that the policy is built from settings.

def test_make_health_checker_k8s_builds_per_metric(monkeypatch):
    from services.action.app import _make_health_checker
    from services.action.adapters.k8s_health import KubernetesHealthChecker

    class S:
        health_check_mode = "k8s"
        detection_policy = "on"
        detection_ratio_threshold = 0.02
        detection_saturation_ratio_threshold = 0.80
        detection_saturation_percent_threshold = 90.0
        detection_latency_ceiling_ms = 500.0
        correlation_z_threshold = 3.0
        prometheus_url = "http://prom:9090"

    checker = _make_health_checker(S())
    assert isinstance(checker, KubernetesHealthChecker)
    assert checker._policy is not None and checker._policy._enabled is True
    assert checker._query_value is not None
    assert checker._z_threshold == 3.0


def test_make_health_checker_always_is_always_healthy():
    from services.action.app import _make_health_checker
    from services.action.adapters.health import AlwaysHealthyChecker

    class S:
        health_check_mode = "always"

    assert isinstance(_make_health_checker(S()), AlwaysHealthyChecker)
```

(If `_policy._enabled` private access is frowned on in this codebase, assert via a behavior probe instead — e.g. build a tiny Situation and call the predicate — but the private-attr check is acceptable for a construction test; match the file's existing testing style.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest services/action/tests/test_health.py -v`
Expected: FAIL — `_make_health_checker` still builds the hardcoded `cpu_usage`/`<50` predicate; no `policy`/`query_value`.

- [ ] **Step 3: Rewrite `_make_health_checker` in `app.py`**

```python
def _make_health_checker(settings):
    if settings.health_check_mode == "k8s":
        import httpx

        from services.correlation.detection_policy import DetectionPolicy

        policy = DetectionPolicy(
            enabled=(settings.detection_policy == "on"),
            thresholds={
                "ratio": settings.detection_ratio_threshold,
                "saturation_ratio": settings.detection_saturation_ratio_threshold,
                "saturation_percent": settings.detection_saturation_percent_threshold,
                "latency_ceiling_ms": settings.detection_latency_ceiling_ms,
            },
        )

        def query_value(name: str) -> float | None:
            # Instant-query the current value of the FIRING metric by name (not cpu).
            try:
                r = httpx.get(
                    f"{settings.prometheus_url}/api/v1/query",
                    params={"query": name},
                    timeout=5.0,
                )
                results = r.json().get("data", {}).get("result", [])
                if not results:
                    return None
                # Take the max across returned series — the worst instance decides recovery.
                return max(float(v["value"][1]) for v in results)
            except Exception:  # noqa: BLE001 — a failed query -> None -> metric not recovered
                return None

        return KubernetesHealthChecker(
            policy=policy,
            query_value=query_value,
            z_threshold=settings.correlation_z_threshold,
        )
    return AlwaysHealthyChecker()
```

Factor the policy construction into a small module-level helper `_make_detection_policy(settings) -> DetectionPolicy` in `app.py` (the `DetectionPolicy(enabled=..., thresholds={...})` block above), and call it from `_make_health_checker`. Task 4 reuses this SAME helper for the sandbox, so the thresholds dict lives in exactly one place. Notes: `max(...)` across series is the safe choice (the worst-behaving instance must be recovered). The old `all(v < 50 ...)` semantics is superseded by the policy (documented intended change). Keep the `httpx` import lazy inside the `k8s` branch (unchanged discipline). Update the inline comment (the old one said "re-queries ... error rate ... low means recovered" and hardcoded cpu — replace with the per-metric description).

- [ ] **Step 4: Run tests + full suite + lint + slim check**

Run: `uv run pytest services/action/tests/ -v && uv run pytest -m "not postgres and not kafka" -q && uv run ruff check . && uv run ruff format --check . && uv run python -c "import sys; import services.action.app; print('httpx' in sys.modules, 'kubernetes' in sys.modules)"`
Expected: green; slim check prints `False False` (httpx/kubernetes stay lazy — importing app.py must not pull them at module load).

- [ ] **Step 5: Commit**

```bash
git add services/action/app.py services/action/tests/test_health.py
git commit -m "feat(action): live health check verifies the firing metric(s), not always cpu

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Wire the sandbox pre-flight post-fix check

**Files:**
- Modify: `services/action/adapters/sandbox.py`
- Test: `services/action/tests/` (the sandbox test — likely `test_remediate.py` or a dedicated sandbox test; place with the existing sandbox coverage)

**Interfaces:**
- Consumes: Task 2's `KubernetesHealthChecker(policy=, query_value=, z_threshold=)`.
- Produces: the sandbox's POST-FIX clone health check (currently `KubernetesHealthChecker(apps_v1=..., timeout, poll)` with default metric predicate) now passes `policy` + a `query_value` that targets the clone's metric series, so the rehearsal verifies the firing metric recovered on the clone — not just pod-readiness.

- [ ] **Step 1: Write the failing test**

```python
# Add near the existing sandbox tests. Assert the post-fix health check is constructed
# with a policy + query_value (per-metric), not the default lambda:True. Because the
# sandbox is heavily k8s-mocked, the cleanest assertion is to monkeypatch/ spy the
# KubernetesHealthChecker constructor within sandbox.py and assert the post-fix call
# receives policy is not None and query_value is not None.

def test_sandbox_post_fix_check_is_per_metric(monkeypatch):
    import services.action.adapters.sandbox as sb

    seen = []
    real = sb.KubernetesHealthChecker

    class Spy(real):
        def __init__(self, *a, **k):
            seen.append(k)
            super().__init__(*a, **k)
        def check(self, situation, target):
            return True  # force a pass so the flow proceeds

    monkeypatch.setattr(sb, "KubernetesHealthChecker", Spy)
    # ... drive NamespaceCloneSandbox.rehearse(...) with the existing mocked apps_v1/
    #     core_v1 fakes and a Situation firing on a known metric ...
    # After the run, the LAST KubernetesHealthChecker construction (the post-fix check)
    # must carry per-metric args:
    post_fix = seen[-1]
    assert post_fix.get("policy") is not None
    assert post_fix.get("query_value") is not None
```

Reuse the sandbox test's existing harness (the mocked `apps_v1`/`core_v1`, the sample plan + Situation). If no sandbox test harness exists to drive `rehearse`, add a minimal one modeled on how `sandbox.py` is exercised elsewhere; keep k8s fully mocked (no real cluster).

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest services/action/tests/ -k sandbox -v`
Expected: FAIL — the post-fix `KubernetesHealthChecker` is built with the default metric predicate (no policy/query_value).

- [ ] **Step 3: Modify `sandbox.py`**

The sandbox seam (confirmed): `NamespaceCloneSandbox.__init__(self, namespace, prometheus_url=None)` — it does NOT currently receive detection config, and `_make_sandbox(settings)` in `app.py` constructs it with `NamespaceCloneSandbox(settings.k8s_namespace, prometheus_url=settings.prometheus_url)`. So:
- Extend `NamespaceCloneSandbox.__init__` to also accept `policy: DetectionPolicy | None = None` and `z_threshold: float = 3.0` (store them; default None keeps existing direct constructions — e.g. in tests — working as pod-readiness-only).
- Update `_make_sandbox(settings)` in `app.py` to build the SAME `DetectionPolicy` as `_make_health_checker` (factor a tiny local `_make_detection_policy(settings)` helper in app.py to avoid duplicating the thresholds dict — both Task 3 and Task 4 use it) and pass `policy=` + `z_threshold=settings.correlation_z_threshold` into `NamespaceCloneSandbox(...)`.

At the POST-FIX health check (the `health = KubernetesHealthChecker(...)` near the "PRIMARY pass signal" comment):
- When `self._policy is not None`, build a `query_value(name)` that instant-queries the clone's metric series via `self._prometheus_url` (lazy `import httpx`, same shape as app.py's query_value; target the clone namespace's series — see the honesty note below), and pass `policy=self._policy, query_value=query_value, z_threshold=self._z_threshold` into the post-fix `KubernetesHealthChecker(...)`. When `self._policy is None`, leave the post-fix check as today (pod-readiness only).
- Define a `query_value(name)` that instant-queries the clone's metric series. IMPORTANT (spec honesty): if the clone's metrics are NOT independently scrapable per-namespace in this setup, `query_value` will return None for the clone's series → the post-fix check fail-safes to not-recovered → the rehearsal fails closed. Do NOT fake a pass. In Step 4, VERIFY what the clone can actually query and document the real behavior in the report; if per-clone metrics aren't available, keep the per-metric wiring (it's correct and future-proof) and note that the rehearsal currently relies on pod-readiness + fail-safe metric behavior, to be revisited when per-clone scraping exists.
- Pass `policy=`, `query_value=`, `z_threshold=` into the post-fix `KubernetesHealthChecker(...)`. Leave the PRE-FIX rollout-wait `KubernetesHealthChecker(...)` as pod-readiness only (no policy) — it's waiting for the clone to come up, not verifying recovery.

- [ ] **Step 4: Run tests + full suite + lint + slim check**

Run: `uv run pytest services/action/tests/ -v && uv run pytest -m "not postgres and not kafka" -q && uv run ruff check . && uv run ruff format --check . && uv run python -c "import sys; import services.action.adapters.sandbox as s; print('kubernetes' in sys.modules)"`
Expected: green; slim check `False` (kubernetes stays lazy in sandbox.py). Document in the report what the clone `query_value` actually resolves in this setup (real series vs fail-safe None).

- [ ] **Step 5: Commit**

```bash
git add services/action/adapters/sandbox.py services/action/tests/
git commit -m "feat(action): sandbox pre-flight verifies the firing metric on the clone (fail-safe)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Docs — ADR-029 + flow/README/MERIDIAN/OPERATIONS; commit spec + plan

**Files:**
- Modify: `architectural.md` (ADR-029), `README.md` (ADR count 28→29), `flow.md` (§5.4 action-service), `docs/MERIDIAN.md`, `docs/OPERATIONS.md`
- Commit the spec + this plan onto the branch.

- [ ] **Step 1: `architectural.md` ADR-029**

Add `### ADR-029 — Per-metric health verification` after ADR-028, before `## 4. Cross-cutting concerns` (house Context → Decision → Why style; read ADR-027/028 as templates). Cover: the hardcoded `cpu_usage < 50` gap (verified the wrong metric → false-success → corrupted reliability/suppression); the decision to re-apply the Phase-2 DetectionPolicy at verify time (detect-and-verify symmetry); all-firing-metrics-must-recover; the fail-safe-to-rollback bias (query fail / no baseline for a default metric → not-recovered, per ADR-007); baseline sourced from the Situation's snapshot; both live + sandbox paths; the honest sandbox caveat (per-clone scraping); that it ships ON by default in the `health_check_mode=k8s` path as an intended correction (cpu threshold moves 50→policy cutoff). Cross-reference ADR-027 (detection policy it reuses), ADR-007 (rollback-on-unhealthy), ADR-023 (sandbox). Update README "twenty-eight" → "twenty-nine" (BOTH locations — grep to confirm; there are two).

- [ ] **Step 2: `flow.md` §5.4 + `docs/MERIDIAN.md` + `docs/OPERATIONS.md`**

- `flow.md` §5.4 (`action-service`): update the health/verify description — post-remediation verification now checks that the FIRING metric(s) recovered (no longer anomalous by the detection policy), all must recover, fail-safe → rollback; note it's the k8s health path.
- `docs/MERIDIAN.md`: note that a fault's recovery is verified on the metric(s) it moved (e.g. a memory_leak fix is verified on memory, an error fault on error_rate), not on cpu.
- `docs/OPERATIONS.md`: note that per-metric verification reuses the `DETECTION_*` + `correlation_z_threshold` settings (no new config); `health_check_mode=k8s` enables it; dry-run (`always`) is unaffected.

- [ ] **Step 3: Final gates**

Run: `uv run pytest -m "not postgres and not kafka" -q && uv run ruff check . && uv run ruff format --check .`
Expected: all green (docs don't change tests).

- [ ] **Step 4: Commit (incl. spec + plan)**

```bash
git add architectural.md flow.md README.md docs/MERIDIAN.md docs/OPERATIONS.md \
  docs/superpowers/specs/2026-09-06-per-metric-health-verification-phase4-design.md \
  docs/superpowers/plans/2026-09-06-per-metric-health-verification-phase4.md
git commit -m "docs(action): ADR-029 per-metric health verification; spec + plan

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review Notes (author)

- **Spec coverage:** AC1 (per-kind recovery) → Task 1 tests; AC2 (all recover) → Task 1 `test_all_firing_metrics_must_recover`; AC3 (missing baseline) → Task 1 `test_default_metric_missing_baseline_fails_safe` + zero-std; AC4 (query fail) → `test_query_returns_none/raises_fails_safe`; AC5 (no metric events) → `test_no_metric_events_is_vacuously_healthy`; AC6 (live wiring) → Task 3; AC7 (sandbox) → Task 4; AC8 (config reuse) → Task 3 `test_make_health_checker_k8s_builds_per_metric`; AC9 (gates+slim) → Task 3/4 slim checks; AC10 (docs) → Task 5.
- **Fail-safe proven at each step:** Task 1's query-None/raise/missing-baseline/zero-std tests; the predicate's two try/excepts; `KubernetesHealthChecker`'s existing `_safe_metric` guard is defense-in-depth on top.
- **Off-is-not-identical is intended and contained:** only `health_check_mode=k8s` changes; `always` (dry-run) untouched → the whole existing test suite that runs under dry-run is unaffected. The cpu 50→90 shift and non-cpu correction are documented (ADR-029) and any cpu-assuming test is updated.
- **Slim-boundary:** `verify.py` imports only pure modules; app.py/sandbox.py keep httpx/kubernetes lazy; each wiring task re-checks the slim import.
- **Type consistency:** `build_metric_healthy(situation, query_value, policy, z_threshold=3.0) -> Callable[[], bool]` (Task 1) consumed unchanged by Task 2's `KubernetesHealthChecker`; `query_value: Callable[[str], float | None]` throughout; `DetectionPolicy(enabled=, thresholds=)` built identically in app.py (Task 3) and sandbox.py (Task 4).
- **Known soft spot (flag for executor):** Task 4's sandbox `query_value` — whether the clone's metrics are per-namespace scrapable is environment-dependent. The plan requires the executor to VERIFY and document the real behavior, and to fail-safe (not fake a pass) if per-clone series don't resolve. This is the one task whose runtime behavior can't be fully asserted in a mocked unit test; its test asserts the WIRING (policy+query_value passed), and the report records the real query behavior.
