# Honesty & Evidence — making IntelliOps prove its realness — Design Spec

**Date:** 2026-08-25
**Owner:** Manvik (integration lead)
**Status:** design from a verified adversarial audit (6 angles, file:line evidence); staged delivery.

## The problem (from the audit — verified against code)

The **engine is real** (414 real behavior tests; real detection → RCA → governance → persistence; proven live on Meridian). But the **UI is evidence-thin theater on top of it**: every rich signal the engine produces — the raw telemetry that crossed threshold, the z-score, the hypothesis evidence, the LLM explanation, the real remediation outcome — is **dropped at the read-model projection boundary**, so the console can't prove anything is real. Plus two UI bugs make it actively *lie* (a permanently-reappearing approve gate; hardcoded "healthy" outcome text), and the LLM is dormant-and-invisible by default.

**The pattern:** the read-model was built to feed a pretty *mock demo*, not to prove anything. The fix is not "add panels" — it's to **stop dropping evidence, expose the internals the engine already has, then build the UI that shows them**, and to **stop the UI lying**.

## Goal

Make IntelliOps *demonstrably* real to a skeptic sitting at the screen: every claim traceable to its source, every incident with a readable narrative (what broke, why, what was done), an "under the hood" view of the live pipeline, a working+visible LLM, and no fabricated numbers. Fix the bugs first so it stops lying immediately.

## Non-goals

- Not re-litigating past docs/PRs (user's call: focus forward).
- Not changing the detection/RCA/remediation *logic* — it's real; the gap is *exposure* and *presentation*.
- Not making dry-run remediation touch real infra (that's the `REMEDIATOR_MODE=k8s` path, already real behind a flag) — but the UI must **label** dry-run honestly.
- No new heavy dependency.

## Global Constraints

- **Test-safe & additive.** Contract changes are additive (new optional fields default None/empty); the existing suite (414) stays green; mock mode still works (mock data gets the new fields too).
- **Gates:** `uv run pytest -m "not postgres and not kafka"` green; `ruff check` + `ruff format --check .` clean; `npm run build` clean.
- **No fabricated data in live mode.** If a number/label can't be computed from real data, it's removed or explicitly labeled (e.g. "dry-run", "no baseline yet") — never a hardcoded literal dressed as real.
- **Commit trailer** on every commit.
- **Shared files (coordinate):** `common/contracts.py` (additive fields), `services/read/projection.py` + `app.py` (the projection is the crux), `frontend/src/data/types.ts` (additive), `common/config.py` (additive).

---

## STAGE 1 — Stop the lying (bugs + fake chrome). Ships first.

### 1a. Fix the reappearing approve gate (CRITICAL)
The optimistic `overrides` never clear and the server never emits `acting`, so the poll can't converge (`Incidents.tsx:40,54,64`). Fix by **reconciling optimistic state with server truth**:
- When a server refetch arrives, **drop any optimistic override for a situation whose server status has advanced past the optimistic one** (i.e. clear `overrides[id]` once the server reflects a terminal or later state). Concretely: after each load, prune overrides where the server situation is `resolved`/`failed` (terminal) — the server wins.
- Make the render chain handle the real HITL lifecycle: a HITL situation sits at `diagnosed` during the wait; on approve, show an **"awaiting outcome"** state (optimistic, but time-boxed) that *yields to* the server's terminal outcome when it arrives — not a permanent `acting` that matches no branch.
- Remove the mock-only `if (!LIVE) setTimeout(...resolved)` asymmetry; live must have a real resolution path (driven by the server outcome, now joined onto the situation — see Stage 2).
- Guard `working` on async completion (not a fixed timer); guard `reject()` the same; hide the dev "reset" button in live (or gate behind a debug flag).

### 1b. Fix the hardcoded outcome text (CRITICAL)
`Incidents.tsx:222/230` hardcode "healthy"/"aborted:rejected". Replace with the **real** `health_after`/`result` from the situation's outcome (joined onto the situation in Stage 2). Until Stage 2 lands, at minimum read the real value from the `/outcomes` feed by `situation_id` rather than a literal.

### 1c. Delete or make-real the hardcoded chrome
- Overview `−41%`/`+6` deltas (`Overview.tsx:188,199`): **remove** (no baseline to compute a delta) — or compute from the rolling metric buffer if a prior window exists; no fake literals.
- "on target" / "target band 80–95%" captions (`Overview.tsx:161,176`): remove or make conditional on the real value.
- Governance gate cards + RBAC table + compliance footer (`Governance.tsx:8-27,113-124,99`): either drive from real data (the playbooks' `hitl_mode`/`reversible` are real; RBAC rules could be exposed) or **clearly label as "policy reference (static)"** so they don't imply live state. Prefer: make the gate cards reflect the *actual* config (AUTH_MODE, whether HITL is enforced), and label the RBAC table as the configured policy.

---

## STAGE 2 — Stop dropping evidence (the real fix). Backend projection + contracts.

The crux: widen the read-model so each situation carries its **full narrative**, and expose the **internals**. All additive.

### 2a. Additive contract fields (`common/contracts.py`)
- `RemediationOutcome`: add `steps: list[dict] = []` (what the remediator actually did — even dry-run logs them), and it already has `result` + `health_after`. The remediator returns the executed steps; `execute_remediation` puts them on the outcome.
- `DiagnosedSituation`: add `enrichment: EnrichmentContext | None = None` so the deploy/topology/config context behind a diagnosis travels.
- (Situation already has `member_events`; `RootCauseHypothesis` already has `evidence` + `explanation` — the gap is projection, not contract.)

### 2b. Widen the read-model projection (`services/read/projection.py`) — the heart of the fix
Keep, don't collapse:
- **member_events**: project a bounded list of `{name, value, labels, kind, ts}` (not just `memberCount`) so the UI can show *which metric hit what value*. Cap at N (e.g. 20) for size.
- **hypotheses**: include `evidence: list[str]` AND `explanation: str | None` (the LLM/template text) in each projected hypothesis dict (`projection.py:120-127`).
- **z-score / peak score**: capture the correlator's max score for the situation onto the projected situation (needs the engine to attach it — see 2c).
- **the real outcome joined onto the situation**: when `apply_outcome` fires, store `outcome: {result, health_after, steps, mode}` ON the situation (not only in the global list), so the incident detail shows its *own* real outcome.
- **a timeline**: record `stages: {detected: ts, diagnosed: ts, acting: ts?, resolved/failed: ts}` as each apply_* fires (the true data gap — capture it now).
- **a human title**: derive a readable title from the top hypothesis description + service (e.g. "resource saturation · meridian-aggregation") instead of the raw hex signature. Keep the signature as a sub-line.
- **remediation mode**: mark whether the outcome was `dry_run` vs `k8s` (from config, published on the outcome) so the UI can label it honestly.

### 2c. Attach the z-score to the situation (`services/correlation/`)
The engine keeps `_max_score` then resets it (`engine.py:60,73`). Instead, **attach the peak score (and per-metric baseline snapshot for the member metrics) to the emitted Situation** via an additive field, so it flows to the read-model. Additive; `river` default behavior otherwise unchanged.

### 2d. New read endpoints for the internals (`services/read/app.py` + a small correlation GET)
- `GET /situations/{id}` — the **full** situation: member_events, hypotheses (with evidence + explanation), the z-score, the timeline, the joined real outcome + steps, the enrichment context. This backs the incident-detail drill-down.
- `GET /audit` on read-service (or surface the governance audit) — the audit records are written but never readable over HTTP; expose them (read-only) so the console's Audit view shows *real* rows, and every decision is traceable.
- `GET /system` — a status/introspection endpoint: active `correlator_kind`, the LLM provider + endpoint status (template vs openai-compatible, reachable?), bus backend, store backend, per-service reachability. Backs the "under the hood" / system-state view and the LLM badge.
- Correlation service: a `GET /baseline` (or `/state`) exposing the current per-metric baselines + active correlator_kind — backs the debug view's "here's the live z-score baseline" panel.

---

## STAGE 3 — Build the UI that shows the evidence + the LLM settings.

All backed by Stage 2's real data. Additive to the existing console (frontend/).

### 3a. Incident detail drill-down (the "no description" fix)
Click a situation → a detail view showing, from `GET /situations/{id}`:
- **What broke:** the member events table — metric name, value, labels, timestamp. The actual signal.
- **The readable title** + severity + service + the z-score ("cpu_usage = 92, z = 6.3 vs baseline 18±2").
- **The full ranked hypotheses** with their **evidence list** and the **LLM/template explanation** (labeled as such), and *why this runbook*.
- **The timeline:** detected → diagnosed → gated → approved → remediated, with real timestamps + durations (MTTR).
- **The real outcome:** the actual `health_after`, the **steps that ran** (labeled `dry-run` if so), result.
- The audit trail for this correlation_id.

### 3b. "Under the hood" / System view (the provenance + debug view)
A new view (or a panel) showing the live internals from `GET /system` + `GET /baseline`:
- Active correlator kind, z-score baselines per metric (with a live spark of value-vs-baseline).
- The event stream (from SSE, now carrying real nudges + a recent-events feed).
- LLM provider status badge: **"LLM: connected (gpt-4o-mini)"** vs **"Template (no endpoint set)"** vs **"LLM error → template fallback"** — so it's provable at a glance.
- Bus/store backends, per-service health. Remediation mode (dry-run vs k8s) shown prominently and honestly.

### 3c. LLM settings + make it actually callable
- A **Settings panel** to set the OpenAI-compatible `endpoint` + `api_key` + `model`. Since these are server-side config, the panel either (a) writes them to the rca service via a small authenticated `POST /config/llm` on rca that updates the running provider, or (b) documents the env clearly + shows the current status and a "test connection" button that calls a `GET /system` liveness probe. **Decide in the plan** — (a) is more impressive (configure live), (b) is simpler and safer. Default recommendation: (b) status + test + clear docs now, (a) as a follow-up.
- **Surface the explanation text** on every incident (Stage 3a), clearly labeled "AI explanation (gpt-4o-mini)" or "Template explanation (no LLM configured)" so the user always knows which produced it.
- Add the `llm_explanation_*` env to the compose stack as **commented-out examples** so it's obvious how to turn it on, and document that the default is the offline template (honest).

### 3d. Provenance on the headline numbers
Every Overview stat links/expands to its source: "noise reduction 92%" → "8 situations from 214 alerts" (the real counts it's computed from); a diagnosis → the events that produced it. Nothing shown without a receipt. Remove any stat that can't be sourced.

---

## Acceptance criteria

1. **The approve gate works:** approve → the gate yields to a real outcome (resolved/failed) driven by the server; buttons don't reappear. Reject likewise. No hardcoded outcome text — the UI shows the real `health_after`.
2. **Every incident has a readable narrative:** what broke (metric+value), the z-score, the ranked hypotheses with evidence, the LLM/template explanation (labeled), the timeline, the real outcome + steps (dry-run labeled). Verified live on a Meridian fault.
3. **"Under the hood" is visible:** a system/debug view shows the active correlator, live baselines, the LLM provider status badge, backends, and remediation mode — all from real endpoints.
4. **The LLM is provable:** with an endpoint configured, the badge shows connected + the model, and the real LLM text appears on incidents; without one, it honestly says "template (no LLM)". A test-connection path exists.
5. **No fabricated numbers in live mode:** the hardcoded deltas/captions/gate-cards are removed or made real/labeled. Every headline stat is traceable to its source.
6. **Test-safe:** existing 414 tests green; new contract fields additive; mock mode still works; `ruff`/`build` clean.

## Suggested task ordering (for the plan) — staged delivery

**Stage 1 (ships first — stop the lying):**
1. Fix the approve-gate reconciliation + `working`/reject guards + hide dev reset (Incidents.tsx) + read the real outcome from `/outcomes` by situation_id (interim, before the join).
2. Remove/label the hardcoded chrome (Overview deltas/captions, Governance gate cards/RBAC).

**Stage 2 (the real fix — expose evidence):**
3. Additive contract fields (outcome.steps, DiagnosedSituation.enrichment, situation peak-score) + remediator returns steps + engine attaches score.
4. Widen the projection (member_events, hypothesis evidence+explanation, joined outcome, timeline, readable title, mode) + `GET /situations/{id}` + `GET /audit` + `GET /system` + correlation `GET /baseline`. Tests for each.

**Stage 3 (show it):**
5. Incident detail drill-down (member events, hypotheses+evidence+explanation, timeline, real outcome+steps).
6. "Under the hood" system/debug view + LLM provider status badge + provenance expanders.
7. LLM settings/status + surface labeled explanation + compose env examples + docs (honest: default is template).
8. ADR-021 (evidence-exposure read path + honesty pass) + docs.

Rationale: bugs first (immediate honesty), then the projection is the linchpin everything else needs, then UI on top. Each stage independently reviewable and shippable.
