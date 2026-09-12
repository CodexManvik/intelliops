# Honesty & Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make IntelliOps *demonstrably* real to a skeptic at the screen — fix the two UI bugs that make it lie, stop the read-model dropping the engine's rich evidence, expose the internals over HTTP, then build the UI (incident drill-down, "under the hood" system view, live-configurable LLM) that shows the proof.

**Architecture:** The engine is already real; the read-model *projection* (`services/read/projection.py`) collapses every rich signal (raw member events, z-score, hypothesis evidence, LLM explanation, real outcome) into thin scalars, so the UI can't prove anything. The fix is three staged layers: **(1)** fix the reappearing approve gate + hardcoded outcome text + fake chrome (frontend only, ships first); **(2)** widen the projection + add additive contract fields + expose internals via new read/correlation/rca endpoints (backend); **(3)** build the drill-down, system view, and live LLM settings UI on top. All contract/config changes are additive with test-safe defaults; the existing 414-test suite stays green.

**Tech Stack:** Python 3.12 + FastAPI + Pydantic v2 (services), pytest (`uv run pytest`), ruff (lint+format); React 18 + TypeScript + Vite + Tailwind + framer-motion (`frontend/`, `npm run build`). Redis Streams bus; SSE `/stream`.

**Spec:** `docs/superpowers/specs/2026-08-25-honesty-and-evidence-design.md` — read it alongside this plan; the plan argues from it.

## Global Constraints

- **Test-safe & additive.** Every contract change is a new optional field defaulting to `None`/empty; every config change has a test-safe default. The existing suite (`uv run pytest -m "not postgres and not kafka"`) stays green. Mock mode still works — mock data (`frontend/src/data/mock.ts`) gets the new fields too.
- **No fabricated data in live mode.** If a number/label can't be computed from real data, it is removed or explicitly labeled (`dry-run`, `no baseline yet`, `template (no LLM)`) — never a hardcoded literal dressed as real.
- **Gates (must pass before any task is complete):** `uv run pytest -m "not postgres and not kafka"` green; `ruff check .` clean; `ruff format --check .` clean; `npm --prefix frontend run build` clean.
- **The advisory LLM contract is inviolable:** the explanation text is advisory-only — it must never affect confidence, hypothesis ordering, or `suggested_runbook_id` (see `services/rca/consumer.py:40-43`). Any LLM change preserves this.
- **Commit trailer** on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **git identity:** CodexManvik. Branch `fix/honesty-and-evidence` (already created, off master). Do NOT merge or push to master — open a PR; the user merges.
- **Windows/git-bash** shell. Ports: read 8007, correlation 8002, governance 8005, rca 8003 (in-container), console 5173. `frontend/.env.local` sets `VITE_DATA_MODE=live`.
- **Shared files (coordinate across tasks):** `common/contracts.py` (additive fields — Task 5), `common/config.py` (additive — Task 5), `services/read/projection.py` + `services/read/app.py` (the crux — Tasks 6-7), `frontend/src/data/types.ts` (additive — Tasks 8, 10), `frontend/src/data/api.ts` + `source.ts` (new loaders — Tasks 8, 10).

---

## STAGE 1 — Stop the lying (frontend only). Ships first.

### Task 1: Join the real outcome onto the situation in mock data + types, and read it in Incidents

**Why first:** Tasks 2-3 need a place to read the *real* outcome from. Today `Incidents.tsx` imports only `loadSituations`/`decideApproval` and can't reach an outcome, so it hardcodes "healthy". This task adds an optional `outcome` field to the `Situation` type + mock data so the UI has a real value to render. (Stage 2 will populate it from the backend projection; this task makes the type/UI ready and keeps mock mode honest.)

**Files:**
- Modify: `frontend/src/data/types.ts` (add optional `outcome` to `Situation`)
- Modify: `frontend/src/data/mock.ts` (add `outcome` to the resolved/failed mock situations)
- Test: none (type-only + mock data; verified by `npm run build` + Task 2/3 rendering)

**Interfaces:**
- Produces: `Situation.outcome?: SituationOutcome` where
  ```ts
  export interface SituationOutcome {
    result: RemediationResult;      // "success" | "failure" | "rolled_back"
    health_after: OutcomeReason;    // the real reason vocabulary
    mode: "dry_run" | "k8s";        // remediation mode — for honest labeling
    steps: string[];                // human-readable executed steps (may be empty)
  }
  ```

- [ ] **Step 1: Add the `SituationOutcome` interface and the optional field to `Situation`**

In `frontend/src/data/types.ts`, after the `Hypothesis` interface (line 36) add:

```ts
export interface SituationOutcome {
  result: RemediationResult;
  health_after: OutcomeReason;
  mode: "dry_run" | "k8s";
  steps: string[];
}
```

Then inside `interface Situation` (after `suppressed: boolean;`, line 52), add:

```ts
  outcome?: SituationOutcome; // present once remediation has produced a result
```

- [ ] **Step 2: Give the resolved/failed mock situations a real outcome**

In `frontend/src/data/mock.ts`, find each situation whose `status` is `"resolved"` or `"failed"` and add an `outcome` object matching its status. For a `resolved` one:

```ts
    outcome: {
      result: "success",
      health_after: "healthy",
      mode: "dry_run",
      steps: ["scale web +2 replicas", "verify health", "hold"],
    },
```

For a `failed`/rejected one:

```ts
    outcome: {
      result: "failure",
      health_after: "aborted:rejected",
      mode: "dry_run",
      steps: [],
    },
```

(If no resolved/failed situation exists in mock, add the field to at least the `diagnosed` one as `undefined` is fine — leave it absent. The point is the type compiles and any terminal mock situation carries a real-looking outcome.)

- [ ] **Step 3: Verify the build compiles**

Run: `npm --prefix frontend run build`
Expected: PASS (no TS errors).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/data/types.ts frontend/src/data/mock.ts
git commit -m "feat(ui): add optional real outcome to Situation type + mock data"
```

---

### Task 2: Fix the reappearing approve gate (reconcile optimistic state with server truth)

**Files:**
- Modify: `frontend/src/views/Incidents.tsx:38-84` (the `list` memo, `update`, `approve`, `reject`, `stageIndex`)
- Test: none (behavioral UI; verified live in Task 12 + `npm run build`)

**Interfaces:**
- Consumes: `Situation.outcome?` (Task 1), `Situation.status`, `decideApproval` (unchanged).
- Produces: an Incidents view where, after approve/reject, the optimistic override yields to the server's terminal status (`resolved`/`failed`) and the gate never reappears.

**Root cause (from the audit):** `overrides[id]` is set to `{status:'acting'}` on approve (`Incidents.tsx:60`) and NEVER cleared (`update()` only spreads-in, line 54). `list` re-applies it every 5s poll (line 40). The server read-model never emits `acting` — a HITL situation sits at `diagnosed` then jumps to `resolved`/`failed`. So `acting` matches no render branch and the gate falls through to the `else` forever.

**Fix:** when the server refetch shows a situation has reached a terminal status (`resolved`/`failed`), drop its optimistic override so the server wins. Keep the optimistic override only while the server hasn't caught up.

- [ ] **Step 1: Prune terminal overrides when server data arrives**

Replace the `list` memo (lines 39-42) with a version that drops overrides once the server reflects a terminal state, and add an effect that prunes the override map itself so it can't grow unbounded:

```tsx
  // merge server data with local optimistic overrides, but let server truth win:
  // once the server shows a terminal status, the optimistic override is stale.
  const list = useMemo<Situation[]>(
    () =>
      seed.map((s) => {
        const o = overrides[s.id];
        if (!o) return s;
        // server reached a terminal state → discard the optimistic flip
        if (s.status === "resolved" || s.status === "failed") return s;
        return { ...s, ...o };
      }),
    [seed, overrides],
  );

  // Prune overrides the server has caught up to, so the map can't pin a stale
  // 'acting' forever (the bug: overrides never cleared → gate reappears).
  useEffect(() => {
    setOverrides((o) => {
      const next: Record<string, Partial<Situation>> = {};
      let changed = false;
      for (const [id, patch] of Object.entries(o)) {
        const srv = seed.find((s) => s.id === id);
        if (srv && (srv.status === "resolved" || srv.status === "failed")) {
          changed = true; // drop it — server is terminal
        } else {
          next[id] = patch;
        }
      }
      return changed ? next : o;
    });
  }, [seed]);
```

- [ ] **Step 2: Make `approve` optimistic-then-yield, not permanent 'acting'**

Replace `approve()` (lines 57-71). The optimistic status becomes `acting` only as a transient "awaiting outcome" hint; in live mode the poll converges to the server's terminal status and Step 1 prunes the override. Remove the mock-only `setTimeout` asymmetry by driving mock resolution through the same path (mock `decideApproval` is a no-op, so in mock we still simulate the terminal outcome, but as `resolved` with the override that Step 1 will NOT prune in mock since mock `seed` never advances — so keep the mock timeout, but guard it clearly):

```tsx
  async function approve() {
    if (working || !sel) return;
    setWorking(true);
    update(sel.id, { status: "acting" }); // transient: "awaiting outcome"
    try {
      await decideApproval(`appr-${sel.id}`, "approved");
      pushToast("success", `Approved — remediating ${sel.suggested_runbook_id ?? "playbook"}`);
      if (!LIVE) {
        // mock mode: server never advances, so simulate the terminal outcome locally
        setTimeout(
          () =>
            update(sel.id, {
              status: "resolved",
              outcome: { result: "success", health_after: "healthy", mode: "dry_run", steps: [] },
            }),
          1400,
        );
      }
      // live mode: the 5s poll converges to the real server status; Step 1 prunes the override
    } catch (e) {
      pushToast("error", `Approval failed: ${e instanceof Error ? e.message : "unknown"}`);
      update(sel.id, { status: "diagnosed" }); // roll the optimistic flip back
    } finally {
      setWorking(false);
    }
  }
```

Note: `setWorking(false)` now runs in `finally` on async completion, not a fixed 1500ms timer (fixes the secondary bug). Remove the old trailing `setTimeout(() => setWorking(false), 1500);`.

- [ ] **Step 3: Guard `reject` the same way**

Replace `reject()` (lines 73-82):

```tsx
  async function reject() {
    if (working || !sel) return;
    setWorking(true);
    update(sel.id, { status: "failed" });
    try {
      await decideApproval(`appr-${sel.id}`, "rejected");
      pushToast("success", "Rejected — no action taken");
      if (!LIVE) {
        update(sel.id, {
          status: "failed",
          outcome: { result: "failure", health_after: "aborted:rejected", mode: "dry_run", steps: [] },
        });
      }
    } catch (e) {
      pushToast("error", `Reject failed: ${e instanceof Error ? e.message : "unknown"}`);
      update(sel.id, { status: "diagnosed" });
    } finally {
      setWorking(false);
    }
  }
```

- [ ] **Step 4: Hide the dev "reset" button in live mode**

The reset button (lines 258-260) sets an override `{status:'detected'}` that also never clears. Gate it behind `!LIVE`:

```tsx
                        {!LIVE && (
                          <button onClick={() => update(sel.id, { status: "detected", outcome: undefined })} className="ml-auto flex items-center gap-1.5 rounded-full px-3 py-2.5 font-mono text-2xs text-ink-3 hover:text-ink-2">
                            <ArrowsClockwise size={13} weight="light" /> reset
                          </button>
                        )}
```

- [ ] **Step 5: Verify the build compiles**

Run: `npm --prefix frontend run build`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/Incidents.tsx
git commit -m "fix(ui): reconcile optimistic approve state with server truth — gate no longer reappears"
```

---

### Task 3: Replace the hardcoded outcome text with the real `health_after`/steps

**Files:**
- Modify: `frontend/src/views/Incidents.tsx:218-233` (the resolved/failed result panel)
- Test: none (verified by `npm run build` + live Task 12)

**Interfaces:**
- Consumes: `Situation.outcome?` (Task 1).

**Root cause:** lines 222/230 hardcode `"healthy"`/`"aborted:rejected"` literals regardless of what happened.

- [ ] **Step 1: Render the real outcome in the resolved branch**

Replace the resolved branch (lines 218-225) so the reason and steps come from `sel.outcome`, with an honest dry-run label:

```tsx
                  {sel.status === "resolved" ? (
                    <div className="flex items-start gap-3">
                      <span className="mt-0.5 flex h-9 w-9 flex-none items-center justify-center rounded-full bg-sev-ok/15 text-sev-ok"><Check size={17} weight="bold" /></span>
                      <div>
                        <div className="text-sm font-medium text-ink">
                          Resolved · <span className="font-mono text-sev-ok">{sel.outcome?.health_after ?? "resolved"}</span>
                          {sel.outcome?.mode === "dry_run" && <span className="ml-2 rounded-md bg-black/[0.05] px-1.5 py-0.5 font-mono text-2xs text-ink-3">dry-run</span>}
                        </div>
                        {sel.outcome?.steps && sel.outcome.steps.length > 0 && (
                          <div className="mt-1 font-mono text-2xs text-ink-3">steps: {sel.outcome.steps.join(" → ")}</div>
                        )}
                        <div className="font-mono text-2xs text-ink-3">outcome labeled → reliability rising → next matching storm may be suppressed</div>
                      </div>
                    </div>
```

- [ ] **Step 2: Render the real outcome in the failed branch**

Replace the failed branch (lines 226-233):

```tsx
                  ) : sel.status === "failed" ? (
                    <div className="flex items-start gap-3">
                      <span className="mt-0.5 flex h-9 w-9 flex-none items-center justify-center rounded-full bg-sev-warn/15 text-sev-warn"><X size={17} weight="bold" /></span>
                      <div>
                        <div className="text-sm font-medium text-ink">
                          No action taken · <span className="font-mono text-sev-warn">{sel.outcome?.health_after ?? "aborted"}</span>
                        </div>
                        <div className="font-mono text-2xs text-ink-3">gate failed closed — nothing executed</div>
                      </div>
                    </div>
```

- [ ] **Step 3: Verify the build compiles**

Run: `npm --prefix frontend run build`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/views/Incidents.tsx
git commit -m "fix(ui): show the real remediation outcome, not hardcoded 'healthy'/'aborted'"
```

---

### Task 4: Remove or make-real the hardcoded chrome (Overview deltas/captions)

**Files:**
- Modify: `frontend/src/views/Overview.tsx:161,176,184-203` (MiniStat deltas, "on target", "target band")
- Test: none (verified by `npm run build`)

**Interfaces:**
- Consumes: `Metrics` (unchanged).

**Root cause:** `−41%`/`+6` deltas (lines 189, 200), `"on target"` (161), `"target band 80–95%"` (176) are hardcoded literals implying computed trends that don't exist.

- [ ] **Step 1: Drop the fabricated `delta`/`up` props from both MiniStats in live mode**

The `MiniStat` component already renders `delta` only when the prop is truthy (line 55). Change the two call sites (lines 184-192 and 194-203) to pass `delta`/`up` **only in mock mode** — in live there is no prior-window baseline to compute a delta:

For the MTTR MiniStat (lines 184-192), change `delta="−41%"` and `up` to:

```tsx
            delta={LIVE ? undefined : "−41%"}
            up={!LIVE}
```

For the Auto-remediated MiniStat (lines 194-203), change `delta="+6"` and `up` to:

```tsx
            delta={LIVE ? undefined : "+6"}
            up={!LIVE}
```

- [ ] **Step 2: Make the "on target" badge and "target band" caption conditional**

Line 161 (`on target` badge): replace with a badge that only asserts "on target" when the real value is in-band, else shows the real value neutrally:

```tsx
                <span className="rounded-full border border-sev-ok/25 bg-sev-ok/10 px-2.5 py-0.5 font-mono text-2xs text-sev-ok">{metrics.noiseReductionPct >= 80 ? "on target" : `${metrics.noiseReductionPct}%`}</span>
```

Line 176 (`target band 80–95%` middle caption): this is a static reference band, which is honest as a *target* label — keep it but make the flanking `00:00`/`now` honest by leaving them (they label the spark axis). No change needed to line 176 itself; leave the target band as a labeled reference.

- [ ] **Step 3: Verify the build compiles**

Run: `npm --prefix frontend run build`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/views/Overview.tsx
git commit -m "fix(ui): drop fabricated deltas/captions in live mode — no fake trend numbers"
```

Note on Governance chrome: the gate cards, RBAC table, and compliance footer (`Governance.tsx:8-27,113-124,99`) describe the *real* enforced policy (ADR-003/007/008 are real gates; the RBAC rows match `policies/rbac_policy.yaml`) — they are a correct **policy reference**, not fabricated live state. Leaving them is honest; a future task could label them "policy reference (static)" but that is out of scope for Stage 1 (the spec calls it acceptable to label them). No change here.

---

## STAGE 2 — Stop dropping evidence (backend). Additive contracts + widened projection + new endpoints.

### Task 5: Additive contract + config fields (outcome steps/mode, situation peak-score, LLM live-config surface)

**Files:**
- Modify: `common/contracts.py:112-118` (`RemediationOutcome`), `:55-64` (`Situation`)
- Modify: `common/config.py` (no new fields strictly needed; confirm the `llm_explanation_*` fields exist — they do, lines 68-71)
- Test: `services/read/tests/test_projection.py` (extend), or a new `tests/test_contracts_additive.py` at repo root

**Interfaces:**
- Produces:
  - `RemediationOutcome.steps: list[str] = []` — human-readable executed steps.
  - `RemediationOutcome.mode: str = "dry_run"` — `"dry_run"` | `"k8s"`.
  - `Situation.peak_score: float | None = None` — the correlator's max z-score for the window.
  - `Situation.baseline: dict | None = None` — per-metric `{metric_name: {mean, std}}` snapshot at emit time (optional).

- [ ] **Step 1: Write the failing test for the new additive fields**

Create `tests/test_contracts_additive.py`:

```python
from datetime import UTC, datetime

from common.contracts import (
    RemediationOutcome,
    RemediationResult,
    Situation,
    SituationStatus,
)


def test_remediation_outcome_has_steps_and_mode_defaults():
    o = RemediationOutcome(
        situation_id="sit-x",
        playbook_id="scale-service",
        result=RemediationResult.SUCCESS,
        health_after="healthy",
        ts=datetime.now(UTC),
    )
    assert o.steps == []
    assert o.mode == "dry_run"


def test_situation_has_optional_peak_score_and_baseline():
    s = Situation(
        id="sit-x",
        status=SituationStatus.DETECTED,
        severity="high",
        first_seen=datetime.now(UTC),
        last_seen=datetime.now(UTC),
        signature="x",
    )
    assert s.peak_score is None
    assert s.baseline is None
    s2 = s.model_copy(update={"peak_score": 6.3, "baseline": {"cpu_usage": {"mean": 18.0, "std": 2.0}}})
    assert s2.peak_score == 6.3
    assert s2.baseline["cpu_usage"]["mean"] == 18.0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_contracts_additive.py -v`
Expected: FAIL (`steps`/`mode`/`peak_score`/`baseline` not defined).

- [ ] **Step 3: Add the fields**

In `common/contracts.py`, extend `RemediationOutcome` (after line 118 `hitl_mode: ...`):

```python
    steps: list[str] = Field(default_factory=list)
    mode: str = "dry_run"  # "dry_run" | "k8s"
```

Extend `Situation` (after line 64 `signature: str`):

```python
    peak_score: float | None = None  # correlator max z-score for the window
    baseline: dict | None = None  # per-metric {name: {mean, std}} at emit time
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_contracts_additive.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite to confirm additivity**

Run: `uv run pytest -m "not postgres and not kafka" -q`
Expected: PASS (no existing test broken — the fields are optional/defaulted).

- [ ] **Step 6: Commit**

```bash
git add common/contracts.py tests/test_contracts_additive.py
git commit -m "feat(contracts): additive outcome.steps/mode + situation.peak_score/baseline"
```

---

### Task 6: Attach the peak z-score + baseline to the emitted Situation (correlation engine)

**Files:**
- Modify: `services/correlation/engine.py:69-78` (`_correlate_buffer` — attach the score before reset), `adapters/river_correlator.py:87-99` (`correlate` accepts the extras)
- Test: `services/correlation/tests/test_engine.py` (extend)

**Interfaces:**
- Consumes: `Situation.peak_score`/`baseline` (Task 5), `RiverCorrelator.correlate(events, severity)` (existing).
- Produces: an emitted `Situation` carrying `peak_score` = the window's max score, and `baseline` = per-metric mean/std snapshot for the member metrics.

**Root cause:** the engine keeps `_max_score` (line 60) then resets it (line 73) without ever attaching it.

- [ ] **Step 1: Write the failing test**

In `services/correlation/tests/test_engine.py`, add a test that feeds a warm baseline then a spike, flushes, and asserts the Situation carries a non-None `peak_score` above the threshold:

```python
def test_emitted_situation_carries_peak_score():
    from datetime import UTC, datetime, timedelta

    from common.contracts import TelemetryEvent, TelemetryKind
    from services.correlation.adapters.river_correlator import RiverCorrelator
    from services.correlation.engine import CorrelationEngine

    eng = CorrelationEngine(RiverCorrelator(z_threshold=3.0, warmup_samples=5), window_seconds=30.0)
    base = datetime(2026, 1, 1, tzinfo=UTC)

    def ev(v, i):
        return TelemetryEvent(
            source="test", kind=TelemetryKind.METRIC, name="cpu_usage",
            value=v, labels={"service": "web"}, ts=base + timedelta(seconds=i), fingerprint="fp",
        )

    for i in range(10):
        eng.add(ev(20.0, i))          # learn a ~20 baseline
    eng.add(ev(200.0, 11))            # spike → scores high, buffers
    sit = eng.flush()
    assert sit is not None
    assert sit.peak_score is not None and sit.peak_score > 3.0
    assert sit.baseline is not None and "cpu_usage" in sit.baseline
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest services/correlation/tests/test_engine.py::test_emitted_situation_carries_peak_score -v`
Expected: FAIL (`peak_score` is None).

- [ ] **Step 3: Add a `baseline_snapshot()` to the correlator**

In `services/correlation/adapters/river_correlator.py`, add a method that returns per-metric mean/std (reusing the existing `_mean`/`_var`):

```python
    def baseline_snapshot(self) -> dict:
        """Per-metric {name: {mean, std}} for attaching to an emitted Situation."""
        out: dict = {}
        for name, mean in list(self._mean.items()):
            var = self._var[name]
            out[name] = {"mean": mean.get(), "std": var.get() ** 0.5}
        return out
```

- [ ] **Step 4: Attach peak_score + baseline in `_correlate_buffer`**

In `services/correlation/engine.py`, modify `_correlate_buffer` (lines 69-78) to capture the score and baseline BEFORE the reset, then attach via `model_copy`:

```python
    def _correlate_buffer(self) -> Situation | None:
        severity = self._correlator._severity_band(self._max_score)
        sit = self._correlator.correlate(self._buffer, severity=severity)
        peak = self._max_score
        baseline = (
            self._correlator.baseline_snapshot()
            if hasattr(self._correlator, "baseline_snapshot")
            else None
        )
        member_metrics = {e.name for e in self._buffer}
        if baseline is not None:
            baseline = {k: v for k, v in baseline.items() if k in member_metrics}
        sit = sit.model_copy(update={"peak_score": peak, "baseline": baseline})
        self._buffer = []
        self._max_score = 0.0
        # Closed loop: suppress a Situation whose signature reliably self-heals.
        if self._correlator.should_suppress(sit.signature, self._suppress_threshold):
            self._suppressed = sit
            return None
        return sit
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest services/correlation/tests/test_engine.py::test_emitted_situation_carries_peak_score -v`
Expected: PASS.

- [ ] **Step 6: Run the correlation suite**

Run: `uv run pytest services/correlation/tests/ -q`
Expected: PASS (robust/trained correlators lack `baseline_snapshot` → `hasattr` guard yields `None`, no break).

- [ ] **Step 7: Commit**

```bash
git add services/correlation/engine.py services/correlation/adapters/river_correlator.py services/correlation/tests/test_engine.py
git commit -m "feat(correlation): attach peak z-score + per-metric baseline to the emitted Situation"
```

---

### Task 7: Record the executed steps + mode on the RemediationOutcome (action service)

**Files:**
- Modify: `services/action/remediate.py:29-39` (`_outcome`), `:95-113` (execute path builds steps)
- Test: `services/action/tests/test_remediate.py` (extend)

**Interfaces:**
- Consumes: `RemediationOutcome.steps`/`mode` (Task 5), `RemediationPlan.steps` (existing `RemediationStep` list).
- Produces: a SUCCESS/ROLLED_BACK outcome whose `steps` are the human-readable executed plan steps and whose `mode` reflects `remediator_mode` config.

**Root cause:** `_outcome` never sets steps/mode; `DryRunRemediator.execute` only logs.

- [ ] **Step 1: Write the failing test**

The file already provides `_situation()`, `_playbook(hitl=HitlMode.AUTO, reversible=True)`, `FakeGate`, plus imports for `FixedHealthChecker`, `RecordingRemediator`, `RemediationStep`, `RemediationResult`, `HitlMode` (confirmed `test_remediate.py:1-52`). Add this test, which builds an AUTO playbook with a `scale` step and asserts the outcome records steps + mode:

```python
def test_successful_outcome_records_steps_and_mode():
    situation = _situation()
    playbook = _playbook(hitl=HitlMode.AUTO, reversible=True)
    playbook = playbook.model_copy(update={"steps": [RemediationStep(action="scale", replicas=2)]})
    gate = FakeGate(rbac_allow=True, decision_status="approved")
    remediator = RecordingRemediator(execute_result=True)
    health = FixedHealthChecker(healthy=True)  # match this file's existing FixedHealthChecker usage

    outcome = execute_remediation(
        situation, playbook, gate, remediator, health,
        timeout_seconds=1.0, poll_interval_seconds=0.1,
    )
    assert outcome.result == RemediationResult.SUCCESS
    assert outcome.steps  # non-empty, human-readable
    assert any("scale" in s for s in outcome.steps)
    assert outcome.mode in ("dry_run", "k8s")
```

If `FixedHealthChecker`'s constructor differs from `healthy=True`, copy the exact construction a passing test in this same file already uses (grep the file for `FixedHealthChecker(`).

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest services/action/tests/test_remediate.py::test_successful_outcome_records_steps_and_mode -v`
Expected: FAIL (`steps` empty).

- [ ] **Step 3: Add a step-formatter and thread steps/mode through `_outcome`**

In `services/action/remediate.py`, add a helper and extend `_outcome` to accept optional steps/mode:

```python
def _format_steps(plan: RemediationPlan) -> list[str]:
    out: list[str] = []
    for step in plan.steps:
        if step.action == "scale" and step.replicas is not None:
            sign = "+" if step.replicas >= 0 else ""
            out.append(f"scale {plan.target.deployment} {sign}{step.replicas} replicas")
        elif step.note:
            out.append(f"{step.action}: {step.note}")
        else:
            out.append(step.action)
    return out
```

Change `_outcome`'s signature and body:

```python
def _outcome(
    situation: Situation,
    playbook: Playbook,
    result: RemediationResult,
    health_after: str,
    steps: list[str] | None = None,
    mode: str = "dry_run",
) -> RemediationOutcome:
    return RemediationOutcome(
        situation_id=situation.id,
        playbook_id=playbook.id,
        result=result,
        health_after=health_after,
        ts=datetime.now(UTC),
        hitl_mode=playbook.hitl_mode,
        steps=steps or [],
        mode=mode,
    )
```

- [ ] **Step 4: Pass steps + mode on the execute/rollback outcomes**

In `execute_remediation`, after `plan = RemediationPlan(...)` (line 99), compute:

```python
    steps = _format_steps(plan)
    mode = get_settings().remediator_mode
```

Then update the three post-execution `_outcome(...)` returns (lines 104, 109, 113) to pass `steps=steps, mode=mode`. Leave the early gate-failure outcomes (skipped/refused/denied/aborted, lines 67/72/77/93) with no steps (nothing ran) — they correctly stay empty.

- [ ] **Step 5: Run the test + the action suite**

Run: `uv run pytest services/action/tests/test_remediate.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/action/remediate.py services/action/tests/test_remediate.py
git commit -m "feat(action): record executed steps + remediation mode on the outcome"
```

---

### Task 8: Widen the read-model projection — keep the evidence (the crux)

**Files:**
- Modify: `services/read/projection.py:89-158` (`apply_detected`, `apply_diagnosed`, `apply_outcome`)
- Test: `services/read/tests/test_projection.py` (extend)

**Interfaces:**
- Consumes: `Situation.member_events`/`peak_score`/`baseline` (Tasks 5-6), `RootCauseHypothesis.evidence`/`explanation` (existing), `RemediationOutcome.steps`/`mode`/`health_after` (Tasks 5, 7).
- Produces: each projected situation dict now carries: `member_events` (bounded list of `{name, value, labels, kind, ts}`), `hypotheses` with `evidence` + `explanation`, `peak_score`, `baseline`, `title` (readable), `stages` (timeline), and `outcome` (joined real outcome). Additive keys — the existing frontend keys (`memberCount`, `hypotheses[].description/confidence/suggested_runbook_id`, `status`, etc.) all remain.

- [ ] **Step 1: Write the failing test**

In `services/read/tests/test_projection.py`, add a test that applies a detected → diagnosed → outcome sequence and asserts the widened shape. Reuse the file's existing builders for `Situation`/`DiagnosedSituation`/`RemediationOutcome` where present:

```python
def test_projection_keeps_evidence_and_joins_outcome():
    from datetime import UTC, datetime

    from common.contracts import (
        DiagnosedSituation, RemediationOutcome, RemediationResult,
        RootCauseHypothesis, Situation, SituationStatus, TelemetryEvent, TelemetryKind,
    )
    from services.read.projection import ReadModel

    ts = datetime(2026, 1, 1, tzinfo=UTC)
    ev = TelemetryEvent(source="prom", kind=TelemetryKind.METRIC, name="cpu_usage",
                        value=92.0, labels={"service": "web"}, ts=ts, fingerprint="fp")
    sit = Situation(id="sit-1", status=SituationStatus.DETECTED, member_events=[ev],
                    severity="high", first_seen=ts, last_seen=ts, signature="1",
                    peak_score=6.3, baseline={"cpu_usage": {"mean": 18.0, "std": 2.0}})
    hyp = RootCauseHypothesis(situation_id="sit-1", description="resource saturation",
                              confidence=0.6, evidence=["metrics: cpu_usage"],
                              suggested_runbook_id="scale-service", explanation="Likely cause: CPU.")
    m = ReadModel()
    m.apply_detected(sit)
    m.apply_diagnosed(DiagnosedSituation(situation=sit, hypotheses=[hyp], suggested_runbook_id="scale-service"))
    m.apply_outcome(RemediationOutcome(situation_id="sit-1", playbook_id="scale-service",
                    result=RemediationResult.SUCCESS, health_after="healthy", ts=ts,
                    steps=["scale web +2 replicas"], mode="dry_run"))

    s = next(x for x in m.situations() if x["id"] == "sit-1")
    assert s["member_events"][0]["name"] == "cpu_usage"
    assert s["member_events"][0]["value"] == 92.0
    assert s["hypotheses"][0]["evidence"] == ["metrics: cpu_usage"]
    assert s["hypotheses"][0]["explanation"] == "Likely cause: CPU."
    assert s["peak_score"] == 6.3
    assert s["baseline"]["cpu_usage"]["mean"] == 18.0
    assert "resource saturation" in s["title"].lower()
    assert s["outcome"]["health_after"] == "healthy"
    assert s["outcome"]["steps"] == ["scale web +2 replicas"]
    assert s["stages"]["detected"] is not None
    assert s["stages"]["resolved"] is not None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest services/read/tests/test_projection.py::test_projection_keeps_evidence_and_joins_outcome -v`
Expected: FAIL (keys missing).

- [ ] **Step 3: Add a bounded member-events projector + a title deriver**

At the top of `projection.py` (after `_epoch_ms`, line 34), add:

```python
_MAX_MEMBER_EVENTS = 20


def _project_events(s: Situation) -> list[dict]:
    out: list[dict] = []
    for ev in s.member_events[:_MAX_MEMBER_EVENTS]:
        out.append(
            {
                "name": ev.name,
                "value": ev.value,
                "labels": dict(ev.labels),
                "kind": ev.kind.value if hasattr(ev.kind, "value") else str(ev.kind),
                "ts": _epoch_ms(ev.ts),
            }
        )
    return out
```

- [ ] **Step 4: Widen `apply_detected` — member_events, peak_score, baseline, stages, readable title fallback**

In `apply_detected` (lines 89-109), add the new keys to the stored dict (keep every existing key). Insert after `"memberCount": len(s.member_events),`:

```python
            "member_events": _project_events(s),
            "peak_score": s.peak_score,
            "baseline": s.baseline,
```

Change the `"title"` line (95/96) so it stays the signature for now but will be overwritten on diagnose (title needs the top hypothesis). Keep `"title": s.signature,` here as the pre-diagnosis fallback. After the dict literal, before `self.publish(...)`, add stage tracking:

```python
        stages = self._sits[s.id].get("stages", {})
        stages.setdefault("detected", _epoch_ms(s.first_seen))
        self._sits[s.id]["stages"] = stages
```

(Move the `stages` read to use `existing.get("stages", {})` merged into the dict — simplest: add `"stages": existing.get("stages", {}),` inside the dict literal, then the block above sets `detected`.)

- [ ] **Step 5: Widen `apply_diagnosed` — evidence + explanation + readable title + diagnosed stage**

In `apply_diagnosed` (lines 115-131), change the hypotheses projection (lines 120-127) to include evidence + explanation, derive a readable title, and stamp the stage:

```python
    def apply_diagnosed(self, d: DiagnosedSituation) -> None:
        self.apply_detected(d.situation)
        hyps = [
            {
                "description": h.description,
                "confidence": h.confidence,
                "suggested_runbook_id": h.suggested_runbook_id,
                "evidence": list(h.evidence),
                "explanation": h.explanation,
            }
            for h in d.hypotheses
        ]
        service = self._sits[d.situation.id].get("service", "unknown")
        title = f"{hyps[0]['description']} · {service}" if hyps else d.situation.signature
        stages = self._sits[d.situation.id].get("stages", {})
        stages["diagnosed"] = _epoch_ms(d.situation.last_seen)
        self._sits[d.situation.id].update(
            {
                "status": "diagnosed",
                "hypotheses": hyps,
                "suggested_runbook_id": d.suggested_runbook_id,
                "title": title,
                "stages": stages,
            }
        )
        self.publish({"type": "changed"})
```

- [ ] **Step 6: Join the real outcome onto the situation in `apply_outcome`**

In `apply_outcome` (lines 133-158), after updating status/last_activity (lines 134-136), store the outcome ON the situation and stamp the terminal stage. Insert after line 136:

```python
        if o.situation_id in self._sits:
            terminal = _RESULT_STATUS.get(o.result, "failed")
            stages = self._sits[o.situation_id].get("stages", {})
            stages[terminal] = _epoch_ms(o.ts)
            self._sits[o.situation_id]["stages"] = stages
            self._sits[o.situation_id]["outcome"] = {
                "result": o.result.value if isinstance(o.result, RemediationResult) else str(o.result),
                "health_after": o.health_after,
                "mode": getattr(o, "mode", "dry_run"),
                "steps": list(getattr(o, "steps", [])),
            }
```

(Leave the existing `_outcomes` global list append intact — the Overview ticker still reads it.)

- [ ] **Step 7: Run the test + the read suite**

Run: `uv run pytest services/read/tests/ -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add services/read/projection.py services/read/tests/test_projection.py
git commit -m "feat(read): widen projection — keep member_events, evidence, explanation, peak_score, timeline, joined outcome"
```

---

### Task 9: New read endpoints — `GET /situations/{id}` and `GET /system`; correlation `GET /baseline`

**Files:**
- Modify: `services/read/projection.py` (add a `situation(id)` accessor), `services/read/app.py` (add `/situations/{id}`, `/system`)
- Modify: `services/correlation/app.py` (add `GET /baseline`)
- Test: `services/read/tests/test_read_api.py` (extend), `services/correlation/tests/` (new small test)

**Interfaces:**
- Consumes: the widened projection (Task 8), `get_settings()` (existing), the correlation engine's `snapshot()`/`_correlator` (existing).
- Produces:
  - `GET /situations/{id}` → the full widened situation dict, or 404.
  - `GET /system` → `{correlator_kind, llm: {provider, endpoint (redacted), model, last_probe}, bus_backend, store_backend, remediator_mode, auth_mode}`.
  - correlation `GET /baseline` → `{correlator_kind, baselines: [{metric_name, mean, std, count}]}`.

- [ ] **Step 1: Add a `situation(id)` accessor to ReadModel**

In `services/read/projection.py`, after `outcomes()` (line 193), add:

```python
    def situation(self, sid: str) -> dict | None:
        s = self._sits.get(sid)
        return dict(s) if s is not None else None
```

- [ ] **Step 2: Write the failing API test for `/situations/{id}` and `/system`**

In `services/read/tests/test_read_api.py`, following the file's existing TestClient pattern, add:

```python
def test_situation_detail_and_system_endpoints(client):  # reuse the file's client fixture
    r = client.get("/situations/does-not-exist")
    assert r.status_code == 404
    r = client.get("/system")
    assert r.status_code == 200
    body = r.json()
    assert "correlator_kind" in body
    assert "llm" in body and "provider" in body["llm"]
    assert body["llm"]["endpoint_configured"] in (True, False)
```

If the existing tests don't expose a `client` fixture, construct `TestClient(app)` the same way the neighboring tests in the file do (copy their setup).

- [ ] **Step 3: Run it to verify it fails**

Run: `uv run pytest services/read/tests/test_read_api.py::test_situation_detail_and_system_endpoints -v`
Expected: FAIL (404 route missing / `/system` missing).

- [ ] **Step 4: Add the read endpoints**

In `services/read/app.py`, after the `/outcomes` route (line 59), add:

```python
@app.get("/situations/{sid}")
def situation_detail(sid: str) -> dict:
    model = getattr(app.state, "model", None)
    detail = model.situation(sid) if model else None
    if detail is None:
        raise HTTPException(status_code=404, detail="situation not found")
    return detail


@app.get("/system")
def system() -> dict:
    settings = get_settings()
    endpoint = settings.llm_explanation_endpoint
    return {
        "correlator_kind": settings.correlator_kind,
        "bus_backend": settings.bus_backend,
        "store_backend": settings.store_backend,
        "remediator_mode": settings.remediator_mode,
        "auth_mode": settings.auth_mode,
        "llm": {
            "provider": "openai-compatible" if endpoint else "template",
            "endpoint_configured": bool(endpoint),
            "endpoint": _redact_endpoint(endpoint),
            "model": settings.llm_explanation_model,
        },
    }
```

And a redaction helper near the top of the file (after the imports):

```python
def _redact_endpoint(endpoint: str) -> str:
    """Show the host but never any embedded credential."""
    if not endpoint:
        return ""
    try:
        from urllib.parse import urlparse

        p = urlparse(endpoint)
        return f"{p.scheme}://{p.hostname}" + (f":{p.port}" if p.port else "")
    except Exception:  # noqa: BLE001
        return "configured"
```

(`get_settings` is already imported at line 15; `HTTPException` already imported line 12.)

Note: `/system` reports config-file LLM state. Task 11 will make it reflect the *live* provider set from the UI; for now it reflects `settings`.

- [ ] **Step 5: Add correlation `GET /baseline`**

In `services/correlation/app.py`, after `/retrain` (line 212), add:

```python
@app.get("/baseline")
def baseline() -> dict:
    settings = get_settings()
    engine = getattr(app.state, "engine", None)
    rows = engine.snapshot() if engine is not None else []
    baselines = [
        {
            "metric_name": r.get("metric_name"),
            "mean": r.get("mean"),
            "std": (r.get("variance") or 0.0) ** 0.5,
            "count": r.get("count"),
        }
        for r in rows
    ]
    return {"correlator_kind": settings.correlator_kind, "baselines": baselines}
```

(`get_settings` imported line 13; `engine.snapshot()` exists, returns the scalar rows — robust/trained return their own snapshot shape; guard with `.get()` so a missing `variance` key yields std 0 rather than KeyError.)

- [ ] **Step 6: Add a correlation baseline test**

Create `services/correlation/tests/test_baseline_endpoint.py`:

```python
from fastapi.testclient import TestClient

from services.correlation.app import app


def test_baseline_endpoint_shape():
    with TestClient(app) as client:
        r = client.get("/baseline")
        assert r.status_code == 200
        body = r.json()
        assert "correlator_kind" in body
        assert isinstance(body["baselines"], list)
```

- [ ] **Step 7: Run both suites**

Run: `uv run pytest services/read/tests/test_read_api.py services/correlation/tests/test_baseline_endpoint.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add services/read/projection.py services/read/app.py services/correlation/app.py services/read/tests/test_read_api.py services/correlation/tests/test_baseline_endpoint.py
git commit -m "feat(read+correlation): GET /situations/{id}, GET /system, GET /baseline for the debug view"
```

---

## STAGE 3 — Show the evidence + live-configurable LLM (frontend + rca endpoint).

### Task 10: Incident detail drill-down + frontend loaders/types for the new endpoints

**Files:**
- Modify: `frontend/src/data/types.ts` (add `SituationDetail`, `MemberEvent`, `SystemInfo` types + extend `Hypothesis` with `evidence`/`explanation`; extend `Situation` with `peak_score`, `baseline`, `member_events`, `stages`)
- Modify: `frontend/src/data/api.ts` + `source.ts` + `mock.ts` (add `loadSituationDetail(id)`, `loadSystem()`)
- Modify: `frontend/src/views/Incidents.tsx` (render evidence + explanation + peak_score + timeline in the detail panel)
- Test: none (verified by `npm run build` + live Task 12)

**Interfaces:**
- Consumes: `GET /situations/{id}`, `GET /system` (Task 9).
- Produces: an Incidents detail panel showing the member events, the z-score line, each hypothesis's evidence + labeled explanation, and the timeline.

- [ ] **Step 1: Extend the frontend types**

In `frontend/src/data/types.ts`:

Add `evidence`/`explanation` to `Hypothesis` (after line 35 `suggested_runbook_id`):

```ts
  evidence?: string[];
  explanation?: string | null;
```

Add to `Situation` (after `outcome?` from Task 1):

```ts
  peak_score?: number | null;
  baseline?: Record<string, { mean: number; std: number }> | null;
  member_events?: MemberEvent[];
  stages?: Partial<Record<"detected" | "diagnosed" | "acting" | "resolved" | "failed", number>>;
```

Add the new interfaces at the end of the file:

```ts
export interface MemberEvent {
  name: string;
  value: number | null;
  labels: Record<string, string>;
  kind: string;
  ts: number;
}

export interface SystemInfo {
  correlator_kind: string;
  bus_backend: string;
  store_backend: string;
  remediator_mode: string;
  auth_mode: string;
  llm: {
    provider: "template" | "openai-compatible";
    endpoint_configured: boolean;
    endpoint: string;
    model: string;
    last_probe?: { ok: boolean; latency_ms?: number; error?: string } | null;
  };
}
```

- [ ] **Step 2: Add the loaders**

In `frontend/src/data/api.ts`, after `loadSituations` (line 18):

```ts
export const loadSituationDetail = (id: string) => getJSON<Situation>(`${READ}/situations/${id}`);
export const loadSystem = () => getJSON<SystemInfo>(`${READ}/system`);
```

Import `SystemInfo` in the type import at line 1. In `frontend/src/data/source.ts`, wire both through the LIVE/mock switch (mock returns the selected situation from `mock.situations` and a static `SystemInfo`):

```ts
export const loadSituationDetail = LIVE
  ? api.loadSituationDetail
  : async (id: string) => mock.situations.find((s) => s.id === id) ?? mock.situations[0];
export const loadSystem = LIVE
  ? api.loadSystem
  : async () => mock.system;
```

Add a `system` export to `frontend/src/data/mock.ts`:

```ts
export const system: SystemInfo = {
  correlator_kind: "river",
  bus_backend: "redis",
  store_backend: "file",
  remediator_mode: "dry_run",
  auth_mode: "off",
  llm: { provider: "template", endpoint_configured: false, endpoint: "", model: "gpt-4o-mini", last_probe: null },
};
```

(Import `SystemInfo` in `mock.ts`'s type import.)

- [ ] **Step 3: Render evidence + explanation + z-score + timeline in the detail panel**

In `frontend/src/views/Incidents.tsx`, in the hypotheses block (lines 199-212), after the confidence bar row, render evidence and (for the top hypothesis) the labeled explanation:

```tsx
                        {h.evidence && h.evidence.length > 0 && (
                          <ul className="mt-2 space-y-0.5">
                            {h.evidence.map((e, j) => (
                              <li key={j} className="font-mono text-2xs text-ink-3">• {e}</li>
                            ))}
                          </ul>
                        )}
                        {i === 0 && h.explanation && (
                          <div className="mt-2 rounded-lg bg-black/[0.03] p-2 text-2xs leading-relaxed text-ink-2">
                            <span className="font-mono text-ink-3">{sel.baseline ? "AI/template explanation" : "explanation"}: </span>
                            {h.explanation}
                          </div>
                        )}
```

After the pipeline rail (before the hypotheses block, ~line 191), add a member-events + z-score panel:

```tsx
                {sel.member_events && sel.member_events.length > 0 && (
                  <div className="mt-5">
                    <div className="mb-2 text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">What broke — the signal</div>
                    <div className="space-y-1">
                      {sel.member_events.slice(0, 6).map((ev, i) => {
                        const b = sel.baseline?.[ev.name];
                        return (
                          <div key={i} className="flex items-center gap-3 rounded-lg bg-black/[0.02] px-3 py-1.5 font-mono text-2xs">
                            <span className="text-ink">{ev.name}</span>
                            <span className="text-signal-dim">{ev.value ?? "—"}</span>
                            {b && <span className="text-ink-3">vs baseline {b.mean.toFixed(1)}±{b.std.toFixed(1)}</span>}
                            {sel.peak_score != null && i === 0 && <span className="ml-auto text-sev-warn">z ≈ {sel.peak_score.toFixed(1)}</span>}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
```

Then wire `Incidents` to fetch the full detail for the selected situation so `member_events`/`baseline`/`evidence`/`explanation` are present (the list endpoint returns them too now, but the detail fetch keeps the panel authoritative). Add, after the `sel` memo (line 51):

```tsx
  const { data: detail } = useLiveData(
    useMemo(() => () => (selId ? loadSituationDetail(selId) : Promise.resolve(null as Situation | null)), [selId]),
    null as Situation | null,
  );
  const shown = detail && detail.id === selId ? { ...sel, ...detail, ...overrides[selId] } : sel;
```

Then replace `sel` with `shown` in the detail-panel JSX (the header/pipeline/hypotheses/gate blocks, lines 147-264) — keep `sel` for the list. Import `loadSituationDetail` from `../data/source` (line 16).

- [ ] **Step 4: Verify the build compiles**

Run: `npm --prefix frontend run build`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/data/types.ts frontend/src/data/api.ts frontend/src/data/source.ts frontend/src/data/mock.ts frontend/src/views/Incidents.tsx
git commit -m "feat(ui): incident drill-down — member events, z-score, evidence, labeled explanation, timeline"
```

---

### Task 11: Live LLM config on the RCA service — `POST /config/llm` + `POST /config/llm/test`, reflected in `GET /system`

**Files:**
- Modify: `services/rca/app.py` (module-level provider holder behind a lock; `POST /config/llm`, `POST /config/llm/test`; expose current state)
- Modify: `services/rca/consumer.py:30-51` (read the provider from a live holder, not a captured arg) OR pass a holder — see Step 3
- Modify: `services/read/app.py` `/system` (report the live provider by querying rca) — OR have rca own `/system/llm` and the UI read it; simplest: rca exposes `GET /config/llm` returning current state, and the console badge reads it
- Test: `services/rca/tests/test_llm_config.py` (new)

**Interfaces:**
- Consumes: `make_explanation_provider(settings)` (existing), `OpenAICompatibleExplanationProvider` (existing).
- Produces:
  - `POST /config/llm` (auth-gated) `{endpoint, api_key, model}` → rebuilds the running provider in place; returns `{provider, endpoint (redacted), model}` — never echoes the key.
  - `POST /config/llm/test` `{endpoint, api_key, model}` → makes a real probe call; returns `{ok, model, latency_ms, error?}`.
  - `GET /config/llm` → current `{provider, endpoint_configured, endpoint (redacted), model, last_probe}`.

**Design note (concurrency):** the RCA consumer runs in a daemon thread and calls `explainer.explain(...)` per diagnosis. To swap the provider live, hold it in a small thread-safe holder the consumer reads each iteration, guarded by a lock. The swap replaces the holder's provider; the consumer picks it up on the next situation.

- [ ] **Step 1: Add a thread-safe provider holder**

Create `services/rca/provider_holder.py`:

```python
"""A thread-safe holder for the live ExplanationProvider so the RCA consumer
(a daemon thread) and the /config/llm route (the request thread) can swap it
without a restart. The consumer reads .get() each iteration; the route calls
.set() to install a freshly built provider."""

from __future__ import annotations

import threading


class ProviderHolder:
    def __init__(self, provider) -> None:
        self._provider = provider
        self._lock = threading.Lock()
        self._last_probe: dict | None = None

    def get(self):
        with self._lock:
            return self._provider

    def set(self, provider) -> None:
        with self._lock:
            self._provider = provider

    def set_last_probe(self, probe: dict) -> None:
        with self._lock:
            self._last_probe = probe

    @property
    def last_probe(self) -> dict | None:
        with self._lock:
            return self._last_probe
```

- [ ] **Step 2: Make the consumer read the live provider each iteration**

In `services/rca/consumer.py`, change `run_consumer` to accept a holder (or a callable) instead of a fixed `explainer`, and read it per situation. Change the signature and the `diagnose` call so `explainer` is fetched fresh:

In `run_consumer` (lines 54-77), replace the `explainer` parameter usage: accept `explainer_source` (a zero-arg callable returning the current provider). Inside the loop, call `diagnose(situation, provider, store, audit_sink=..., explainer=explainer_source(), ...)`. Keep `diagnose`'s signature unchanged (it still takes a concrete `explainer`). Concretely:

```python
def run_consumer(
    bus,
    provider: ContextProvider,
    store: PlaybookStore,
    audit_sink: AuditSink,
    explainer_source,  # zero-arg callable -> ExplanationProvider (live-swappable)
    stop_event: threading.Event,
    reliability_provider=None,
) -> None:
    for situation in iter_models(bus, "situations.detected", "rca", Situation):
        if stop_event.is_set():
            break
        diagnosed = diagnose(situation, provider, store, explainer_source(), reliability_provider)
        publish_model(bus, "situations.diagnosed", diagnosed)
        audit_sink.write(
            AuditRecord(
                actor="rca-service",
                action="diagnose",
                resource=f"situation:{situation.id}",
                decision="allow",
                ts=datetime.now(UTC),
                correlation_id=situation.id,
            )
        )
```

Update the existing `services/rca/tests/test_consumer.py` call sites. Both calls pass `TemplateExplanationProvider()` as the **5th positional arg** to `run_consumer(bus, provider, store, audit, TemplateExplanationProvider(), stop)` (confirmed at `test_consumer.py:69-76` and `:102-109`). Change each to pass a callable in that position: `run_consumer(bus, provider, store, audit, lambda: TemplateExplanationProvider(), stop)`. This is the ONLY existing test that changes — keep it mechanical (two edits).

- [ ] **Step 3: Wire the holder in `app.py` and add the routes**

In `services/rca/app.py`, build a holder from the initial provider and pass `explainer_source=holder.get` to the consumer. In `lifespan` (line 55), replace `explainer = make_explanation_provider(settings)` with:

```python
    from services.rca.provider_holder import ProviderHolder

    holder = ProviderHolder(make_explanation_provider(settings))
    app.state.provider_holder = holder
```

and change the thread args to pass `holder.get`:

```python
    thread = threading.Thread(
        target=run_consumer,
        args=(app.state.bus, provider, store, audit_sink, holder.get, stop_event),
        kwargs={"reliability_provider": reliability_provider},
        daemon=True,
    )
```

After `app.router.lifespan_context = lifespan` (line 78), add the routes (import `BaseModel`, `time`, `OpenAICompatibleExplanationProvider`, `TemplateExplanationProvider`, `_redact` helper):

```python
class LlmConfig(BaseModel):
    endpoint: str = ""
    api_key: str = ""
    model: str = "gpt-4o-mini"
    timeout_seconds: float = 10.0


def _redact(endpoint: str) -> str:
    if not endpoint:
        return ""
    from urllib.parse import urlparse

    p = urlparse(endpoint)
    return f"{p.scheme}://{p.hostname}" + (f":{p.port}" if p.port else "")


def _state(holder) -> dict:
    prov = holder.get()
    is_llm = isinstance(prov, OpenAICompatibleExplanationProvider)
    return {
        "provider": "openai-compatible" if is_llm else "template",
        "endpoint_configured": is_llm,
        "endpoint": _redact(getattr(prov, "_base", "")),
        "model": getattr(prov, "_model", get_settings().llm_explanation_model),
        "last_probe": holder.last_probe,
    }


@app.get("/config/llm")
def get_llm_config() -> dict:
    return _state(app.state.provider_holder)


@app.post("/config/llm")
def set_llm_config(cfg: LlmConfig) -> dict:
    holder = app.state.provider_holder
    if cfg.endpoint:
        holder.set(
            OpenAICompatibleExplanationProvider(
                base_url=cfg.endpoint, model=cfg.model,
                api_key=cfg.api_key, timeout_seconds=cfg.timeout_seconds,
            )
        )
    else:
        holder.set(TemplateExplanationProvider())
    return _state(holder)  # never echoes api_key


@app.post("/config/llm/test")
def test_llm_config(cfg: LlmConfig) -> dict:
    import time

    from common.contracts import EnrichmentContext, RootCauseHypothesis, Situation, SituationStatus
    from datetime import UTC, datetime

    if not cfg.endpoint:
        return {"ok": False, "error": "no endpoint configured"}
    provider = OpenAICompatibleExplanationProvider(
        base_url=cfg.endpoint, model=cfg.model, api_key=cfg.api_key, timeout_seconds=cfg.timeout_seconds,
    )
    hyp = RootCauseHypothesis(situation_id="probe", description="probe", confidence=0.5, evidence=["probe"])
    sit = Situation(id="probe", status=SituationStatus.DETECTED, severity="low",
                    first_seen=datetime.now(UTC), last_seen=datetime.now(UTC), signature="probe")
    template = TemplateExplanationProvider().explain(hyp, EnrichmentContext(), sit)
    start = time.monotonic()
    text = provider.explain(hyp, EnrichmentContext(), sit)
    latency_ms = int((time.monotonic() - start) * 1000)
    ok = text != template  # provider falls back to template on ANY failure
    probe = {"ok": ok, "model": cfg.model, "latency_ms": latency_ms}
    if not ok:
        probe["error"] = "endpoint unreachable or returned no usable content (fell back to template)"
    app.state.provider_holder.set_last_probe(probe)
    return probe
```

Add imports at the top of `app.py`: `from pydantic import BaseModel`, and `from services.rca.adapters.explanation_provider import (OpenAICompatibleExplanationProvider, TemplateExplanationProvider, make_explanation_provider)`.

**Auth:** `create_app` gates every non-exempt route behind `AUTH_MODE=token` already (`services/base.py:66-75`), and RCA uses the default exempt predicate (only `/health`+`/ready`). So `POST /config/llm` is auth-gated automatically — no extra work. Add a one-line comment in the route noting this.

- [ ] **Step 4: Write the test**

Create `services/rca/tests/test_llm_config.py`:

```python
from fastapi.testclient import TestClient

from services.rca.app import app


def test_config_llm_swaps_provider_and_never_echoes_key():
    with TestClient(app) as client:
        # default: template
        r = client.get("/config/llm")
        assert r.json()["provider"] == "template"
        # set an (unreachable) endpoint → provider becomes openai-compatible
        r = client.post("/config/llm", json={"endpoint": "http://127.0.0.1:1/v1", "api_key": "secret-key", "model": "gpt-4o-mini"})
        body = r.json()
        assert body["provider"] == "openai-compatible"
        assert "secret-key" not in str(body)  # key never echoed
        # test-connection against the dead endpoint → ok False, falls back
        r = client.post("/config/llm/test", json={"endpoint": "http://127.0.0.1:1/v1", "api_key": "k", "model": "gpt-4o-mini"})
        assert r.json()["ok"] is False
        # clear → back to template
        r = client.post("/config/llm", json={"endpoint": "", "api_key": "", "model": "gpt-4o-mini"})
        assert r.json()["provider"] == "template"
```

- [ ] **Step 5: Run the rca suite**

Run: `uv run pytest services/rca/tests/ -q`
Expected: PASS (including the adjusted `test_consumer.py` call sites).

- [ ] **Step 6: Commit**

```bash
git add services/rca/provider_holder.py services/rca/app.py services/rca/consumer.py services/rca/tests/test_llm_config.py services/rca/tests/test_consumer.py
git commit -m "feat(rca): live-configurable LLM — POST /config/llm swaps the running provider, /config/llm/test probes, never echoes key"
```

---

### Task 12: System view + LLM settings panel in the console (the "under the hood" UI)

**Files:**
- Create: `frontend/src/views/System.tsx` (the debug/provenance view + LLM settings panel)
- Modify: `frontend/src/data/api.ts` + `source.ts` + `mock.ts` (add `loadBaseline()`, `setLlmConfig()`, `testLlmConfig()`, `loadLlmConfig()`)
- Modify: `frontend/src/data/types.ts` (add `BaselineInfo`, `LlmProbe` types)
- Modify: `frontend/src/components/Shell.tsx` (add a "System" nav entry) — follow the existing nav pattern
- Test: none (verified by `npm run build` + live run)

**Interfaces:**
- Consumes: `GET /system`, correlation `GET /baseline`, rca `GET/POST /config/llm`, `POST /config/llm/test` (Tasks 9, 11).
- Produces: a System view showing correlator kind, live baselines, backends, remediation mode, and an LLM settings panel with a test-connection button + a truthful provider badge.

- [ ] **Step 1: Add types + loaders**

In `frontend/src/data/types.ts`:

```ts
export interface BaselineInfo {
  correlator_kind: string;
  baselines: { metric_name: string; mean: number; std: number; count: number }[];
}

export interface LlmProbe {
  ok: boolean;
  model?: string;
  latency_ms?: number;
  error?: string;
}
```

In `frontend/src/data/api.ts` (add `CORR` base + calls). Add near the top:

```ts
const CORR = import.meta.env.VITE_CORR_URL ?? "http://localhost:8002";
const RCA = import.meta.env.VITE_RCA_URL ?? "http://localhost:8003";
```

and the calls:

```ts
export const loadBaseline = () => getJSON<BaselineInfo>(`${CORR}/baseline`);
export const loadLlmConfig = () => getJSON<SystemInfo["llm"]>(`${RCA}/config/llm`);

async function postJSON<T>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, {
    method: "POST",
    headers: authHeaders({ "content-type": "application/json" }),
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return (await r.json()) as T;
}

export const setLlmConfig = (cfg: { endpoint: string; api_key: string; model: string }) =>
  postJSON<SystemInfo["llm"]>(`${RCA}/config/llm`, cfg);
export const testLlmConfig = (cfg: { endpoint: string; api_key: string; model: string }) =>
  postJSON<LlmProbe>(`${RCA}/config/llm/test`, cfg);
```

Wire mock fallbacks in `source.ts` (baseline → a static `BaselineInfo`; config setters → no-op returning `mock.system.llm`; test → `{ok:false, error:"mock mode"}`). Add `baseline` + these to `mock.ts`.

**Note (RCA reachability from the browser):** the console today only knows `READ`/`GOV` URLs. RCA runs on 8003 and correlation on 8002; expose them to the console via `VITE_RCA_URL`/`VITE_CORR_URL` in `frontend/.env.local`, and ensure both services' CORS allows the console origin (they use the shared `cors_origins` setting — already `http://localhost:5173`). Document this in Task 13.

- [ ] **Step 2: Build the System view**

Create `frontend/src/views/System.tsx` — a view with three regions: (1) a system-state grid (correlator kind, bus/store backend, remediation mode, auth mode) from `loadSystem`; (2) a live baselines table from `loadBaseline`; (3) an LLM settings card: three inputs (endpoint, api_key [type=password], model), a "Test connection" button that calls `testLlmConfig` and shows `{ok, latency_ms, error}`, a "Save" button that calls `setLlmConfig`, and a provider badge reading `loadLlmConfig` — **"LLM: connected (model)"** (green) when `provider === "openai-compatible"` and `last_probe.ok`, **"Template (no endpoint set)"** (neutral) otherwise, **"LLM error → template fallback"** (amber) when configured but `last_probe.ok === false`. Follow the Double-Bezel + Apple-light patterns already in `primitives.tsx`/other views. Never display the api_key back (the response never contains it). Keep the key in component state only to send it.

- [ ] **Step 3: Add the nav entry**

In `frontend/src/components/Shell.tsx`, add a "System" (or "Under the hood") route/nav item following the existing pattern for Overview/Incidents/Pipeline/Governance/Audit. Route it to `<System />`.

- [ ] **Step 4: Verify the build compiles**

Run: `npm --prefix frontend run build`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/System.tsx frontend/src/data/api.ts frontend/src/data/source.ts frontend/src/data/mock.ts frontend/src/data/types.ts frontend/src/components/Shell.tsx
git commit -m "feat(ui): System view — live baselines, backends, remediation mode, and LLM settings with test-connection"
```

---

### Task 13: Compose env examples + docs (honest defaults) + ADR-021

**Files:**
- Modify: `docker-compose.yml` (commented-out `llm_explanation_*` env examples on the rca service)
- Modify: `frontend/.env.local` or `.env.example` (add `VITE_CORR_URL`, `VITE_RCA_URL` examples)
- Modify: `architectural.md` (append `### ADR-021 — Evidence exposure & honesty pass` — ADRs live as `### ADR-0xx — Title` sections in this ONE file; latest is ADR-020, NOT a separate `docs/architecture/adr/` directory)
- Modify: `README.md` (a short "Proving it's real" section pointing at the System view + drill-down; note the default LLM is the offline template)
- Test: none (docs)

- [ ] **Step 1: Add commented LLM env examples to compose**

In `docker-compose.yml`, on the `rca` service's environment block, add commented examples (keep them commented so the default stays the offline template — honest):

```yaml
      # --- Optional: enable the LLM explanation provider (default is the offline template) ---
      # INTELLIOPS_LLM_EXPLANATION_ENDPOINT: "https://api.openai.com/v1"
      # INTELLIOPS_LLM_EXPLANATION_MODEL: "gpt-4o-mini"
      # INTELLIOPS_LLM_EXPLANATION_API_KEY: "sk-..."
      # Or configure it live from the console's System view — no restart needed.
```

- [ ] **Step 2: Add the console env examples**

In `frontend/.env.local` (and `.env.example` if present), add:

```
VITE_CORR_URL=http://localhost:8002
VITE_RCA_URL=http://localhost:8003
```

- [ ] **Step 3: Write ADR-021**

Append `### ADR-021 — Evidence exposure & honesty pass` to `architectural.md` (after the ADR-020 section, ~line 1030), matching the house format exactly: `**Context.**` / `**Decision.**` / `**Why.**` prose sections with cross-references like `[ADR-012](#adr-012--...)`. Record: the finding that the engine was real but the read-model *projection* dropped every rich signal at its boundary; the decision to widen the projection rather than change engine logic; the additive-contract discipline (new optional fields, test-safe defaults — consistent with [ADR-006](#adr-006) frozen contracts and [ADR-012](#adr-012--config-switched-adapter-selection-with-test-safe-defaults)); the new introspection endpoints (`GET /situations/{id}`, `GET /system`, correlation `GET /baseline`, rca `POST /config/llm` + `/config/llm/test`); the live-LLM-swap design (a `ProviderHolder` behind a lock so the daemon consumer and the request thread share a swappable provider, no restart) and its auth-gating (never echo the key); the two UI honesty bugs fixed (reappearing approve gate, hardcoded outcome); and the honesty rule (no fabricated numbers in live mode; dry-run labeled).

- [ ] **Step 4: Add the README "Proving it's real" note**

In `README.md`, add a short section describing: open an incident → drill-down shows the metric+value, z-score vs baseline, ranked hypotheses with evidence + the (labeled) explanation, the timeline, and the real outcome + steps (dry-run labeled); open the System view → live correlator baselines + backends + the LLM settings/badge; the LLM default is the offline template and can be turned on live from the UI.

- [ ] **Step 5: Verify docs render + suite still green**

Run: `uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check . && npm --prefix frontend run build`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml frontend/.env.local docs/architecture/adr/ADR-021-evidence-exposure-and-honesty.md README.md
git commit -m "docs: ADR-021 evidence exposure + honest LLM defaults + console env examples"
```

---

## Self-Review checklist (run after execution, before the PR)

1. **Approve gate:** approve → gate yields to real server outcome; buttons don't reappear (Task 2). Reject likewise. No hardcoded outcome text (Task 3).
2. **Readable narrative per incident:** metric+value, z-score, evidence, labeled explanation, timeline, real outcome + steps (dry-run labeled) — Tasks 6-10.
3. **"Under the hood" visible:** System view shows correlator, baselines, backends, remediation mode, LLM badge — Tasks 9, 12.
4. **LLM provable AND live-configurable:** Settings panel sets endpoint+key+model; `POST /config/llm` swaps the running provider (no restart); test-connection probes; badge truthful; key never echoed — Tasks 11-12.
5. **No fabricated numbers in live mode:** deltas/captions removed/made-real (Task 4); every headline stat traceable.
6. **Test-safe:** existing 414 tests green; new fields additive; mock mode works; ruff/build clean — enforced at every task's gate.
