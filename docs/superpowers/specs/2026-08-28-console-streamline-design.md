# Console Streamline & Honesty Pass — Design Spec

**Date:** 2026-08-28
**Owner:** Manvik (integration lead)
**Status:** design (architectural) — from direct user feedback that the 6-page console is cluttered, some data looked fake (it wasn't — a timestamp bug), and the Docker images are needlessly huge.

## The problem (from the user, verified against code)

1. **Too many pages.** The IntelliOps console has 6 views (Overview, Incidents, Pipeline, Governance, Audit, System). The operational story is scattered; the user has to hop pages to follow one incident. Verdict: cluttered.
2. **Looked fake, but wasn't.** The Governance/Audit page renders `NaNh ago` for every timestamp and dumps ~675 unpaginated rows. **Root cause (verified):** `timeAgo(ts: number)` in `frontend/src/components/primitives.tsx:178` does `Date.now() - ts`, but the backend `AuditRecord.ts` is a `datetime` that serializes to an **ISO string**, so the subtraction is `NaN`. The data is 100% real (every RCA diagnose + every action gate writes a real `AuditRecord`); only the rendering is broken, and nothing ever trims the log so it grows unbounded.
3. **Metrics without meaning.** The Overview numbers (noise reduction, MTTR, auto-remediated) are shown as bare figures with no explanation of what they mean or how they're computed.
4. **Docker images are ~1.5GB each (verified via `docker images`).** Cause: one shared image (`deploy/Dockerfile`) installs the full data-science stack (`scikit-learn`, `scipy` via sklearn, `numpy 2`, `river`) for **all 13 services**, but only `correlation` imports them. Twelve services carry ~1GB of unused ML libraries.

## Goal

Two separate UIs, both simplified, each on its own port:
1. A **3-page IntelliOps console** — **Incidents · Governance · Settings** — that tells the whole observability story with no page-hopping, explains every number it shows, and fixes the timestamp/pagination bugs. **No break controls** live here — IntelliOps only *watches*.
2. A **simplified Meridian UI** — collapse its current 4 views (Dashboard, Submit, Reports, Operations) down to just the **Operations** panel (where you break things, incl. the custom scenario builder), plus optionally a minimal landing. The fake financial-portal views (Submit, Reports) are cut. **You break things in Meridian, then switch to IntelliOps to watch** — that's the intended flow.

Separately (PR B), slim the Docker images so only `correlation` carries the ML stack.

## Non-goals / explicit constraints

- **Meridian's BACKEND is untouched.** Its 4 backend services keep running on their ports (gateway 8008, etc.), emitting real metrics — IntelliOps still monitors a real multi-service system. Only Meridian's *UI* is simplified (cut Submit + Reports; keep Operations). The fault API (`/admin/fault`, the gateway ops proxy) is unchanged.
- **The break panel stays in MERIDIAN, not IntelliOps.** IntelliOps gets NO break controls — it is purely the observer console.
- **No engine/logic change.** Detection, RCA, governance, remediation logic stay exactly as they are. This is a presentation + honesty + packaging effort.
- **No new heavy dependency** in either frontend.
- **Split delivery:** PR A = both UI simplifications (IntelliOps 3-page + Meridian trim) + bug fixes. PR B = the Docker slimming (separate plan / focused follow-up).

## Global Constraints

- **Test-safe.** Backend contract changes (if any) are additive with defaults; the existing suite (~427) stays green. `uv run pytest -m "not postgres and not kafka"` green; `ruff check .` + `ruff format --check .` clean; `npm --prefix frontend run build` clean.
- **No fabricated data.** Every number shown is computed from real data and labeled with what it means. No hardcoded deltas/captions dressed as real (continue the honesty rule from the prior effort).
- **Commit trailer** on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Mock mode still works** — the console builds and renders in `VITE_DATA_MODE=mock` with representative fixtures for the new/merged views.

---

## PR A — The 3-page console

### Navigation
Replace the 6-item `View` union + nav (`frontend/src/components/Shell.tsx:7`, `App.tsx`) with **3**: `"incidents" | "governance" | "settings"`. Default landing = `incidents`. Keep the existing Apple-light aesthetic + primitives (`Bezel`, `Eyebrow`, the token classes). Add a persistent top-right **"Break a service"** button that opens the break panel (a drawer/modal), available from any page.

### Page 1 — Incidents (merges Overview + Incidents + Pipeline)

**A. Metrics strip (top).** A compact row of the REAL metrics from `GET /metrics` (`services/read/projection.py:metrics()`), each card **click-to-expand** into a plain-English explanation. The four metrics and their VERIFIED formulas (copy these into the UI copy exactly):
- **Noise reduction %** — "How much alert noise IntelliOps collapsed." Formula: `1 − (open+closed situations ÷ raw alerts ingested)`, shown as a percent. Expander shows the live counts: e.g. "91% = 1 − 8 situations ÷ 8,420 alerts."
- **MTTR (Mean Time To Resolve)** — "Average time from an incident first appearing to it being fixed." Formula: mean of `(outcome.ts − situation.first_seen)` over successful remediations, in minutes.
- **Auto-remediated %** — "Share of fixes that ran automatically (no human needed), because the playbook had earned autonomy." Formula: `auto-mode outcomes ÷ all outcomes`.
- **Success rate** — "Share of remediations that verified healthy." Formula: `successes ÷ all outcomes`.
- Also surface **alerts ingested** and **situations open** as context (already in `/metrics`).
- Rule: if a metric can't be computed (no outcomes yet), show "—" with "no data yet", never a fake number.

**B. Open situations list + inline incident story.** Bring the current `Incidents.tsx` queue + drill-down in as-is (it already shows member events, z-score vs baseline, ranked hypotheses with evidence + the labeled AI/template explanation, timeline, Approve/Reject, real outcome + steps — all shipped in the prior effort). Fold the current standalone **Pipeline** view's per-stage rail into this inline story (it already partially is). The Overview's "live outcomes ticker" can appear as a small recent-activity strip under the list. Drop the Overview's fabricated/graduation-theater chrome that isn't backed by real data.

**C. What's cut from Overview:** the hero "on target"/"target band" chrome, the playbook-graduation bento (keep the real graduation data only if it earns its place on Governance; otherwise drop). Fleet-health strip can stay as a small honest indicator (it already refuses to fake "ok").

### Page 2 — Governance (Governance + Audit merged, bugs fixed)

- The **3 real enforced safety gates** (RBAC fail-closed, reversible-only, human-in-the-loop) — kept as the capstone/compliance story, driven by the real config where possible (as today).
- The **RBAC policy** table + **playbook registry** (real data from `/playbooks`).
- The **audit trail** from `GET /audit` (governance) — with TWO fixes:
  1. **Timestamp fix.** `AuditRow.ts` arrives as an ISO string, not epoch ms. Fix `timeAgo` (or the audit-row rendering) to parse the ISO string: `new Date(ts).getTime()` before the delta, OR change the read/governance projection to emit epoch ms consistently. **Decision:** fix on the frontend — make `timeAgo` accept `number | string` and coerce via `new Date(ts).getTime()` (also protects the other callers). Verify no `NaN` renders.
  2. **Pagination.** The audit list must paginate — render the most recent N (e.g. 20–50) with a "load more" (or a bounded fetch), not all 675+. Prefer a client-side "show more" over the full list; optionally add a `?limit=` to `GET /audit` (additive) if the payload is large. Show "showing X of Y".

### Page 3 — Settings (System view, as shipped, kept)

The current System view (LLM provider dropdown from the just-merged work + live internals: correlator kind, baselines, backends, remediation mode). This page is already good — it moves under "Settings" and stays.

### IntelliOps frontend files touched (PR A)
- `frontend/src/components/Shell.tsx` — 3-item nav (no break button).
- `frontend/src/App.tsx` — 3-route switch.
- `frontend/src/views/Incidents.tsx` — absorb metrics strip + pipeline rail + recent-activity.
- New `frontend/src/views/Governance.tsx` — merged governance + audit with fixes (or refactor the two existing).
- Keep `frontend/src/views/System.tsx` → routed as "Settings".
- Delete/retire `frontend/src/views/Overview.tsx`, `Pipeline.tsx`, `Audit.tsx` (their real content is absorbed).
- `frontend/src/components/primitives.tsx` — `timeAgo` accepts `number | string`.
- `frontend/src/data/api.ts` + `source.ts` + `types.ts` — audit pagination if server-side.

---

## PR A (part 2) — Simplify the Meridian UI

Meridian's console (`services/meridian/ui/`, a separate Vite/React app served by the gateway) currently has **4 views**: `Dashboard`, `Submit`, `Reports`, `Operations`. Only `Operations` (the SRE break panel) is load-bearing for the demo; `Submit` and `Reports` are fake financial-portal fluff. Simplify to **1–2 pages**.

### Keep & simplify
- **`Operations` (keep — it's the point).** This is the break panel: quick fault presets per service AND the custom scenario builder (service + `type` [`saturation|error|latency|crash`] + magnitude + duration → `POST` the fault). Keep the full capability; just make sure it's clean and clearly the primary page. This is where the user breaks Meridian.
- **`Dashboard` (keep minimal OR fold into Operations).** A slim "these services are running" landing gives Meridian a face so it reads as a real system. Keep a trimmed version, or make Operations the landing and drop Dashboard. Implementer's call during build; prefer the simpler of the two that still shows the services are alive.

### Cut
- **`Submit`** (fake "submit a financial filing" form) — delete the view + its nav entry.
- **`Reports`** (fake "generated reports" list) — delete the view + its nav entry.
- Remove their routes from `services/meridian/ui/src/App.tsx` and nav items from `services/meridian/ui/src/components/AppShell.tsx`.

### Meridian frontend files touched (PR A)
- `services/meridian/ui/src/App.tsx` — drop `submit`/`reports` routes.
- `services/meridian/ui/src/components/AppShell.tsx` — drop `Submit`/`Reports` nav items; nav is now Operations (+ optional Dashboard).
- Delete `services/meridian/ui/src/views/Submit.tsx`, `services/meridian/ui/src/views/Reports.tsx`.
- Trim or keep `services/meridian/ui/src/views/Dashboard.tsx`; keep `Operations.tsx`.
- Rebuild the Meridian UI (`npm run build` in `services/meridian/ui/`) so the gateway serves the trimmed `dist/`. Confirm the gateway's StaticFiles mount still serves it.

### Acceptance criteria (PR A)
1. **IntelliOps** has exactly 3 nav items (Incidents, Governance, Settings) and NO break controls.
2. Every metric on IntelliOps' Incidents page expands to a plain-English meaning + its real formula + the live counts it's computed from. No bare/fabricated numbers.
3. One incident's full story (what broke, z-score, ranked hypotheses + evidence + labeled explanation, timeline, Approve/Reject, real outcome + steps) is visible inline on the Incidents page without navigating away.
4. IntelliOps Governance shows the 3 real gates, RBAC/playbook data, and the audit trail with **real human-readable timestamps** (no `NaN`) and **pagination** (N shown of total).
5. **Meridian** UI is down to 1–2 pages: Operations (break presets + custom scenario builder, fully working) and optionally a minimal Dashboard. Submit and Reports are gone. Meridian's backend + fault API are unchanged; a fault fired from Meridian's Operations panel produces a real incident that appears in IntelliOps.
6. Both frontends build clean (`npm run build` in `frontend/` and in `services/meridian/ui/`); IntelliOps mock mode renders all 3 pages; existing backend suite green (no engine change).

---

## PR B — Slim the Docker images (separate delivery)

**Problem:** `deploy/Dockerfile` installs the full dependency set (incl. `scikit-learn`, `numpy`, `river`, `joblib`) into one image used by all 13 services; only `correlation` imports the ML stack. Result: ~1.5GB × 13.

**Approach (to be detailed in PR B's own plan):**
- Split dependencies so ML libs are an OPTIONAL group (e.g. a `correlation` extra in `pyproject.toml`), and build a **slim base image** (just FastAPI + pydantic + redis + sqlalchemy) for the non-ML services, with `correlation` getting the ML extra.
- Verify: `correlation` still trains/detects (its tests pass); the other services import nothing they now lack (run each service's tests + a compose smoke boot); image sizes drop to ~200–300MB for the non-ML services.
- Keep it test-safe: the dependency split must not change what any service can import at runtime.

**Acceptance (PR B):** non-correlation images are materially smaller (target < 400MB); `correlation` retains sklearn/river; full suite green; `docker compose up` boots all services healthy.

---

## Suggested task ordering (for the plan — PR A)

1. `timeAgo` accepts `number | string` (+ a test) — the smallest honest fix, unblocks Governance.
2. New 3-item IntelliOps nav in Shell + App route switch (Incidents / Governance / Settings), landing on Incidents. Retire Overview/Pipeline/Audit routes.
3. IntelliOps Incidents page: absorb the real metrics strip with click-to-expand explanations (real formulas) + the recent-activity strip; keep the existing drill-down.
4. IntelliOps Governance page: merge gates + RBAC + playbook registry + the audit trail with fixed timestamps + pagination.
5. Trim the Meridian UI: delete Submit + Reports views/routes/nav; keep Operations (break presets + custom builder) + optional minimal Dashboard; rebuild its `dist/`.
6. Mock fixtures for the merged IntelliOps views; both `npm run build`s + IntelliOps mock-mode pass; honest-copy sweep (no buzzwords, every number explained).

Rationale: fix the lying timestamp first, then restructure IntelliOps nav, then compose each IntelliOps page, then trim Meridian (independent of the IntelliOps work).
