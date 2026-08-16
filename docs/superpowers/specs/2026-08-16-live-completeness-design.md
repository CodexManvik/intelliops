# Live Completeness (Tier 1 + 2) — Design

**Date:** 2026-08-16
**Status:** Approved (pending spec review)
**Depends on:** the real-data live stack (2026-08-15) — running end-to-end.

## Goal

Close every "feels like a demo, not a system" gap in the running live stack, and
make the stack **repeatably runnable** so we can drive simulations without a
docker restart. Two tiers:

- **Tier 1 — live-data completeness:** the Overview KPIs become real (no mock
  numbers left in the UI), live-mode UI errors become visible, and the
  read-model's service derivation stops being demo-hardcoded.
- **Tier 2 — simulation ergonomics:** a one-command scenario reset, and bounded
  situation retention so long runs stay legible.

Deliberately **out of scope** (Tier 3, deferred to a dedicated design once we know
the real target system): real Kubernetes remediation, a real health checker,
endpoint auth. The remediator stays dry-run (ADR-007).

## Guiding principle

The **read-model is the lifecycle-aware service** — it is the one place that sees
a situation's detection (`first_seen`), its diagnosis, and its resolution
(`RemediationOutcome.ts`), plus the outcomes stream. So it is the correct home for
computing live KPIs and for owning situation retention. No new timestamp threading
is required; the timestamps already exist.

## The six pieces

### 1. Live KPIs — `GET /metrics` on the read service

A new `ReadModel.metrics()` computes the 8 fields of the frontend `Metrics` type
from projected state. Nothing is fabricated.

| Field | Derivation |
|---|---|
| `situationsOpen` | count of situations with status in {detected, diagnosed, acting} |
| `approvalsPending` | count of situations that are HITL + status diagnosed/acting + not resolved |
| `successRate` | success outcomes / total outcomes (0..1) |
| `autoRemediatedPct` | outcomes whose playbook is graduated (auto) / total outcomes × 100 |
| `mttrMinutes` | mean of (outcome.ts − situation.first_seen) over resolved situations, in minutes |
| `alertsIngested` | Σ memberCount across all situations |
| `noiseReductionPct` | (1 − situationCount / alertsIngested) × 100, clamped ≥ 0 |
| `suppressedToday` | count of `situations.suppressed` events the read-model has seen |

Empty model → every rate/count/MTTR is 0; no divide-by-zero, no nulls.

**`approvalsPending`** is derived from projected situation state (HITL + non-terminal),
not a cross-service call to governance — a dashboard number that can lag governance
by one poll but never blocks. Governance's `GET /approvals` remains authoritative
for the actual approve action.

**`autoRemediatedPct`** needs to know whether each outcome ran auto or via a HITL
approval. The current `RemediationOutcome` contract carries `situation_id,
playbook_id, result, health_after, ts` — but NOT the playbook's `hitl_mode`, and
neither the situation events nor the outcome tell the read-model the mode. So this
is not derivable from projected state today. **Decision — stamp the mode on the
outcome:** add `hitl_mode: HitlMode` to the `RemediationOutcome` contract (the
action service knows it at outcome time; it is an additive, non-breaking field with
a default). The read-model then counts an outcome as auto-remediated when its
`hitl_mode == "auto"`. This is the truthful, low-coupling source — no cross-service
call, no inference. Existing tests that construct `RemediationOutcome` get the
default and are unaffected; the read-model projection and any outcome-shaped tests
gain the field.

### 2. Frontend live KPIs

- `loadMetrics(): Promise<Metrics>` added to `api.ts` (GET `{READ}/metrics`) and
  `source.ts` (live → api; mock → the mock `metrics` object).
- `Overview.tsx`: `metrics` moves from a static mock import to
  `useData(loadMetrics, mockMetrics)` — mock object as the initial value so tiles
  render instantly, then update live. No tile markup changes.

### 3. UI error feedback

- A minimal toast system: `useToast` hook + a fixed-position container mounted in
  `Shell` (bottom-right, auto-dismiss ~4s, severity-colored via the existing `sev`
  palette; bezel + blur + spring to match the design language). No new dependency.
- `approve()`/`reject()` in `Incidents.tsx`: on live-mode failure, `toast.error`
  with the reason; on success, `toast.success`. Mock mode stays silent-success
  (decideApproval is a no-op there).
- **Optimistic update change (live only):** on approve, flip to `acting` on click
  but do NOT optimistically flip to `resolved`; let the 5s poll converge to the
  real server status, and surface any error via toast. Mock mode keeps the instant
  optimistic resolve (no server to converge to).

### 4. `_service_of` fix

Replace the hardcoded `"demo-app"` fallback with a precedence chain over member
events' labels: first non-empty of `labels["service"]` → `labels["job"]` →
`labels["instance"]`, across all member events; `"unknown"` if none. Correctness
fix only; visible demo behavior is identical (events carry `service: demo-app`),
but it now works for any telemetry source.

### 5. Stale-situation age-out + cap

The read-model bounds retained situations:

- **Age-out:** drop situations whose status is terminal (resolved/failed) AND whose
  last activity is older than `read_situation_ttl_seconds` (default 600). "Last
  activity" = outcome ts if resolved, else first_seen.
- **Cap:** keep at most `read_situations_max` situations (default 50); evict
  oldest-terminal-first; NEVER evict a non-terminal (active) situation.
- A `_prune(now_ms)` helper runs at the top of `apply_*` (bounds memory as events
  arrive) and in `situations()` (reads never return aged-out rows). `now_ms` is an
  injected parameter — the service passes real time, tests pass a fixed time,
  keeping the projection a deterministic pure structure.

### 6. Scenario reset

Each service owns its own reset; a script composes them (no cross-service coupling).

- **correlation `POST /reset-baseline`:** swap the engine's correlator for a fresh
  `RiverCorrelator` and clear the buffer, under the existing engine lock. Detector
  forgets the learned spike. Returns `{"reset": true}`.
- **read `POST /reset`:** clear `_sits` and `_outcomes` (empties the projection).
  Returns `{"reset": true}`. The Redis streams are untouched — this resets the
  *view*, which is the demo goal. Wiping streams is explicitly NOT done: deleting an
  active stream orphans its consumer groups (observed to kill RCA's consumer).
- **demo-app `POST /fix`:** already exists; recovers to healthy.
- **`scripts/reset.sh`:** calls demo-app/fix → correlation/reset-baseline →
  read/reset in sequence. `chaos.sh` gains a `reset` subcommand and calls reset at
  the start of a scripted incident so every run begins clean.

**Safety:** these are simulation affordances, guarded by the same localhost CORS
posture as everything else. README notes them as simulation controls, not
production endpoints. Known follow-up: gate or remove them when wired to a real
system.

## Testing

TDD, matching existing style (pure functions tested directly; endpoints via
`TestClient`; no real infra). Existing suite stays green — every change is additive
with test-safe defaults.

- `ReadModel.metrics()` — scripted situation+outcome sequence asserts all 8 fields;
  empty model → all zeros.
- `_service_of` — service / job-only / instance-only / no-labels precedence + unknown.
- Age-out + cap — known `now_ms`; terminal-old drop, active never drop, cap evicts
  oldest-terminal-first.
- `suppressedToday` — applying a suppressed event increments the counter.
- read `POST /reset`, correlation `POST /reset-baseline` — TestClient: post-reset
  state is empty / detector re-detects.
- Frontend — `npm run build` (strict) is the gate; toast, `loadMetrics`, and the
  live-path approve change must type-check.

## Concrete change list

**New files:**
- `frontend/src/hooks/useToast.tsx`
- `scripts/reset.sh`
- `docs/superpowers/specs/2026-08-16-live-completeness-design.md` (this file)

**Modified — backend:**
- `common/contracts.py` — add `hitl_mode: HitlMode` to `RemediationOutcome`
  (additive, defaulted — does not mutate existing field semantics; ADR-006 allows
  additive changes)
- `services/action/remediate.py` — stamp `hitl_mode` on the emitted outcome
- `services/read/projection.py` — `metrics()`, `_service_of` fix, `_prune()` +
  age-out/cap, suppressed counter, auto-vs-hitl from outcome `hitl_mode`
- `services/read/app.py` — `GET /metrics`, `POST /reset`
- `services/read/consumer.py` — consume `situations.suppressed`
- `services/correlation/engine.py` — buffer clear / correlator swap for reset;
  emit suppression signal where suppression is decided
- `services/correlation/app.py` — `POST /reset-baseline`
- `services/correlation/consumer.py` — publish `situations.suppressed` on suppress
- `common/config.py` — `read_situation_ttl_seconds` (600), `read_situations_max` (50)
- `README.md` — reset controls + simulation note

**Modified — frontend:**
- `frontend/src/data/api.ts`, `source.ts` — `loadMetrics`
- `frontend/src/views/Overview.tsx` — live metrics
- `frontend/src/views/Incidents.tsx` — toast + live-path status handling
- `frontend/src/components/Shell.tsx` — mount toast container

**Rough size:** ~4 new files, ~11 edits, ~7 TDD tasks.
