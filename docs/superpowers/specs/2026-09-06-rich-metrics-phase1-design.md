# Rich metric surface + typed fault profiles — Design Spec (Metrics Phase 1)

**Date:** 2026-09-06
**Owner:** Manvik
**Status:** design (architectural — broadens the emitted metric set across demo_app + the 4 Meridian services, generalizes the fault model from single-gauge toggles to typed metric-cluster profiles, and widens the Prometheus scrape/ingestion selectors + the Meridian ops UI). **Phase 1 of a 4-phase metrics arc** (P1: rich metrics + typed faults · P2: detection policy per metric kind · P3: richer RCA metric→runbook rules · P4: per-metric health verification).

**Depends on:** nothing beyond current master. Branch off master.

## The problem

IntelliOps *looks* like a one-metric system: the demo target emits `cpu_usage` and Meridian emits `cpu_usage` + `meridian_error_rate`. That is not an architectural limit — `PrometheusSource` ingests any metric by `__name__` and `RiverCorrelator` keeps a **per-metric** z-score baseline (`self._mean.setdefault(name, ...)`) — but with only 1–2 gauges per service, the system can't *demonstrate* production-grade breadth: real incidents show up as clusters of correlated metric movements (latency ↑ with queue depth ↑, errors ↑ with a dependency down, memory ramping toward an OOM), and there's nothing here to detect that richness against.

## Goal

Make demo_app and the four Meridian services emit a **production-shaped metric set** (USE + RED), and generalize the fault model so each **named fault scenario perturbs a realistic *cluster* of those metrics over time** — so different incidents look genuinely different downstream. This phase produces the *signals*; the detection/correlation layer already consumes arbitrary metrics, so no correlator change is needed here (P2 adds detection *policy*; P3/P4 use the richer signals for RCA + health).

## The metric set (USE + RED)

Per service, ~10–12 gauges. Names are Prometheus-conventional and carry the metric family in the name so downstream (P3 RCA rules, P2 policy) can classify them:

**RED (request-driven):**
- `request_rate` — requests/sec (traffic)
- `meridian_error_rate` — error ratio 0..1 (errors) *(the EXISTING metric — kept as-is, NOT renamed; see the Compatibility ruling below)*
- `latency_p50_ms`, `latency_p99_ms` — request duration (duration)

**USE (resources):**
- `cpu_usage` — utilization percent *(existing; kept)*
- `memory_usage_mb` — memory utilization
- `saturation` — a 0..1 saturation index (run-queue / thread-pool pressure)
- `queue_depth` — pending work items
- `db_pool_in_use`, `db_pool_max` — connection-pool utilization
- `disk_usage_percent` — disk utilization

(The exact final list is pinned in the plan; this is the target shape. Each is a `prometheus_client.Gauge`, bare — no `service` label — matching the existing convention where the scrape job injects `service` at scrape time.)

**Compatibility ruling (no rename — purely additive).** `meridian_error_rate` is referenced in real code beyond `common.py`: the gateway ops-panel fold + query (`gateway/app.py`), the compose ingestion selector (`docker-compose.yml`), the k8s Prometheus scrape (`deploy/k8s/`), and three Meridian test files. Renaming it to `error_rate` would force churn across all of those and risk the working live pipeline mid-arc for no functional gain. So Phase 1 **keeps `meridian_error_rate` exactly as-is** as Meridian's error-rate metric and only **adds** the new gauges alongside it. There is no `error_rate` alias and no rename — every change in this phase is additive. (demo_app, a separate process with its own metric names, may use whatever error metric name it already has or add one; it shares no registry with Meridian.)

## The fault model (typed scenarios → metric profiles)

Generalize `FaultSpec` from a single `type` that moves one or two gauges into **named scenarios**, each with a **metric profile** (which metrics move, by how much, and over what shape — step vs. ramp). The scenarios, each mapping to a distinct realistic incident:

| Scenario | Metric profile | The incident it models |
|---|---|---|
| `saturation` | cpu ↑, saturation ↑, queue_depth ↑ (step) | capacity exhaustion |
| `latency` | latency_p50/p99 ↑, queue_depth ↑ (step); cpu mildly ↑ | slow downstream / lock contention |
| `error` | error_rate ↑ (step); **cpu/latency held at baseline** | a failing dependency / bad code path |
| `memory_leak` | memory_usage_mb **ramps** over `duration_seconds` (gradual) | a leak trending toward OOM |
| `traffic_surge` | request_rate ↑, cpu ↑, saturation ↑, queue_depth ↑ (step) | load spike |
| `dependency_outage` | error_rate ↑, latency_p99 ↑ (step); cpu baseline | an upstream dependency down |
| `db_exhaustion` | db_pool_in_use → db_pool_max, latency ↑ (step) | connection-pool starvation |
| `crash` | `unhealthy=True` (readiness fails; detection-only) | a wedged process |

**Load-bearing invariant (preserve + extend):** the existing code documents that a pure-`error` fault must keep `cpu` at baseline, else the z-score correlator sees two anomalies and RCA's `scale-service` (0.6) outranks `restart-pod` (0.5), misdiagnosing an error incident as capacity. Phase 1 must preserve this and apply the same discipline to every new scenario: **a scenario's profile moves only the metrics that incident would realistically move**, so the *shape* of the anomaly cluster is itself diagnostic. (This is exactly what P3's RCA rules and P2's detection policy will key off — Phase 1 lays down the honest signal, later phases read it.)

`FaultSpec` gains: `type` becomes the scenario name (the 8 above); `magnitude` and `duration_seconds` stay; a `ramp` shape is supported for `memory_leak` (the `/metrics` handler advances the ramp based on elapsed time). Backward-compat: the four existing types (`saturation`/`error`/`latency`/`crash`) keep their current meaning (extended to move the new correlated gauges), so existing Meridian tests + the ops UI's current presets still work.

## Non-goals / constraints (Phase 1)

- **No correlator/detection change.** Per-metric z-score already handles every new metric. Detection *policy* (hard thresholds for error/saturation, seasonal latency) is **Phase 2** — explicitly out of scope here. Phase 1 must not special-case detection.
- **No RCA rule change.** Mapping the new metric families to runbooks is **Phase 3**. In Phase 1, a new-scenario incident will still be diagnosed by today's rules (cpu/error/deploy) or fall to the semantic selector/gap — that's expected and fine; P3 sharpens it.
- **No health-verification change.** Per-metric health is **Phase 4**.
- **Preserve the cross-metric diagnostic invariants** (above) — the single most important correctness property of this phase.
- **Additive + backward-compatible.** New gauges are added; `cpu_usage` stays; the existing fault types keep working; existing Meridian/demo tests stay green (updated only where they assert the exact gauge set). The base compose + CI stay green.
- **Sequential fault injection stays enforced.** The correlator groups anomalies by time window, not service; the Meridian ops UI's existing sequential-injection guard remains (concurrent faults on two services would merge into one incident).
- **Slim-boundary holds.** `prometheus_client` is already a base dep; no new heavy deps. demo_app/Meridian stay slim-target.

## Global Constraints

- **Gates:** `uv run pytest -m "not postgres and not kafka"` green (current base + new tests); `ruff check .` + `ruff format --check .` clean; `npm --prefix frontend run build` clean (Meridian ops UI is served by the gateway — if its UI assets are in `frontend/` or `services/meridian/ui`, build/lint accordingly).
- **compose-smoke stays green:** all 13 services still build + boot + pass `/health`|`/ready` (the CI job). The new gauges must render on `/metrics` without breaking the gateway's metrics-folding (`services/meridian/gateway/app.py` folds the scrape result into per-service rows — it must tolerate the new metric names).
- **Prometheus scrape + ingestion query widen** to select the new metrics (`deploy/docker-compose.yml`'s `INTELLIOPS_PROMETHEUS_QUERY`, the Prometheus scrape config, and the gateway's ops-panel query at `gateway/app.py`). The `common/config.py` default (`prometheus_query="cpu_usage"`) stays as the test-safe default; the live selector broadens in compose.
- **Commit trailer** on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **git:** branch `feat/rich-metrics-phase1` off master. PR; user merges. Never merge to master.
- **Shared files:** `services/meridian/common.py` (the heart — `MeridianState` + `FaultSpec` + gauges), `services/meridian/gateway/app.py` (ops-panel metric folding + query), `services/demo_app/app.py`, `deploy/docker-compose.yml` (+ Prometheus scrape config under `deploy/`), the Meridian ops UI, and the affected tests.

---

## Design

### 1. `MeridianState` — the full metric set (`services/meridian/common.py`)

Extend `MeridianState.__init__` with the USE+RED fields (healthy baselines), a `_started_at`/ramp bookkeeping for `memory_leak`, and keep `unhealthy`. Add healthy-baseline + broken constants for each. `apply(spec)` becomes a **profile dispatch**: each scenario sets the cluster of fields its profile names (leaving the rest at baseline), and records ramp start for `memory_leak`. A `sample(now)` method (called by `/metrics`) returns the current values, advancing any active ramp by elapsed time. `clear()` resets all to baseline.

### 2. Gauges + `/metrics` (`services/meridian/common.py`)

Register one bare `Gauge` per metric in `make_meridian_service` (against `effective_registry`, no `service` label — the scrape job injects it). The `/metrics` handler calls `state.sample(now)` and sets every gauge. Keep `cpu_usage` and `meridian_error_rate` exactly as they are today (no rename — see the Compatibility ruling); the new gauges are added alongside them.

### 3. `FaultSpec` + scenarios (`services/meridian/common.py`)

`FaultSpec.type` accepts the 8 scenario names; `magnitude`/`duration_seconds` retained; the profile table (above) is implemented in `apply`. The four legacy types keep working (extended to move their correlated cluster). The latency middleware stays (real latency injection on domain routes) and now the latency *gauges* reflect it too.

### 4. demo_app parity (`services/demo_app/app.py`)

Give demo_app the same USE+RED gauge set and a `/break`/`/fix` that moves a representative cluster (keep `/break`/`/fix` as the simple quickstart controls; optionally accept a scenario name). Keep it a single process, single registry. The quickstart still works; it just emits richer metrics now.

### 5. Scrape + ingestion selectors (`deploy/`, `gateway/app.py`)

- Prometheus scrape config: unchanged if it already scrapes all `/metrics` series (it scrapes the endpoint, not per-metric) — confirm.
- `INTELLIOPS_PROMETHEUS_QUERY` (compose env): broaden the `{__name__=~"..."}` selector to include the new metric names (or a prefix/regex that matches the family). The `common/config.py` default stays `cpu_usage`.
- `services/meridian/gateway/app.py`: the ops-panel query + the fold-into-rows logic must tolerate the new metrics (render what it knows, ignore the rest) — it currently hard-maps `cpu_usage`/`error_rate`; make it not crash on new names and, ideally, surface a few key ones (latency, memory) in the ops panel.

### 6. Meridian ops UI (the fault presets)

The ops panel's fault presets expand from the current set to the 8 scenarios, each with a human label ("Memory leak (gradual)", "Dependency outage", …). The sequential-injection guard stays. Honest labels — each preset says what metrics it moves.

---

## Acceptance criteria

1. **Rich metrics emitted:** each Meridian service's `/metrics` exposes the full USE+RED gauge set (unit-test the gauge names are present and at healthy baseline on a fresh state). demo_app likewise.
2. **Typed fault profiles move the right cluster:** unit tests over `MeridianState.apply` — each of the 8 scenarios moves exactly its profile's metrics and leaves the others at baseline. In particular: **`error`/`dependency_outage` keep `cpu` at baseline** (the load-bearing invariant); `saturation`/`traffic_surge` move cpu; `memory_leak` ramps `memory_usage_mb` over time (assert two `sample()` calls at different times show the ramp); `crash` sets `unhealthy`.
3. **Backward-compatible:** the four legacy fault types still work; `meridian_error_rate` and `cpu_usage` are unchanged (no rename) so existing scrape/ingestion/gateway/tests don't break; the change is purely additive; existing Meridian tests pass (updated only where they assert the exact gauge set).
4. **Gateway tolerates the new metrics:** `gateway/app.py`'s ops-panel fold doesn't crash on the new metric names (unit test with a scrape result containing them); the sequential guard still holds.
5. **Live selectors widened:** the compose `INTELLIOPS_PROMETHEUS_QUERY` selects the new metrics; a documented manual check shows a non-cpu fault (e.g. `memory_leak`) producing an anomaly in the pipeline. (Detection already handles it; RCA mapping is P3 — the incident may diagnose via the semantic selector or land in the gap, which is expected for P1.)
6. **Gates green:** base suite + new tests; ruff clean; frontend build clean; compose-smoke boots all 13 (the new gauges don't break `/metrics` or the gateway).
7. **Honesty preserved:** the ops UI presets state what each moves; no fabricated metric is presented as real infra.

## Suggested task ordering (for the plan)

1. **`MeridianState` + gauges + `/metrics`:** the full metric field set, healthy baselines, the `sample(now)` method (with ramp support), and gauge registration. Unit tests: fresh state exposes all gauges at baseline; `sample` advances a ramp. (Foundation — no fault logic yet.)
2. **`FaultSpec` scenarios + profiles:** the 8 scenarios in `apply`, preserving the cpu-baseline-on-error invariant and extending it to every scenario. Unit tests: the per-scenario cluster assertions (AC2), legacy-type compat (AC3).
3. **demo_app parity:** the same gauge set + `/break`/`/fix` cluster. Unit test.
4. **Gateway fold + selectors + ops UI:** make `gateway/app.py` tolerate/surface the new metrics; broaden the compose query; expand the ops-panel presets. Unit test the fold; frontend build.
5. **Docs:** update `docs/MERIDIAN.md` (the metric set + the scenario table + the honest cross-metric invariant), a note in `README`/`flow.md` that the sample system now emits USE+RED; commit the spec + plan. Final gates + compose-smoke.

Rationale: state+gauges first (the signals exist), then the fault profiles (the signals move realistically), then demo_app parity, then the surface (gateway/query/UI), then docs — each independently testable, and the load-bearing cross-metric invariant is asserted the moment the fault profiles land.
