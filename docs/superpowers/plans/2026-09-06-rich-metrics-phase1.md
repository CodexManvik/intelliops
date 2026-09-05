# Rich metric surface + typed fault profiles Implementation Plan (Metrics Phase 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make demo_app + the 4 Meridian services emit a production-shaped **USE+RED** metric set, and generalize the fault model so each **named scenario perturbs a realistic cluster of those metrics over time** — so different incidents look genuinely different. Detection/correlation already consumes arbitrary metrics, so this phase produces the *signals* only (later phases add detection policy, RCA rules, per-metric health).

**Architecture:** `MeridianState` gains the full metric field set + a `sample(now)` method (with ramp support for gradual faults); `make_meridian_service` registers one bare `Gauge` per metric. `FaultSpec.type` becomes one of 8 named scenarios, each implemented in `apply` as a metric profile that moves only its cluster (preserving the load-bearing invariant that a pure-`error` fault keeps `cpu` at baseline). demo_app gets the same gauge set. The gateway ops-panel fold + query broaden to carry the new metrics; the compose ingestion selector widens; the ops UI presets expand. **Purely additive — no metric is renamed** (`cpu_usage`/`meridian_error_rate` stay exactly as they are).

**Tech Stack:** Python 3.11/3.12, FastAPI, `prometheus_client` (already a base dep), pytest; the Meridian ops UI (static assets served by the gateway).

**Spec:** `docs/superpowers/specs/2026-09-06-rich-metrics-phase1-design.md` (read alongside). **Phase 1 of a 4-phase metrics arc** (P2: detection policy · P3: RCA rules · P4: per-metric health).

## Global Constraints

- **Branch `feat/rich-metrics-phase1` off current master.**
- **Gates:** `uv run pytest -m "not postgres and not kafka"` green (current base + new tests); `ruff check .` + `ruff format --check .` clean; `npm --prefix frontend run build` clean.
- **Env:** `uv sync --extra ml --extra k8s` once.
- **PURELY ADDITIVE — no rename.** `cpu_usage` and `meridian_error_rate` keep their exact names and meaning; the change only ADDS gauges + generalizes fault profiles. Do NOT rename `meridian_error_rate` to `error_rate` (it is referenced in gateway/app.py, docker-compose.yml, deploy/k8s, and 3 test files — a rename would churn all of them and risk the live pipeline).
- **LOAD-BEARING cross-metric invariant (the single most important correctness property):** a scenario's profile moves ONLY the metrics that incident would realistically move. In particular an `error` / `dependency_outage` fault MUST keep `cpu` at baseline — else the z-score correlator sees two anomalies and RCA's `scale-service` (0.6) outranks `restart-pod` (0.5), misdiagnosing an error incident as capacity. This invariant is already documented in `services/meridian/common.py:11-15`; preserve it and apply the same discipline to every new scenario. Tests must assert it.
- **No correlator / RCA / health change** — those are Phases 2/3/4. Phase 1 must not special-case detection, add RCA rules, or touch health verification. A new-scenario incident diagnosing via the semantic selector or landing in the gap is EXPECTED for P1.
- **compose-smoke must stay green:** all 13 services build + boot + pass `/health`|`/ready`; the new gauges must render on `/metrics` and not break the gateway fold.
- **Slim-boundary holds:** no new heavy deps (`prometheus_client` is base). demo_app/Meridian stay slim-target.
- **Commit trailer** on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **git:** push; open a PR against master; the USER merges. Never merge to master.

---

## File Structure

- `services/meridian/common.py` — the heart: `MeridianState` (full field set + `sample(now)` + ramp), `FaultSpec` (8 scenarios), gauge registration in `make_meridian_service`, `/metrics` handler.
- `services/demo_app/app.py` — the same USE+RED gauge set + `/break`/`/fix` moving a representative cluster.
- `services/meridian/gateway/app.py` — generalize the ops-panel fold (carry all known metrics, not just cpu/error) + broaden its query.
- `deploy/docker-compose.yml` — broaden `INTELLIOPS_PROMETHEUS_QUERY` to select the new metrics.
- Meridian ops UI (find it — likely `services/meridian/ui/` static assets served by the gateway) — expand the fault presets to the 8 scenarios.
- `docs/MERIDIAN.md` — the metric set + scenario table + the cross-metric invariant.
- Tests: `services/meridian/tests/` (existing `test_metrics.py`, `test_services.py`, `test_gateway_metrics.py` — extend), + a demo_app test if one exists (else add).

**Metric set (PINNED — use these exact names):** `cpu_usage` (existing), `meridian_error_rate` (existing, Meridian only), `request_rate`, `latency_p50_ms`, `latency_p99_ms`, `memory_usage_mb`, `saturation`, `queue_depth`, `db_pool_in_use`, `db_pool_max`, `disk_usage_percent`. (demo_app uses `cpu_usage` + the same USE/RED names; for demo_app's error metric, it already has the `http_request_errors_total` Counter — keep it, and ADD a `demo_error_rate` gauge or reuse the same `request_rate`/latency/memory/etc. gauge names. demo_app is a separate process/registry, so its gauge names don't collide with Meridian's.)

**The 8 fault scenarios (PINNED profiles) — each moves ONLY these, rest stay baseline:**
| Scenario | Moves |
|---|---|
| `saturation` | cpu↑, saturation↑, queue_depth↑ |
| `latency` | latency_p50↑, latency_p99↑, queue_depth↑, cpu mild↑ |
| `error` | meridian_error_rate↑ — **cpu + latency stay baseline** |
| `memory_leak` | memory_usage_mb **ramps** over duration_seconds |
| `traffic_surge` | request_rate↑, cpu↑, saturation↑, queue_depth↑ |
| `dependency_outage` | meridian_error_rate↑, latency_p99↑ — **cpu stays baseline** |
| `db_exhaustion` | db_pool_in_use→db_pool_max, latency↑ |
| `crash` | unhealthy=True (readiness fails; detection-only) |

---

## Task 1: `MeridianState` full metric set + `sample(now)` + gauges

**Files:**
- Modify: `services/meridian/common.py` (`MeridianState.__init__`, add `sample(now)`, `clear()`; register the new gauges in `make_meridian_service`; update `/metrics`)
- Test: `services/meridian/tests/test_metrics.py` (extend)

**Interfaces:**
- Produces: `MeridianState` with fields `cpu, error_rate, latency_ms, latency_p50_ms, latency_p99_ms, request_rate, memory_usage_mb, saturation, queue_depth, db_pool_in_use, db_pool_max, disk_usage_percent, unhealthy` (+ ramp bookkeeping); `MeridianState.sample(now: float) -> None` (advances any active ramp, called by `/metrics` before reading gauges); healthy-baseline + broken constants per metric. `make_meridian_service` registers a bare `Gauge` per metric (against `effective_registry`).

**Note:** keep `cpu_usage` and `meridian_error_rate` gauges exactly as they are (names + registration). ADD the new gauges alongside. `sample(now)` is where a `memory_leak` ramp is advanced by elapsed time; for all non-ramp state it's a no-op read.

- [ ] **Step 1: Write the failing test**

Extend `services/meridian/tests/test_metrics.py` (it already tests `/metrics` — read it first for the fixture style, esp. how it passes a fresh `CollectorRegistry()` per service to avoid duplicate-timeseries). Add:

```python
def test_fresh_state_exposes_full_metric_set_at_baseline():
    from prometheus_client import CollectorRegistry
    from services.meridian.common import make_meridian_service
    app = make_meridian_service("meridian-test", registry=CollectorRegistry())
    client = TestClient(app)
    body = client.get("/metrics").text
    for name in ("cpu_usage", "meridian_error_rate", "request_rate", "latency_p50_ms",
                 "latency_p99_ms", "memory_usage_mb", "saturation", "queue_depth",
                 "db_pool_in_use", "db_pool_max", "disk_usage_percent"):
        assert name in body, f"missing metric: {name}"


def test_sample_advances_memory_leak_ramp():
    from services.meridian.common import MeridianState, FaultSpec
    st = MeridianState()
    st.apply(FaultSpec(type="memory_leak", magnitude=1.0, duration_seconds=100.0))
    st.sample(now=0.0)
    m0 = st.memory_usage_mb
    st.sample(now=50.0)
    m50 = st.memory_usage_mb
    assert m50 > m0  # the ramp climbed with elapsed time
```

(Match the file's actual `TestClient`/registry fixture pattern — if it uses a helper, reuse it.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest services/meridian/tests/test_metrics.py -v`
Expected: FAIL — the new gauges / `sample` don't exist.

- [ ] **Step 3: Extend `MeridianState` + add `sample(now)`**

Add the metric fields to `__init__` with healthy baselines (e.g. `request_rate=50.0`, `latency_p50_ms=20.0`, `latency_p99_ms=80.0`, `memory_usage_mb=256.0`, `saturation=0.1`, `queue_depth=2.0`, `db_pool_in_use=3.0`, `db_pool_max=20.0`, `disk_usage_percent=35.0` — pick plausible values, define as module constants like the existing `CPU_HEALTHY`). Add ramp bookkeeping (`self._ramp = None` — a dict `{metric, start_value, target_value, start_time, duration}` or similar). Add `sample(now: float)`: if a ramp is active, set the ramped metric to `start + (target-start) * min(1.0, (now-start_time)/duration)`. `clear()` resets every field to baseline and clears the ramp.

- [ ] **Step 4: Register the gauges + update `/metrics`**

In `make_meridian_service`, register a bare `Gauge` per new metric (against `effective_registry`, no `service` label — matching the existing `cpu_gauge`/`error_gauge`). In the `/metrics` handler, call `state.sample(time.monotonic())` (import `time`), then set every gauge from state. Keep the existing `cpu_gauge`/`error_gauge` set calls.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest services/meridian/tests/test_metrics.py -v`
Expected: PASS.

- [ ] **Step 6: Full suite + lint**

Run: `uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check .`
Expected: green (existing Meridian tests still pass — the new gauges are additive; only tests asserting the exact metric set change, and this task updated those).

- [ ] **Step 7: Commit**

```bash
git add services/meridian/common.py services/meridian/tests/test_metrics.py
git commit -m "feat(metrics): MeridianState full USE+RED metric set + sample() ramp support

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `FaultSpec` scenarios + metric profiles

**Files:**
- Modify: `services/meridian/common.py` (`FaultSpec`, `MeridianState.apply`)
- Test: `services/meridian/tests/test_services.py` or `test_metrics.py` (extend — put the fault-profile tests where the existing `apply` tests live; check both)

**Interfaces:**
- Consumes: `MeridianState` fields (Task 1).
- Produces: `FaultSpec.type` accepts the 8 scenario names; `apply(spec)` implements each profile (moving only its cluster). Legacy types (`saturation`/`error`/`latency`/`crash`) keep working (extended to move their correlated cluster).

**RULING (load-bearing):** each scenario moves ONLY the metrics in its pinned profile; every other metric stays at baseline. `error` and `dependency_outage` MUST leave `cpu` at baseline. The tests assert this per scenario.

- [ ] **Step 1: Write the failing tests**

Add per-scenario profile tests. Find where the existing `apply` behavior is tested (grep `services/meridian/tests/` for `apply\|FaultSpec\|\.cpu`) and match its style. Example:

```python
from services.meridian.common import MeridianState, FaultSpec, CPU_HEALTHY

def _apply(scenario, **kw):
    st = MeridianState()
    st.apply(FaultSpec(type=scenario, magnitude=kw.get("magnitude", 1.0),
                       duration_seconds=kw.get("duration_seconds")))
    st.sample(now=0.0)
    return st

def test_error_keeps_cpu_at_baseline():  # THE load-bearing invariant
    st = _apply("error", magnitude=0.5)
    assert st.error_rate > 0
    assert st.cpu == CPU_HEALTHY          # cpu must NOT move
    assert st.latency_p99_ms == MeridianState().latency_p99_ms  # latency baseline

def test_dependency_outage_moves_errors_and_latency_not_cpu():
    st = _apply("dependency_outage")
    assert st.error_rate > 0 and st.latency_p99_ms > MeridianState().latency_p99_ms
    assert st.cpu == CPU_HEALTHY

def test_saturation_moves_cpu_cluster():
    st = _apply("saturation")
    assert st.cpu > CPU_HEALTHY and st.saturation > MeridianState().saturation
    assert st.queue_depth > MeridianState().queue_depth
    assert st.error_rate == 0  # errors stay baseline

def test_traffic_surge_moves_rate_and_capacity():
    st = _apply("traffic_surge")
    assert st.request_rate > MeridianState().request_rate and st.cpu > CPU_HEALTHY

def test_db_exhaustion_saturates_pool():
    st = _apply("db_exhaustion")
    assert st.db_pool_in_use >= st.db_pool_max and st.latency_p99_ms > MeridianState().latency_p99_ms

def test_crash_sets_unhealthy():
    st = _apply("crash")
    assert st.unhealthy is True

def test_legacy_types_still_work():
    for t in ("saturation", "error", "latency", "crash"):
        st = _apply(t)  # must not raise; scenario recognized
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest services/meridian/tests/ -k "scenario or fault or apply or cpu" -v`
Expected: FAIL — the new scenarios aren't implemented.

- [ ] **Step 3: Implement the 8 profiles in `apply`**

Rewrite `MeridianState.apply` as a scenario dispatch. Each branch sets only its cluster (per the pinned table), leaving the rest at baseline. For `memory_leak`, set up the ramp (start=baseline, target=high, duration=`spec.duration_seconds or 60`) rather than a step. Keep `error`/`dependency_outage` cpu-at-baseline. Define broken constants per metric. Preserve the existing docstring's invariant note and extend it to name every scenario.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest services/meridian/tests/ -v`
Expected: PASS (new profile tests + all existing Meridian tests).

- [ ] **Step 5: Full suite + lint**

Run: `uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check .`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add services/meridian/common.py services/meridian/tests/
git commit -m "feat(metrics): typed fault scenarios -> metric-cluster profiles (cpu-baseline-on-error preserved)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: demo_app parity

**Files:**
- Modify: `services/demo_app/app.py` (add the USE+RED gauge set; `/break`/`/fix` move a representative cluster)
- Test: a demo_app test (find or add — check `services/demo_app/tests/` or `tests/` for existing demo_app coverage)

**Interfaces:**
- Produces: demo_app `/metrics` exposes `cpu_usage` + the USE/RED gauges; `/break` moves a representative cluster (cpu↑, error↑, latency↑, memory↑), `/fix` restores baseline.

- [ ] **Step 1: Write the failing test**

Find existing demo_app test coverage (`grep -rln "demo_app\|demo-app" tests/ services/demo_app/`); match its style. Assert `/metrics` exposes the new gauge set at baseline, and `/break` then `/metrics` shows the cluster elevated. (demo_app uses the module-level default registry — no per-registry fixture needed, but tests that construct the app multiple times in one process may need care; check the existing pattern.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest <the demo_app test path> -v`
Expected: FAIL — new gauges absent.

- [ ] **Step 3: Add the gauges + cluster toggle**

Add the USE+RED gauges (module-level, like `_cpu`). `/break` sets the representative cluster high; `/fix` restores baseline; `/metrics` keeps them fresh (like the existing `_cpu.set(...)`). Keep the existing `http_requests_total`/`http_request_errors_total` counters and `/work`.

- [ ] **Step 4: Run + full suite + lint**

Run: `uv run pytest <the demo_app test path> -v && uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check .`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add services/demo_app/app.py <the demo_app test path>
git commit -m "feat(metrics): demo_app emits the USE+RED metric set; /break moves the cluster

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Gateway ops-panel fold + selectors + ops UI presets

**Files:**
- Modify: `services/meridian/gateway/app.py` (`ops_metrics` — broaden the query + generalize the fold to carry all known metrics)
- Modify: `deploy/docker-compose.yml` (`INTELLIOPS_PROMETHEUS_QUERY`)
- Modify: the Meridian ops UI (find it — the static assets the gateway serves; expand the fault presets to the 8 scenarios)
- Test: `services/meridian/tests/test_gateway_metrics.py` (extend)

**Interfaces:**
- Consumes: the new metric names (Task 1).
- Produces: `ops_metrics` query selects the new metrics; the fold carries each known metric into the per-service row (not just cpu/error); the ops UI offers the 8 scenario presets. The `INTELLIOPS_PROMETHEUS_QUERY` compose env selects the new metrics.

- [ ] **Step 1: Write the failing test**

Extend `services/meridian/tests/test_gateway_metrics.py` (read it — it likely stubs a Prometheus response and asserts the fold). Add a case: a scrape result containing the new metrics (e.g. `latency_p99_ms`, `memory_usage_mb`) for a service folds them into that service's row (and does NOT crash). Keep the existing cpu/error assertions.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest services/meridian/tests/test_gateway_metrics.py -v`
Expected: FAIL — the fold only carries cpu/error today.

- [ ] **Step 3: Generalize the fold + broaden the query**

In `ops_metrics` (`gateway/app.py:110-157`): broaden `query` to `{__name__=~"cpu_usage|meridian_error_rate|request_rate|latency_p50_ms|latency_p99_ms|memory_usage_mb|saturation|queue_depth|db_pool_in_use|db_pool_max|disk_usage_percent"}` (or a prefix regex). Change the fold so `row` is a dict that accepts any known metric name (map `__name__` → the row key generically, e.g. `row[name] = val` for names in a known set), keeping `cpu_usage`/`error_rate` for the existing `healthy` computation. Keep it fail-soft (unknown names ignored, any Prometheus error → empty payload). Keep the `healthy` heuristic (cpu<50 and err<0.1) — optionally extend it, but that's not required for P1.

- [ ] **Step 4: Broaden the compose ingestion query**

In `deploy/docker-compose.yml`, update `INTELLIOPS_PROMETHEUS_QUERY` (currently `{__name__=~"cpu_usage|meridian_error_rate"}`) to include the new metric names (same regex as the gateway query, or a broader `{__name__=~".+"}` scoped to the demo/meridian jobs — but a name allowlist is safer/clearer). The `common/config.py` default stays `cpu_usage`.

- [ ] **Step 5: Expand the ops UI fault presets**

Find the Meridian ops UI (the static assets served by the gateway — check `services/meridian/ui/` or wherever `StaticFiles` mounts from in `gateway/app.py`). Expand the fault presets from the current set to the 8 scenarios, each with an honest human label ("Memory leak (gradual)", "Dependency outage", "DB pool exhaustion", …). Keep the sequential-injection guard. If the UI is plain HTML/JS, edit it directly; if it's in `frontend/`, run the frontend build.

- [ ] **Step 6: Run tests + build + full suite + lint**

Run: `uv run pytest services/meridian/tests/ -v && uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check . && npm --prefix frontend run build`
Expected: green + clean. (Run the frontend build only if the ops UI lives in `frontend/`.)

- [ ] **Step 7: Commit**

```bash
git add services/meridian/gateway/app.py deploy/docker-compose.yml services/meridian/tests/test_gateway_metrics.py <ops UI files>
git commit -m "feat(metrics): gateway ops-panel carries the full metric set; widen scrape + fault presets

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Docs + final gates

**Files:**
- Modify: `docs/MERIDIAN.md` (the metric set + the 8-scenario table + the cross-metric invariant)
- Modify: a short note in `README.md` + `flow.md` (the sample system now emits USE+RED)
- Commit the spec + this plan (untracked) onto the branch.

- [ ] **Step 1: Document the metric set + scenarios**

In `docs/MERIDIAN.md`: add a "Metrics" section listing the USE+RED set with one-line meanings, and a "Fault scenarios" table (the 8, each with its metric profile + the incident it models). State the **cross-metric diagnostic invariant** plainly (a scenario moves only its cluster; `error`/`dependency_outage` keep cpu at baseline so the correlator + RCA don't misread an error incident as capacity). Honest: these are simulated/toggle-driven metrics on a synthetic system.

- [ ] **Step 2: Short notes in README + flow**

README: the Meridian row / status line now says it emits a **USE+RED metric set** with typed fault scenarios (not just cpu/error). flow.md: a line in the Meridian/status section. Keep it brief — MERIDIAN.md is the detail.

- [ ] **Step 3: Final gates + compose-smoke sanity**

Run: `uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check . && npm --prefix frontend run build`
Expected: all green. (compose-smoke runs in CI; locally, optionally `docker compose -f deploy/docker-compose.yml up -d --build` and curl a Meridian `/metrics` to eyeball the new gauges, then `down`.)

- [ ] **Step 4: Commit**

```bash
git add docs/MERIDIAN.md README.md flow.md docs/superpowers/specs/2026-09-06-rich-metrics-phase1-design.md docs/superpowers/plans/2026-09-06-rich-metrics-phase1.md
git commit -m "docs(metrics): Meridian USE+RED metric set + typed fault scenarios; spec + plan

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review Notes (author)

- **Spec coverage:** §1 MeridianState+gauges → Task 1; §3 FaultSpec scenarios → Task 2; §4 demo_app → Task 3; §5 selectors+gateway → Task 4; §6 ops UI → Task 4; docs → Task 5. Acceptance criteria 1–7 mapped.
- **Load-bearing invariant asserted the moment it can be:** Task 2's `test_error_keeps_cpu_at_baseline` + `test_dependency_outage_...` lock the cpu-baseline-on-error property; Task 1 lays the fields down.
- **No rename / additive:** every task adds gauges/scenarios; `cpu_usage`/`meridian_error_rate` untouched. Task 4's gateway fold keeps the existing cpu/error mapping and adds the rest. Each task's full-suite run guards the base staying green.
- **Type/name consistency:** the pinned metric names are identical across Task 1 (gauges), Task 4 (fold query), the compose selector, and the docs. The 8 scenario names identical across Task 2 (apply), Task 4 (UI presets), and the docs.
- **Phase boundaries honored:** no correlator/RCA/health change in P1 (stated in Global Constraints + each task); a new-scenario incident diagnosing via semantic-selector/gap is expected and not a P1 defect.
- **Known soft spot (flag for the executor):** the Meridian ops UI location isn't pinned (Task 4 Step 5 says "find it"). The executor must locate the StaticFiles mount in `gateway/app.py` and edit the presets there; if the UI is minimal, keep the preset change minimal (honest labels + the 8 types) rather than a redesign.
