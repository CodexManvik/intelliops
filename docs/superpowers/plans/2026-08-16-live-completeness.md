# Live Completeness (Tier 1 + 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the running live stack fully real (live Overview KPIs, visible UI errors, derived service names) and repeatably runnable for simulations (scenario reset, bounded situation retention).

**Architecture:** The read-model is the lifecycle-aware service — it already sees detection (`first_seen`), diagnosis, resolution (`outcome.ts`), and outcomes, so it computes all live KPIs and owns situation retention. `RemediationOutcome` gains a defaulted `hitl_mode` so auto-vs-HITL is truthful. Correlation publishes a `situations.suppressed` signal so suppression is visible. Per-service reset endpoints, composed by a shell script, give a one-command clean slate without a docker restart. All changes are additive with test-safe defaults.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, Redis Streams, React 18 + TypeScript + Vite + Tailwind + Framer Motion.

**Spec:** `docs/superpowers/specs/2026-08-16-live-completeness-design.md`

## Global Constraints

- Existing suite (203 tests) MUST stay green. Every change is additive; new settings/fields default to preserve current behavior.
- Contracts in `common/contracts.py` may gain ADDITIVE fields only (ADR-006); never change or remove existing field semantics. New fields must be defaulted so existing constructors keep working.
- All bus models use `common/envelope.py` helpers (`publish_model` / `iter_models`). Never hand-roll serialization.
- Background consumers use the lifespan + daemon-thread + `threading.Event` pattern (see `services/read/app.py`, `services/correlation/app.py`).
- The read-model is a pure, rebuildable projection: no wall-clock inside it — time is passed in as an epoch-ms parameter (tests pass fixed values).
- Frontend: strict TypeScript (`noUnusedLocals`/`noUnusedParameters` ON). `npm run build` from `frontend/` must pass. Match the existing design language (bezel, blur, `sev` palette, `ease-fluid` / custom cubic-bezier motion).
- Python: `uv run pytest`, `uv run ruff check`. Commit after each task; messages end with:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

## File Structure

**New:**
- `frontend/src/hooks/useToast.tsx` — toast state hook + `<ToastHost>` container.
- `scripts/reset.sh` — composes the per-service reset calls.

**Modified — backend:**
- `common/contracts.py` — `RemediationOutcome.hitl_mode` (defaulted).
- `services/action/remediate.py` — stamp `hitl_mode` on the outcome.
- `services/correlation/engine.py` — reset (correlator swap + buffer clear) + record suppressed situation for the consumer to publish.
- `services/correlation/consumer.py` — publish `situations.suppressed`.
- `services/correlation/app.py` — `POST /reset-baseline`.
- `services/read/projection.py` — `metrics()`, `_service_of` fix, `_prune()` age-out/cap, suppressed counter, auto-vs-hitl.
- `services/read/consumer.py` — consume `situations.suppressed`.
- `services/read/app.py` — `GET /metrics`, `POST /reset`.
- `common/config.py` — `read_situation_ttl_seconds` (600), `read_situations_max` (50).
- `README.md` — reset controls + simulation note.

**Modified — frontend:**
- `frontend/src/data/api.ts`, `source.ts` — `loadMetrics`.
- `frontend/src/views/Overview.tsx` — live metrics via `useData`.
- `frontend/src/views/Incidents.tsx` — toast on approve/reject; live-path status handling.
- `frontend/src/components/Shell.tsx` — mount `<ToastHost>`.

---

## Task 1: Add `hitl_mode` to RemediationOutcome + stamp it in action

**Files:**
- Modify: `common/contracts.py` (RemediationOutcome), `services/action/remediate.py` (_outcome helper)
- Test: `services/action/tests/test_remediate.py` (add one assertion), `tests/test_contracts_outcome.py` (Create)

**Interfaces:**
- Produces: `RemediationOutcome.hitl_mode: HitlMode = HitlMode.HITL` (defaulted, additive). `_outcome(...)` in remediate.py now sets `hitl_mode=playbook.hitl_mode`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_contracts_outcome.py
from datetime import UTC, datetime

from common.contracts import HitlMode, RemediationOutcome, RemediationResult


def test_outcome_defaults_hitl_mode():
    # existing constructors omit hitl_mode → must still work, defaulting to HITL
    o = RemediationOutcome(
        situation_id="s",
        playbook_id="p",
        result=RemediationResult.SUCCESS,
        health_after="healthy",
        ts=datetime.now(UTC),
    )
    assert o.hitl_mode == HitlMode.HITL


def test_outcome_accepts_hitl_mode():
    o = RemediationOutcome(
        situation_id="s",
        playbook_id="p",
        result=RemediationResult.SUCCESS,
        health_after="healthy",
        ts=datetime.now(UTC),
        hitl_mode=HitlMode.AUTO,
    )
    assert o.hitl_mode == HitlMode.AUTO
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_contracts_outcome.py -v`
Expected: FAIL — `RemediationOutcome` has no `hitl_mode`.

- [ ] **Step 3: Add the field**

In `common/contracts.py`, `RemediationOutcome` (currently ends at `ts: datetime`). `HitlMode` is already defined above in the same file. Add the field:

```python
class RemediationOutcome(BaseModel):
    situation_id: str
    playbook_id: str
    result: RemediationResult
    health_after: str
    ts: datetime
    hitl_mode: HitlMode = HitlMode.HITL
```

- [ ] **Step 4: Stamp it in action's `_outcome`**

In `services/action/remediate.py`, the `_outcome` helper builds the outcome. Update it to pass the playbook's mode:

```python
def _outcome(
    situation: Situation, playbook: Playbook, result: RemediationResult, health_after: str
) -> RemediationOutcome:
    return RemediationOutcome(
        situation_id=situation.id,
        playbook_id=playbook.id,
        result=result,
        health_after=health_after,
        ts=datetime.now(UTC),
        hitl_mode=playbook.hitl_mode,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_contracts_outcome.py services/action/tests/ -v`
Expected: PASS (new tests + existing action tests unaffected — they omit hitl_mode and get the default).

- [ ] **Step 6: Commit**

```bash
git add common/contracts.py services/action/remediate.py tests/test_contracts_outcome.py
git commit -m "feat(contracts): RemediationOutcome.hitl_mode (additive); action stamps it"
```

---

## Task 2: Read-model `_service_of` derivation fix

**Files:**
- Modify: `services/read/projection.py` (`_service_of`)
- Test: `services/read/tests/test_projection.py` (add cases)

**Interfaces:**
- Produces: `ReadModel._service_of(s)` returns first non-empty of `labels["service"]` → `labels["job"]` → `labels["instance"]` across member events, else `"unknown"`.

- [ ] **Step 1: Write the failing test**

```python
# add to services/read/tests/test_projection.py
from datetime import UTC, datetime
from common.contracts import Situation, SituationStatus, TelemetryEvent, TelemetryKind


def _sit_with_labels(labels):
    ev = TelemetryEvent(
        source="prometheus",
        kind=TelemetryKind.METRIC,
        name="cpu_usage",
        value=90.0,
        labels=labels,
        ts=datetime(2026, 8, 16, tzinfo=UTC),
        fingerprint="fp",
    )
    return Situation(
        id="sit-x",
        status=SituationStatus.DETECTED,
        member_events=[ev],
        severity="high",
        first_seen=datetime(2026, 8, 16, tzinfo=UTC),
        last_seen=datetime(2026, 8, 16, tzinfo=UTC),
        signature="x",
    )


def test_service_of_precedence_service():
    from services.read.projection import ReadModel

    assert (
        ReadModel._service_of(_sit_with_labels({"service": "web", "job": "j", "instance": "i"}))
        == "web"
    )


def test_service_of_precedence_job_then_instance():
    from services.read.projection import ReadModel

    assert ReadModel._service_of(_sit_with_labels({"job": "api"})) == "api"
    assert ReadModel._service_of(_sit_with_labels({"instance": "host:9100"})) == "host:9100"


def test_service_of_unknown_when_no_labels():
    from services.read.projection import ReadModel

    assert ReadModel._service_of(_sit_with_labels({})) == "unknown"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/read/tests/test_projection.py -k service_of -v`
Expected: FAIL on `test_service_of_unknown_when_no_labels` (currently returns `"demo-app"`).

- [ ] **Step 3: Fix the derivation**

In `services/read/projection.py`, replace `_service_of`:

```python
    @staticmethod
    def _service_of(s: Situation) -> str:
        for ev in s.member_events:
            for key in ("service", "job", "instance"):
                val = ev.labels.get(key)
                if val:
                    return val
        return "unknown"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest services/read/tests/test_projection.py -v`
Expected: PASS (new + existing projection tests).

- [ ] **Step 5: Commit**

```bash
git add services/read/projection.py services/read/tests/test_projection.py
git commit -m "fix(read): derive service from labels (service>job>instance>unknown)"
```

---

## Task 3: Read-model situation age-out + cap

**Files:**
- Modify: `common/config.py` (2 settings), `services/read/projection.py` (`_prune`, apply hooks, `situations()`)
- Test: `services/read/tests/test_pruning.py` (Create)

**Interfaces:**
- Consumes: `Settings.read_situation_ttl_seconds` (600), `Settings.read_situations_max` (50).
- Produces: `ReadModel(max_outcomes, ttl_seconds, max_situations)`; `apply_*(model, ..., now_ms=None)` — the apply methods gain an optional `now_ms` used for pruning; `situations(now_ms=None)` prunes then returns. When `now_ms` is None, pruning is skipped (so existing tests that call apply/situations without a clock are unaffected).

- [ ] **Step 1: Add settings**

In `common/config.py`, after `read_outcomes_max`:

```python
    read_situation_ttl_seconds: float = 600.0
    read_situations_max: int = 50
```

- [ ] **Step 2: Write the failing test**

```python
# services/read/tests/test_pruning.py
from datetime import UTC, datetime
from common.contracts import RemediationOutcome, RemediationResult, Situation, SituationStatus
from services.read.projection import ReadModel


def _sit(sid, status=SituationStatus.DETECTED, t=datetime(2026, 8, 16, tzinfo=UTC)):
    return Situation(
        id=sid,
        status=status,
        member_events=[],
        severity="high",
        first_seen=t,
        last_seen=t,
        signature=sid.replace("sit-", ""),
    )


MS = 1000


def test_terminal_old_situation_is_aged_out():
    rm = ReadModel(ttl_seconds=10, max_situations=50)
    rm.apply_detected(_sit("sit-1"))
    rm.apply_outcome(
        RemediationOutcome(
            situation_id="sit-1",
            playbook_id="p",
            result=RemediationResult.SUCCESS,
            health_after="healthy",
            ts=datetime(2026, 8, 16, tzinfo=UTC),
        )
    )
    # far in the future: > ttl past the outcome ts
    base = int(datetime(2026, 8, 16, tzinfo=UTC).timestamp() * 1000)
    assert len(rm.situations(now_ms=base + 20 * MS)) == 0


def test_active_situation_never_aged_out():
    rm = ReadModel(ttl_seconds=1, max_situations=50)
    rm.apply_detected(_sit("sit-1", status=SituationStatus.DETECTED))
    base = int(datetime(2026, 8, 16, tzinfo=UTC).timestamp() * 1000)
    assert len(rm.situations(now_ms=base + 999999)) == 1  # still detected → kept


def test_cap_evicts_oldest_terminal_first():
    rm = ReadModel(ttl_seconds=10_000, max_situations=2)
    for i in range(3):
        t = datetime(2026, 8, 16, 0, 0, i, tzinfo=UTC)
        rm.apply_detected(_sit(f"sit-{i}", t=t))
        rm.apply_outcome(
            RemediationOutcome(
                situation_id=f"sit-{i}",
                playbook_id="p",
                result=RemediationResult.SUCCESS,
                health_after="healthy",
                ts=t,
            )
        )
    ids = {s["id"] for s in rm.situations()}
    assert "sit-0" not in ids and len(ids) == 2  # oldest terminal evicted
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest services/read/tests/test_pruning.py -v`
Expected: FAIL — `ReadModel.__init__` has no `ttl_seconds`/`max_situations`; `situations()` takes no `now_ms`.

- [ ] **Step 4: Implement pruning**

In `services/read/projection.py`:

Update `__init__`:

```python
def __init__(
    self, max_outcomes: int = 200, ttl_seconds: float = 600.0, max_situations: int = 50
) -> None:
    self._sits: dict[str, dict] = {}
    self._outcomes: list[dict] = []
    self._max = max_outcomes
    self._ttl_ms = ttl_seconds * 1000
    self._max_sits = max_situations
```

Add helpers and wire pruning. A situation is terminal when its status is `resolved` or `failed`. "Last activity" is its outcome ts if present else `first_seen`; store `last_activity` on the dict when an outcome lands (update `apply_outcome` to also set `self._sits[o.situation_id]["last_activity"] = _epoch_ms(o.ts)`), and set `last_activity = first_seen` in `apply_detected`.

Add to `apply_detected` dict: `"last_activity": _epoch_ms(s.first_seen),` (only if not already present — use `existing.get("last_activity", _epoch_ms(s.first_seen))`).

In `apply_outcome`, after setting status, add:
```python
        if o.situation_id in self._sits:
            self._sits[o.situation_id]["last_activity"] = _epoch_ms(o.ts)
```

Add the prune method and use it in `situations()`:

```python
_TERMINAL = {"resolved", "failed"}


def _prune(self, now_ms: int) -> None:
    # 1. age-out terminal situations older than ttl
    for sid in list(self._sits):
        s = self._sits[sid]
        if s["status"] in self._TERMINAL and now_ms - s.get("last_activity", 0) > self._ttl_ms:
            del self._sits[sid]
    # 2. cap: if over max, evict oldest-terminal-first (never active)
    if len(self._sits) > self._max_sits:
        terminal = sorted(
            (s for s in self._sits.values() if s["status"] in self._TERMINAL),
            key=lambda s: s.get("last_activity", 0),
        )
        n_to_drop = len(self._sits) - self._max_sits
        for s in terminal[:n_to_drop]:
            del self._sits[s["id"]]


def situations(self, now_ms: int | None = None) -> list[dict]:
    if now_ms is not None:
        self._prune(now_ms)
    return list(self._sits.values())
```

Note: pruning on read (with `now_ms`) is sufficient; do NOT add `now_ms` to `apply_*` (keeps their signatures and all existing callers unchanged). The service passes `now_ms` in the endpoint (Task 7).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest services/read/tests/ -v`
Expected: PASS (new pruning tests + existing projection/api tests; existing `situations()` calls with no arg still work).

- [ ] **Step 6: Commit**

```bash
git add common/config.py services/read/projection.py services/read/tests/test_pruning.py
git commit -m "feat(read): age-out terminal situations by TTL + cap total (active never pruned)"
```

---

## Task 4: Read-model `metrics()` computation

**Files:**
- Modify: `services/read/projection.py` (add `metrics()` + auto tracking)
- Test: `services/read/tests/test_metrics.py` (Create)

**Interfaces:**
- Consumes: the projected `_sits` and `_outcomes`, and `RemediationOutcome.hitl_mode` (Task 1).
- Produces: `ReadModel.metrics() -> dict` with keys exactly matching the frontend `Metrics` type: `alertsIngested, situationsOpen, noiseReductionPct, mttrMinutes, autoRemediatedPct, suppressedToday, approvalsPending, successRate`. Also: `apply_outcome` records `hitl_mode` and resolution latency for MTTR; a `_suppressed_count` int incremented by `apply_suppressed` (Task 5).

- [ ] **Step 1: Extend apply_outcome to capture MTTR + auto data**

In `services/read/projection.py`, the outcome dict currently lacks the data metrics need. Add to the dict appended in `apply_outcome`:
- `"hitl_mode"`: `o.hitl_mode.value if hasattr(o.hitl_mode, "value") else str(o.hitl_mode)`
- `"mttr_ms"`: computed as `_epoch_ms(o.ts) - self._sits[o.situation_id]["first_seen"]` when the situation is known and result is SUCCESS, else `None`.

So the outcome append becomes:

```python
sit = self._sits.get(o.situation_id, {})
mttr_ms = None
if sit and o.result == RemediationResult.SUCCESS:
    mttr_ms = _epoch_ms(o.ts) - sit["first_seen"]
self._outcomes.insert(
    0,
    {
        "situation_id": o.situation_id,
        "playbook_id": o.playbook_id,
        "result": result,
        "reason": o.health_after,
        "ts": _epoch_ms(o.ts),
        "service": sit.get("service", "unknown"),
        "hitl_mode": o.hitl_mode.value if hasattr(o.hitl_mode, "value") else str(o.hitl_mode),
        "mttr_ms": mttr_ms,
    },
)
```

Also add `self._suppressed_count = 0` to `__init__`.

- [ ] **Step 2: Write the failing test**

```python
# services/read/tests/test_metrics.py
from datetime import UTC, datetime, timedelta
from common.contracts import (
    DiagnosedSituation,
    HitlMode,
    RemediationOutcome,
    RemediationResult,
    RootCauseHypothesis,
    Situation,
    SituationStatus,
)
from services.read.projection import ReadModel

T0 = datetime(2026, 8, 16, tzinfo=UTC)


def _sit(sid, status=SituationStatus.DETECTED, members=1, t=T0):
    from common.contracts import TelemetryEvent, TelemetryKind

    evs = [
        TelemetryEvent(
            source="p",
            kind=TelemetryKind.METRIC,
            name="cpu_usage",
            value=90.0,
            labels={"service": "web"},
            ts=t,
            fingerprint=f"f{i}",
        )
        for i in range(members)
    ]
    return Situation(
        id=sid,
        status=status,
        member_events=evs,
        severity="high",
        first_seen=t,
        last_seen=t,
        signature=sid.replace("sit-", ""),
    )


def test_empty_metrics_all_zero():
    m = ReadModel().metrics()
    assert m["successRate"] == 0.0 and m["mttrMinutes"] == 0.0
    assert m["situationsOpen"] == 0 and m["noiseReductionPct"] == 0.0
    assert set(m) == {
        "alertsIngested",
        "situationsOpen",
        "noiseReductionPct",
        "mttrMinutes",
        "autoRemediatedPct",
        "suppressedToday",
        "approvalsPending",
        "successRate",
    }


def test_mttr_and_rates():
    rm = ReadModel()
    rm.apply_detected(_sit("sit-1", members=10))  # 10 alerts collapsed
    # resolve 2 minutes later, auto
    rm.apply_outcome(
        RemediationOutcome(
            situation_id="sit-1",
            playbook_id="p",
            result=RemediationResult.SUCCESS,
            health_after="healthy",
            ts=T0 + timedelta(minutes=2),
            hitl_mode=HitlMode.AUTO,
        )
    )
    m = rm.metrics()
    assert m["alertsIngested"] == 10
    assert abs(m["noiseReductionPct"] - 90.0) < 0.01  # 1 situation from 10 alerts
    assert abs(m["mttrMinutes"] - 2.0) < 0.01
    assert m["successRate"] == 1.0
    assert m["autoRemediatedPct"] == 100.0


def test_open_and_pending_counts():
    rm = ReadModel()
    d = DiagnosedSituation(
        situation=_sit("sit-2", status=SituationStatus.DIAGNOSED),
        hypotheses=[
            RootCauseHypothesis(
                situation_id="sit-2",
                description="x",
                confidence=0.6,
                suggested_runbook_id="scale-service",
            )
        ],
        suggested_runbook_id="scale-service",
    )
    rm.apply_diagnosed(d)
    m = rm.metrics()
    assert m["situationsOpen"] == 1
    assert m["approvalsPending"] == 1  # diagnosed + hitl + not resolved
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest services/read/tests/test_metrics.py -v`
Expected: FAIL — `ReadModel` has no `metrics()`.

- [ ] **Step 4: Implement `metrics()`**

Add to `services/read/projection.py`:

```python
_OPEN = {"detected", "diagnosed", "acting"}


def metrics(self) -> dict:
    sits = list(self._sits.values())
    outs = self._outcomes
    total_out = len(outs)
    successes = sum(1 for o in outs if o["result"] == "success")
    autos = sum(1 for o in outs if o.get("hitl_mode") == "auto")
    mttrs = [o["mttr_ms"] for o in outs if o.get("mttr_ms") is not None]
    alerts = sum(s["memberCount"] for s in sits)
    n_sits = len(sits)
    open_sits = [s for s in sits if s["status"] in self._OPEN]
    pending = [
        s
        for s in open_sits
        if s.get("hitl_mode") == "hitl" and s["status"] in ("diagnosed", "acting")
    ]
    noise = ((1 - n_sits / alerts) * 100) if alerts else 0.0
    return {
        "alertsIngested": alerts,
        "situationsOpen": len(open_sits),
        "noiseReductionPct": round(max(0.0, noise), 1),
        "mttrMinutes": round((sum(mttrs) / len(mttrs) / 60000), 2) if mttrs else 0.0,
        "autoRemediatedPct": round(autos / total_out * 100, 1) if total_out else 0.0,
        "suppressedToday": self._suppressed_count,
        "approvalsPending": len(pending),
        "successRate": round(successes / total_out, 3) if total_out else 0.0,
    }
```

Note: `pending` uses `s.get("hitl_mode")` — situations default `hitl_mode` to `"hitl"` in `apply_detected`, so a diagnosed HITL situation counts. This is the derived (not authoritative) pending count per the spec.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest services/read/tests/ -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/read/projection.py services/read/tests/test_metrics.py
git commit -m "feat(read): compute live KPIs (MTTR, noise-reduction, auto%, rates) from projection"
```

---

## Task 5: `situations.suppressed` signal (correlation → read-model)

**Files:**
- Modify: `services/correlation/engine.py` (record suppressed), `services/correlation/consumer.py` (publish), `services/read/projection.py` (`apply_suppressed`), `services/read/consumer.py` (consume)
- Test: `services/correlation/tests/test_suppressed_signal.py` (Create), add a projection test.

**Interfaces:**
- Produces: engine `pop_suppressed() -> Situation | None` returns and clears the most recently suppressed situation; consumer publishes it to topic `situations.suppressed` as a `Situation`. Read: `ReadModel.apply_suppressed(s: Situation)` increments `_suppressed_count`; read consumer subscribes `("situations.suppressed", Situation, "apply_suppressed")`.

- [ ] **Step 1: Write the failing engine test**

```python
# services/correlation/tests/test_suppressed_signal.py
from datetime import UTC, datetime, timedelta
from common.contracts import TelemetryEvent, TelemetryKind
from services.correlation.adapters.river_correlator import RiverCorrelator
from services.correlation.engine import CorrelationEngine


def _ev(v, i, t0):
    return TelemetryEvent(
        source="p",
        kind=TelemetryKind.METRIC,
        name="cpu",
        value=v,
        labels={},
        ts=t0 + timedelta(seconds=5 * i),
        fingerprint=f"f{i}",
    )


def test_pop_suppressed_returns_suppressed_situation():
    # Force suppression: a correlator whose should_suppress is always True.
    class AlwaysSuppress(RiverCorrelator):
        def should_suppress(self, signature, threshold):
            return True

    c = AlwaysSuppress(z_threshold=0.0, warmup_samples=0)
    eng = CorrelationEngine(c, window_seconds=0.0)
    t0 = datetime(2026, 8, 16, tzinfo=UTC)
    for i in range(3):
        eng.add(_ev(90.0, i, t0))
    eng.flush()  # buffer collapses; suppressed
    s = eng.pop_suppressed()
    assert s is not None
    assert eng.pop_suppressed() is None  # cleared after pop
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest services/correlation/tests/test_suppressed_signal.py -v`
Expected: FAIL — no `pop_suppressed`.

- [ ] **Step 3: Record suppressed in the engine**

In `services/correlation/engine.py`, add `self._suppressed: Situation | None = None` to `__init__`. In `_correlate_buffer`, capture the suppressed situation before returning None:

```python
def _correlate_buffer(self) -> Situation | None:
    severity = self._correlator._severity_band(self._max_score)
    sit = self._correlator.correlate(self._buffer, severity=severity)
    self._buffer = []
    self._max_score = 0.0
    if self._correlator.should_suppress(sit.signature, self._suppress_threshold):
        self._suppressed = sit
        return None
    return sit


def pop_suppressed(self) -> Situation | None:
    with self._lock:
        s = self._suppressed
        self._suppressed = None
        return s
```

(`_correlate_buffer` already runs under the lock via its callers `add`/`flush`, so setting `self._suppressed` there is safe; `pop_suppressed` takes the lock for the read/clear.)

- [ ] **Step 4: Publish it in the consumer**

In `services/correlation/consumer.py`, after each `engine.add(event)` and after the final `engine.flush()`, drain suppressed. Refactor the publish logic:

```python
def _drain_suppressed(bus, engine):
    s = engine.pop_suppressed()
    if s is not None:
        publish_model(bus, "situations.suppressed", s)


def run_consumer(bus, engine: CorrelationEngine, stop_event: threading.Event) -> None:
    for event in iter_models(bus, "telemetry.raw", "correlation", TelemetryEvent):
        if stop_event.is_set():
            break
        emitted = engine.add(event)
        if emitted is not None:
            publish_model(bus, "situations.detected", emitted)
        _drain_suppressed(bus, engine)
    tail: Situation | None = engine.flush()
    if tail is not None:
        publish_model(bus, "situations.detected", tail)
    _drain_suppressed(bus, engine)
```

Note: the periodic flusher in `services/correlation/app.py:run_flusher` also calls `engine.flush()`. Add a `_drain_suppressed(bus, engine)` call there too, right after its `emitted = engine.flush()` block, importing `_drain_suppressed` from the consumer. (Update run_flusher accordingly.)

- [ ] **Step 5: Read-model consumes it**

In `services/read/projection.py` add:

```python
    def apply_suppressed(self, s: Situation) -> None:
        self._suppressed_count += 1
```

In `services/read/consumer.py`, add to `_TOPICS`:

```python
(("situations.suppressed", Situation, "apply_suppressed"),)
```

- [ ] **Step 6: Add a projection test for apply_suppressed**

```python
# add to services/read/tests/test_metrics.py
def test_suppressed_count_increments():
    from services.read.projection import ReadModel

    rm = ReadModel()
    rm.apply_suppressed(_sit("sit-9"))
    rm.apply_suppressed(_sit("sit-10"))
    assert rm.metrics()["suppressedToday"] == 2
```

- [ ] **Step 7: Run all affected tests**

Run: `uv run pytest services/correlation/tests/ services/read/tests/ -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add services/correlation/engine.py services/correlation/consumer.py services/correlation/app.py services/read/projection.py services/read/consumer.py services/correlation/tests/test_suppressed_signal.py services/read/tests/test_metrics.py
git commit -m "feat: publish situations.suppressed so closed-loop suppression is visible in metrics"
```

---

## Task 6: Correlation `POST /reset-baseline`

**Files:**
- Modify: `services/correlation/engine.py` (`reset`), `services/correlation/app.py` (endpoint + store engine on state)
- Test: `services/correlation/tests/test_reset.py` (Create)

**Interfaces:**
- Produces: `CorrelationEngine.reset()` swaps in a fresh correlator (same construction params) and clears buffer/suppressed under the lock. `POST /reset-baseline` on correlation returns `{"reset": true}`.

- [ ] **Step 1: Write the failing engine test**

```python
# services/correlation/tests/test_reset.py
from datetime import UTC, datetime, timedelta
from common.contracts import TelemetryEvent, TelemetryKind
from services.correlation.adapters.river_correlator import RiverCorrelator
from services.correlation.engine import CorrelationEngine


def _ev(v, i, t0):
    return TelemetryEvent(
        source="p",
        kind=TelemetryKind.METRIC,
        name="cpu",
        value=v,
        labels={},
        ts=t0 + timedelta(seconds=5 * i),
        fingerprint=f"f{i}",
    )


def test_reset_clears_buffer_and_baseline():
    eng = CorrelationEngine(
        RiverCorrelator(z_threshold=2.0, warmup_samples=2), window_seconds=100.0
    )
    t0 = datetime(2026, 8, 16, tzinfo=UTC)
    for i in range(6):
        eng.add(_ev(10.0 if i < 3 else 99.0, i, t0))  # baseline then spike → buffered anomaly
    eng.reset()
    # after reset, buffer empty → flush yields nothing
    assert eng.flush() is None
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest services/correlation/tests/test_reset.py -v`
Expected: FAIL — no `reset`.

- [ ] **Step 3: Implement `reset`**

In `services/correlation/engine.py`, the engine needs to remember how to build a fresh correlator. Capture the construction in `__init__` by storing a factory. Simplest: store the correlator's config and rebuild. Since `RiverCorrelator(z_threshold=..., warmup_samples=...)` are the only params, capture them:

Add to `__init__` (after `self._correlator = correlator`):
```python
        self._correlator_factory = lambda: type(correlator)(
            z_threshold=correlator._z_threshold,
            warmup_samples=correlator._warmup_samples,
        )
```

Add the method:
```python
    def reset(self) -> None:
        with self._lock:
            self._correlator = self._correlator_factory()
            self._buffer = []
            self._max_score = 0.0
            self._suppressed = None
```

Confirm `RiverCorrelator` exposes `_warmup_samples` (it's set in `__init__` as `self._warmup_samples`). If the attribute name differs, use the real one.

- [ ] **Step 4: Add the endpoint + store engine on app.state**

In `services/correlation/app.py`, the lifespan builds `engine` but doesn't store it. Add `app.state.engine = engine` in the lifespan (after constructing it), and add:

```python
@app.post("/reset-baseline")
def reset_baseline() -> dict:
    engine = getattr(app.state, "engine", None)
    if engine is not None:
        engine.reset()
    return {"reset": True}
```

- [ ] **Step 5: Write an endpoint test**

```python
# add to services/correlation/tests/test_reset.py
def test_reset_baseline_endpoint():
    from fastapi.testclient import TestClient
    from services.correlation.app import app
    from services.correlation.engine import CorrelationEngine
    from services.correlation.adapters.river_correlator import RiverCorrelator

    app.state.engine = CorrelationEngine(RiverCorrelator())
    c = TestClient(app)
    assert c.post("/reset-baseline").json() == {"reset": True}
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest services/correlation/tests/ -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add services/correlation/engine.py services/correlation/app.py services/correlation/tests/test_reset.py
git commit -m "feat(correlation): POST /reset-baseline resets detector for repeat simulations"
```

---

## Task 7: Read `GET /metrics` + `POST /reset` endpoints

**Files:**
- Modify: `services/read/app.py`
- Test: `services/read/tests/test_read_api.py` (add cases)

**Interfaces:**
- Consumes: `ReadModel.metrics()` (Task 4), `ReadModel` internals for reset, `Settings` for ttl/max.
- Produces: `GET /metrics` → `model.metrics()`; `POST /reset` → clears model, returns `{"reset": true}`. `GET /situations` now passes real `now_ms` so pruning runs.

- [ ] **Step 1: Write failing tests**

```python
# add to services/read/tests/test_read_api.py
def test_metrics_endpoint():
    from services.read.projection import ReadModel

    model = ReadModel()
    c = _client(model)  # existing helper that sets app.state.model
    m = c.get("/metrics").json()
    assert "successRate" in m and "mttrMinutes" in m


def test_reset_endpoint_clears_model():
    from datetime import UTC, datetime
    from common.contracts import Situation, SituationStatus
    from services.read.projection import ReadModel

    model = ReadModel()
    model.apply_detected(
        Situation(
            id="sit-1",
            status=SituationStatus.DETECTED,
            member_events=[],
            severity="high",
            first_seen=datetime(2026, 8, 16, tzinfo=UTC),
            last_seen=datetime(2026, 8, 16, tzinfo=UTC),
            signature="1",
        )
    )
    c = _client(model)
    assert len(c.get("/situations").json()) == 1
    assert c.post("/reset").json() == {"reset": True}
    assert c.get("/situations").json() == []
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest services/read/tests/test_read_api.py -k "metrics or reset" -v`
Expected: FAIL — routes don't exist.

- [ ] **Step 3: Implement**

In `services/read/app.py`:
- The lifespan currently constructs `ReadModel(max_outcomes=settings.read_outcomes_max)`. Pass the new settings:
  `ReadModel(max_outcomes=settings.read_outcomes_max, ttl_seconds=settings.read_situation_ttl_seconds, max_situations=settings.read_situations_max)`.
- Add a `reset()` method to `ReadModel` in projection.py:
```python
    def reset(self) -> None:
        self._sits.clear()
        self._outcomes.clear()
        self._suppressed_count = 0
```
- Add endpoints. For `/situations`, pass real time (import `time`):
```python
import time


@app.get("/situations")
def situations() -> list[dict]:
    model = getattr(app.state, "model", None)
    return model.situations(now_ms=int(time.time() * 1000)) if model else []


@app.get("/metrics")
def metrics() -> dict:
    model = getattr(app.state, "model", None)
    return model.metrics() if model else ReadModel().metrics()


@app.post("/reset")
def reset() -> dict:
    model = getattr(app.state, "model", None)
    if model is not None:
        model.reset()
    return {"reset": True}
```

(Leave the existing `/outcomes` endpoint as-is.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest services/read/tests/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/read/app.py services/read/projection.py services/read/tests/test_read_api.py
git commit -m "feat(read): GET /metrics and POST /reset; wire ttl/cap settings; prune on read"
```

---

## Task 8: reset.sh + README + wire into chaos.sh

**Files:**
- Create: `scripts/reset.sh`
- Modify: `scripts/chaos.sh`, `README.md`

**Interfaces:**
- Produces: `scripts/reset.sh` composes demo-app `/fix` + correlation `/reset-baseline` + read `/reset`. `chaos.sh` gains a `reset` path and calls reset at the start.

- [ ] **Step 1: Write reset.sh**

```bash
#!/usr/bin/env bash
# Reset the live stack to a clean slate WITHOUT a docker restart:
# recover the demo target, clear the detector baseline, empty the read model.
set -euo pipefail

DEMO=${DEMO_URL:-http://localhost:8080}
CORR=${CORR_URL:-http://localhost:8002}
READ=${READ_URL:-http://localhost:8007}

echo "→ Recovering demo-app…"
curl -fsS -X POST "$DEMO/fix" >/dev/null && echo "  demo-app healthy"
echo "→ Resetting correlation baseline…"
curl -fsS -X POST "$CORR/reset-baseline" >/dev/null && echo "  detector baseline cleared"
echo "→ Clearing read-model…"
curl -fsS -X POST "$READ/reset" >/dev/null && echo "  read model empty"
echo "✓ Clean slate. Next break will detect fresh."
```

Make executable: `chmod +x scripts/reset.sh`. Verify: `bash -n scripts/reset.sh`.

- [ ] **Step 2: Wire into chaos.sh**

In `scripts/chaos.sh`, add a `reset` subcommand near the top (after arg parsing/DEMO/READ setup):

```bash
if [ "${1:-}" = "reset" ]; then
  exec "$(dirname "$0")/reset.sh"
fi
```

And call reset at the very start of the incident flow (before the break), so every scripted run begins clean:

```bash
echo "→ Resetting to a clean slate first…"
"$(dirname "$0")/reset.sh" >/dev/null 2>&1 || true
```

Verify: `bash -n scripts/chaos.sh`.

- [ ] **Step 3: README**

Add to the "Run it live" section a "Resetting between runs" note:
- `./scripts/reset.sh` (or `./scripts/chaos.sh reset`) gives a clean slate without `docker compose down` — recovers the demo app, clears the detector's learned baseline, and empties the read model.
- Note verbatim: "The `/reset`, `/reset-baseline`, `/break`, and `/fix` endpoints are simulation controls, not production endpoints. When this stack is pointed at a real system, they must be gated or removed."

- [ ] **Step 4: Commit**

```bash
git add scripts/reset.sh scripts/chaos.sh README.md
git commit -m "feat(scripts): reset.sh clean-slate + chaos.sh reset; README simulation-controls note"
```

---

## Task 9: Frontend — loadMetrics + live Overview KPIs

**Files:**
- Modify: `frontend/src/data/api.ts`, `frontend/src/data/source.ts`, `frontend/src/views/Overview.tsx`

**Interfaces:**
- Consumes: read `GET /metrics` (Task 7); the `Metrics` type from `types.ts`; the mock `metrics` object.
- Produces: `loadMetrics(): Promise<Metrics>` in api.ts and source.ts; Overview uses `useData(loadMetrics, metrics)`.

- [ ] **Step 1: Add loadMetrics to api.ts**

In `frontend/src/data/api.ts`, add `Metrics` to the type import and a loader:

```typescript
import type { AuditRow, Metrics, OutcomeRow, Playbook, Situation } from "./types";
// ...
export const loadMetrics = () => getJSON<Metrics>(`${READ}/metrics`);
```

- [ ] **Step 2: Add to source.ts**

In `frontend/src/data/source.ts`:

```typescript
export const loadMetrics = LIVE ? api.loadMetrics : async () => mock.metrics;
```

- [ ] **Step 3: Wire Overview**

In `frontend/src/views/Overview.tsx`:
- Change `import { metrics, series, services } from "../data/mock"` to `import { metrics as mockMetrics, series, services } from "../data/mock"`.
- Add imports: `import { loadMetrics } from "../data/source";` and (if not present) `import { useData } from "../hooks/useData";`.
- Inside `Overview()`, add: `const { data: metrics } = useData(loadMetrics, mockMetrics);`
- All existing `metrics.*` references now read the live value. No tile markup changes.

- [ ] **Step 4: Type-check**

Run (from `frontend/`): `npm run build`
Expected: build succeeds. Fix any unused-import (`noUnusedLocals`) issues from the rename.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/data/api.ts frontend/src/data/source.ts frontend/src/views/Overview.tsx
git commit -m "feat(frontend): Overview KPIs read live read-model /metrics (last mock removed)"
```

---

## Task 10: Frontend — toast system + approve/reject error feedback

**Files:**
- Create: `frontend/src/hooks/useToast.tsx`
- Modify: `frontend/src/components/Shell.tsx`, `frontend/src/views/Incidents.tsx`

**Interfaces:**
- Produces: `useToast()` returning `{ toasts, push, dismiss }`; a `<ToastHost>` component; a module-level toast bus so any component can `pushToast(...)` without prop drilling. Incidents calls `pushToast({kind, msg})` on approve/reject success/failure.

- [ ] **Step 1: Write the toast hook + host (module-level bus, no context needed)**

```tsx
// frontend/src/hooks/useToast.tsx
import { useEffect, useState } from "react";

export type Toast = { id: number; kind: "success" | "error"; msg: string };

let _id = 0;
const _listeners = new Set<(t: Toast) => void>();

export function pushToast(kind: Toast["kind"], msg: string) {
  const t = { id: ++_id, kind, msg };
  _listeners.forEach((l) => l(t));
}

export function ToastHost() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  useEffect(() => {
    const on = (t: Toast) => {
      setToasts((cur) => [...cur, t]);
      setTimeout(() => setToasts((cur) => cur.filter((x) => x.id !== t.id)), 4000);
    };
    _listeners.add(on);
    return () => { _listeners.delete(on); };
  }, []);
  return (
    <div className="pointer-events-none fixed bottom-6 right-6 z-[60] flex flex-col gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`pointer-events-auto rounded-2xl border px-4 py-3 text-sm shadow-lift backdrop-blur-xl transition-all duration-500 ease-fluid ${
            t.kind === "error"
              ? "border-sev-crit/30 bg-sev-crit/10 text-sev-crit"
              : "border-sev-ok/30 bg-sev-ok/10 text-sev-ok"
          }`}
        >
          {t.msg}
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: Mount ToastHost in Shell**

In `frontend/src/components/Shell.tsx`, import `ToastHost` and render it just before the closing `</div>` of the root (after `<main>…</main>` at line ~130):

```tsx
import { ToastHost } from "../hooks/useToast";
// ...
      <main className="relative z-10 mx-auto w-full max-w-6xl px-4 pb-24 pt-8 sm:px-6">{children}</main>
      <ToastHost />
    </div>
```

- [ ] **Step 3: Wire Incidents approve/reject**

In `frontend/src/views/Incidents.tsx`, import `pushToast` from `../hooks/useToast`, and replace the approve/reject handlers:

```tsx
import { pushToast } from "../hooks/useToast";

const LIVE = import.meta.env.VITE_DATA_MODE === "live";

async function approve() {
  if (working || !sel) return;
  setWorking(true);
  update(sel.id, { status: "acting" });
  try {
    await decideApproval(`appr-${sel.id}`, "approved");
    pushToast("success", `Approved — remediating ${sel.suggested_runbook_id ?? "playbook"}`);
    if (!LIVE) setTimeout(() => update(sel.id, { status: "resolved" }), 1400);
    // live mode: let the 5s poll converge to the real server status
  } catch (e) {
    pushToast("error", `Approval failed: ${e instanceof Error ? e.message : "unknown"}`);
    update(sel.id, { status: "diagnosed" }); // roll the optimistic flip back
  }
  setTimeout(() => setWorking(false), 1500);
}

async function reject() {
  if (!sel) return;
  update(sel.id, { status: "failed" });
  try {
    await decideApproval(`appr-${sel.id}`, "rejected");
    pushToast("success", "Rejected — no action taken");
  } catch (e) {
    pushToast("error", `Reject failed: ${e instanceof Error ? e.message : "unknown"}`);
  }
}
```

Note: in mock mode `decideApproval` is a no-op (resolves), so the success toast fires and the optimistic resolve still runs (guarded by `!LIVE`). In live mode, the optimistic resolve is dropped and the poll drives the final status; a real failure shows an error toast and rolls the status back to `diagnosed`.

- [ ] **Step 4: Type-check**

Run (from `frontend/`): `npm run build`
Expected: build succeeds. Ensure `LIVE` const doesn't collide with any existing symbol; remove if `import.meta.env` already referenced elsewhere in the file (use inline check if so).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/useToast.tsx frontend/src/components/Shell.tsx frontend/src/views/Incidents.tsx
git commit -m "feat(frontend): toast system + visible approve/reject errors in live mode"
```

---

## Task 11: Full verification + live smoke

**Files:** none (verification only)

- [ ] **Step 1: Full Python suite**

Run: `uv run pytest -q`
Expected: all pass (203 + new). A failure in existing tests means a default wasn't test-safe — investigate, don't paper over.

- [ ] **Step 2: Ruff**

Run: `uv run ruff check` (then `uv run ruff check --fix` for import-order nits in new test files; re-run to confirm clean).

- [ ] **Step 3: Frontend build**

Run (from `frontend/`): `npm run build`
Expected: clean strict build.

- [ ] **Step 4: Commit any lint fixes**

```bash
git add -A && git commit -m "chore: lint + verification for live completeness" || echo "nothing to commit"
```

- [ ] **Step 5: Live smoke (manual, requires Docker)**

Rebuild + recreate the stack, then: `./scripts/reset.sh`; break the demo; confirm `GET :8007/metrics` returns non-zero `situationsOpen`/live values after an incident, and that an approve failure surfaces a toast. This is a manual gate (8-container integration), noted here for the operator — not an automated test.

---

## Self-Review Notes (for the executor)

- **`_warmup_samples` attribute:** Task 6's `reset()` reads `correlator._warmup_samples` and `_z_threshold`. Confirm these exact private names on `RiverCorrelator.__init__` before writing `reset` (the plan verified `z_threshold`/`warmup_samples` are the constructor params). If the stored attribute names differ, use the real ones.
- **`run_flusher` + suppressed drain (Task 5 Step 4):** the periodic flusher in `correlation/app.py` must also drain suppressed after its flush, or a suppression that happens on a timer-flush is never published. The task says to add the drain call there — don't skip it.
- **Situations `hitl_mode` is always `"hitl"` today** (defaulted in `apply_detected`, never set from real data). So `approvalsPending` counts diagnosed situations as pending, which is correct for the demo (all demo playbooks are HITL). If a future situation carries a real auto mode, this stays correct because auto playbooks never create a pending gate.
- **MTTR only from SUCCESS outcomes:** a rolled-back/failed remediation has no meaningful resolution time, so `mttr_ms` is None for non-success and excluded from the mean. Intended.
