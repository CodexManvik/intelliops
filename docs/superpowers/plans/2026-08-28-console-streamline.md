# Console Streamline & Honesty Pass — Implementation Plan (PR A)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Simplify two UIs. IntelliOps → 3 pages (Incidents · Governance · Settings), no break controls; every metric explained; audit timestamps + pagination fixed. Meridian → trim its 4-view UI to Operations (the break panel, kept in full) + a minimal Dashboard; cut Submit + Reports.

**Architecture:** Pure frontend/presentation change across two separate Vite/React apps (`frontend/` = IntelliOps, `services/meridian/ui/` = Meridian). No backend/engine change. IntelliOps merges Overview+Pipeline into Incidents and Audit into Governance, deletes the retired views, and fixes the `timeAgo` ISO-string bug + adds audit pagination. Meridian drops two fluff views and their nav/routes and rebuilds its `dist/`.

**Tech Stack:** React 18 + TypeScript + Vite + Tailwind + framer-motion (both apps). `npm --prefix frontend run build` for IntelliOps; `npm --prefix services/meridian/ui run build` for Meridian. Backend gate: `uv run pytest -m "not postgres and not kafka"`, `ruff check .`, `ruff format --check .` (must stay green — no backend change expected).

**Spec:** `docs/superpowers/specs/2026-08-28-console-streamline-design.md`

## Global Constraints

- **No engine/logic change.** Backend stays byte-identical unless a task explicitly adds an additive `?limit=` to `GET /audit` (optional — the plan does pagination client-side by default).
- **No fabricated data.** Every number shown is real and labeled with what it means + how it's computed. Continue the honesty rule: no hardcoded deltas/captions dressed as real.
- **Gates:** `npm --prefix frontend run build` clean; `npm --prefix services/meridian/ui run build` clean; `uv run pytest -m "not postgres and not kafka"` green; `ruff check .` + `ruff format --check .` clean.
- **Mock mode:** IntelliOps `VITE_DATA_MODE=mock` must render all 3 pages (mock fixtures cover the merged views).
- **Commit trailer** on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Apple-light aesthetic preserved.** Reuse existing primitives (`Bezel`, `Eyebrow`, `timeAgo`, the `text-ink*`/`rounded-*`/`signal` token classes). Do not invent a new visual language.
- **Real metric formulas (verified from `services/read/projection.py:metrics()`), use verbatim in UI copy:**
  - Noise reduction % = `1 − (situations ÷ raw alerts)` × 100
  - MTTR = mean of `(outcome.ts − first_seen)` over successes, in minutes
  - Auto-remediated % = `auto-mode outcomes ÷ all outcomes` × 100
  - Success rate = `successes ÷ all outcomes`
- **Real Meridian FaultSpec (from `services/meridian/common.py:39`):** `type` ∈ `saturation|error|latency|crash`, `magnitude: float`, `duration_seconds: float | None`.

---

## Task 1: Fix the `timeAgo` ISO-string bug (the smallest honest fix)

**Files:**
- Modify: `frontend/src/components/primitives.tsx:178-185` (`timeAgo`)
- Test: `frontend/` has no unit-test runner wired for primitives; verify via `npm run build` + a runtime assertion in Task 4/8. (If a test file exists under `frontend/src`, add one; otherwise this is validated by the Governance render showing real times.)

**Interfaces:**
- Produces: `timeAgo(ts: number | string): string` — accepts epoch-ms numbers (existing callers: situations use epoch ms) AND ISO strings (audit records serialize `datetime` → ISO). Coerces via `new Date(ts).getTime()`.

**Root cause:** `AuditRow.ts` arrives from `GET /audit` as an ISO string (`AuditRecord.ts` is a pydantic `datetime`), but `timeAgo` does `Date.now() - ts` expecting a number → `NaN` → "NaNh ago". Situations pass epoch-ms numbers (projection uses `_epoch_ms`), which must keep working.

- [ ] **Step 1: Widen `timeAgo` to accept number | string**

Replace `timeAgo` (lines 178-185) in `frontend/src/components/primitives.tsx`:

```tsx
export function timeAgo(ts: number | string): string {
  const ms = typeof ts === "number" ? ts : new Date(ts).getTime();
  if (!Number.isFinite(ms)) return "—"; // never render NaN
  const s = Math.floor((Date.now() - ms) / 1000);
  if (s < 0) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}
```

(Adds day rollover + a `—` guard so a bad value never shows `NaN`. Existing number callers are unaffected: `typeof ts === "number"` path is identical to before.)

- [ ] **Step 2: Verify the build compiles**

Run: `npm --prefix frontend run build`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/primitives.tsx
git commit -m "fix(ui): timeAgo accepts ISO strings — audit timestamps no longer render NaNh"
```

---

## Task 2: New 3-item IntelliOps nav (Incidents / Governance / Settings)

**Files:**
- Modify: `frontend/src/components/Shell.tsx:7,9-16` (`View` union + `tabs` array)
- Modify: `frontend/src/App.tsx` (imports + route switch + default view)
- Test: none (verified by `npm run build` + Task 8 mock render)

**Interfaces:**
- Produces: `View = "incidents" | "governance" | "settings"`. Default landing = `incidents`. `App.tsx` renders `{view === "settings" && <System />}` (System.tsx is reused as "Settings"). Overview/Pipeline/Audit routes removed.

- [ ] **Step 1: Reduce the `View` union + tabs in Shell.tsx**

Replace line 7:

```tsx
export type View = "incidents" | "governance" | "settings";
```

Replace the `tabs` array (lines 9-16) — reuse existing Phosphor icons already imported:

```tsx
const tabs: { id: View; label: string; icon: JSX.Element }[] = [
  { id: "incidents", label: "Incidents", icon: <Waveform size={17} weight="light" /> },
  { id: "governance", label: "Governance", icon: <ShieldCheck size={17} weight="light" /> },
  { id: "settings", label: "Settings", icon: <Circuitry size={17} weight="light" /> },
];
```

Remove now-unused icon imports (`FlowArrow`, `ListMagnifyingGlass`, `SquaresFour`) from line 3 — keep `Circuitry`, `Pulse`, `ShieldCheck`, `Waveform`.

- [ ] **Step 2: Update App.tsx route switch**

Replace the imports (lines 3-8) — drop Overview, Pipeline, Audit; keep Incidents, Governance, System:

```tsx
import { Incidents } from "./views/Incidents";
import { Governance } from "./views/Governance";
import { System } from "./views/System";
```

Replace the default view (line 12): `const [view, setView] = useState<View>("incidents");`

Replace the render switch (lines 21-26):

```tsx
        {view === "incidents" && <Incidents />}
        {view === "governance" && <Governance />}
        {view === "settings" && <System />}
```

- [ ] **Step 3: Verify the build compiles**

Run: `npm --prefix frontend run build`
Expected: FAIL initially IF Overview/Pipeline/Audit are still imported anywhere else — grep `frontend/src` for those imports and confirm only App.tsx referenced them. The view FILES stay on disk until Task 7 deletes them; only the imports/routes are removed here. If the build fails on an unused-import lint, remove the dangling imports it names.
Expected after fixes: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Shell.tsx frontend/src/App.tsx
git commit -m "feat(ui): collapse IntelliOps nav to 3 pages (Incidents/Governance/Settings)"
```

---

## Task 3: Incidents page — add the explained metrics strip + recent-activity

**Files:**
- Modify: `frontend/src/views/Incidents.tsx` (add a metrics strip at the top + a small recent-outcomes strip; keep the existing queue + drill-down)
- Modify: `frontend/src/data/source.ts` (already exports `loadMetrics`, `loadOutcomes` — reuse)
- Test: none (verified by `npm run build` + Task 8 mock render)

**Interfaces:**
- Consumes: `loadMetrics` → `Metrics` (`{alertsIngested, situationsOpen, noiseReductionPct, mttrMinutes, autoRemediatedPct, suppressedToday, approvalsPending, successRate}`), `loadOutcomes` → `OutcomeRow[]`. Both already exist in `frontend/src/data/source.ts`.

**Design:** a compact metric-card row above the current "Situations, not alerts" queue. Each card is **click-to-expand** (a details disclosure) showing the plain-English meaning + the exact formula + the live counts. Below the queue, a small "recent outcomes" strip (last ~5 from `loadOutcomes`).

- [ ] **Step 1: Add a `MetricCard` with an expandable explanation**

At the top of `Incidents.tsx`, add a small component (above `export function Incidents`). Use the existing `Bezel`/token classes:

```tsx
const METRIC_DOCS: Record<string, { title: string; formula: string; meaning: string }> = {
  noise: {
    title: "Noise reduction",
    meaning: "How much raw alert noise IntelliOps collapsed into a handful of real incidents.",
    formula: "1 − (situations ÷ raw alerts ingested)",
  },
  mttr: {
    title: "MTTR",
    meaning: "Mean Time To Resolve — average time from an incident first appearing to it being fixed.",
    formula: "avg(resolved_at − first_seen) over successful remediations",
  },
  auto: {
    title: "Auto-remediated",
    meaning: "Share of fixes that ran automatically, because the playbook had earned autonomy (≥3 clean successes).",
    formula: "auto-mode outcomes ÷ all outcomes",
  },
  success: {
    title: "Success rate",
    meaning: "Share of remediations that verified healthy afterward.",
    formula: "successful outcomes ÷ all outcomes",
  },
};

function MetricCard({
  docKey, value, sub,
}: { docKey: keyof typeof METRIC_DOCS; value: string; sub: string }) {
  const [open, setOpen] = useState(false);
  const d = METRIC_DOCS[docKey];
  return (
    <button onClick={() => setOpen((o) => !o)} className="block w-full text-left">
      <div className="rounded-2xl border border-black/[0.06] bg-black/[0.02] p-4 transition-colors hover:bg-black/[0.04]">
        <div className="flex items-center justify-between">
          <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">{d.title}</span>
          <span className="font-mono text-2xs text-ink-4">{open ? "−" : "?"}</span>
        </div>
        <div className="mt-1 text-2xl font-semibold tracking-tightest tnum">{value}</div>
        <div className="font-mono text-2xs text-ink-3">{sub}</div>
        {open && (
          <div className="mt-3 border-t border-black/[0.06] pt-3">
            <p className="text-2xs leading-relaxed text-ink-2">{d.meaning}</p>
            <p className="mt-1.5 font-mono text-2xs text-ink-3">= {d.formula}</p>
          </div>
        )}
      </div>
    </button>
  );
}
```

- [ ] **Step 2: Wire the metrics strip into the Incidents render**

In `Incidents()`, add the loaders near the existing `useLiveData` call:

```tsx
  const { data: metrics } = useLiveData(loadMetrics, {
    alertsIngested: 0, situationsOpen: 0, noiseReductionPct: 0, mttrMinutes: 0,
    autoRemediatedPct: 0, suppressedToday: 0, approvalsPending: 0, successRate: 0,
  } as Metrics);
  const { data: recentOutcomes } = useLiveData(loadOutcomes, [] as OutcomeRow[]);
```

Import `loadMetrics, loadOutcomes` from `../data/source` and `Metrics, OutcomeRow` from `../data/types`. Render the strip at the very top of the returned JSX (before the existing header), showing real values with the live counts as the sub-line:

```tsx
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <MetricCard docKey="noise" value={`${metrics.noiseReductionPct}%`} sub={`${metrics.alertsIngested.toLocaleString()} alerts → ${metrics.situationsOpen} open`} />
        <MetricCard docKey="mttr" value={metrics.mttrMinutes > 0 ? `${metrics.mttrMinutes}m` : "—"} sub={metrics.mttrMinutes > 0 ? "mean time to resolve" : "no fixes yet"} />
        <MetricCard docKey="auto" value={`${metrics.autoRemediatedPct}%`} sub="ran without a human" />
        <MetricCard docKey="success" value={`${Math.round(metrics.successRate * 100)}%`} sub="verified healthy" />
      </div>
```

(The "—" for MTTR when there are no successful fixes yet honors "no fabricated data".)

- [ ] **Step 3: Add a compact recent-outcomes strip below the queue**

After the situations queue column (or below the whole grid), add a small strip so the Overview's live ticker isn't lost:

```tsx
      {recentOutcomes.length > 0 && (
        <div className="rounded-2xl border border-black/[0.06] bg-black/[0.02] p-4">
          <div className="mb-2 text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">Recent outcomes</div>
          <div className="space-y-1">
            {recentOutcomes.slice(0, 5).map((o, i) => (
              <div key={i} className="flex items-center gap-3 font-mono text-2xs">
                <span className="text-ink-3">{timeAgo(o.ts)}</span>
                <span className="w-40 truncate text-ink-2">{o.playbook_id}</span>
                <span className="ml-auto text-ink-3">{o.reason}</span>
              </div>
            ))}
          </div>
        </div>
      )}
```

(`timeAgo` is already imported in Incidents.tsx via primitives; if not, add it.)

- [ ] **Step 4: Verify the build compiles**

Run: `npm --prefix frontend run build`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/Incidents.tsx
git commit -m "feat(ui): Incidents page absorbs explained metrics strip + recent outcomes"
```

---

## Task 4: Governance page — merge the audit trail with fixed timestamps + pagination

**Files:**
- Modify: `frontend/src/views/Governance.tsx` (add the audit-trail section from Audit.tsx, paginated)
- Test: none (verified by `npm run build` + live render)

**Interfaces:**
- Consumes: `loadAudit` → `AuditRow[]` (already imported in Governance), `timeAgo` (now ISO-safe from Task 1).

**Design:** Governance already renders the 3 gates + RBAC + playbook registry + a short audit list. Replace its audit block with a **paginated** version (show N, "load more"), using the fixed `timeAgo`. The dedicated Audit page is being retired (Task 7), so its filter capability can optionally move here — but the minimum is: paginated real-timestamp audit rows.

- [ ] **Step 1: Add pagination state + a paginated audit section**

In `Governance()`, add near the top:

```tsx
  const PAGE = 25;
  const [shown, setShown] = useState(PAGE);
```

(Import `useState` if not already imported.)

Replace the existing audit-list block with a paginated one (the newest records first — the backend returns them; if not ordered, sort by `ts` desc). Render `audit.slice(0, shown)`:

```tsx
      <Bezel coreClassName="p-6">
        <div className="mb-4 flex items-center justify-between gap-3">
          <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">
            Immutable audit trail · threaded by correlation_id
          </span>
          <span className="font-mono text-2xs text-ink-3">
            showing {Math.min(shown, audit.length)} of {audit.length}
          </span>
        </div>
        <div className="space-y-1">
          {audit.slice(0, shown).map((a, i) => (
            <div key={i} className="grid grid-cols-[auto_1fr_auto] items-center gap-3 rounded-lg px-2 py-2 font-mono text-2xs transition-colors hover:bg-black/[0.03]">
              <span className="text-ink-3">{timeAgo(a.ts)}</span>
              <span className="truncate">
                <span className="text-ink-2">{a.actor}</span>
                <span className="text-ink-3"> {a.action} </span>
                <span className="text-ink">{a.resource}</span>
              </span>
              <span className={`${a.decision === "deny" ? "text-sev-crit" : a.decision === "pending" ? "text-sev-warn" : "text-sev-ok"}`}>{a.decision}</span>
            </div>
          ))}
        </div>
        {shown < audit.length && (
          <button onClick={() => setShown((n) => n + PAGE)} className="mt-3 w-full rounded-xl border border-black/[0.08] bg-black/[0.03] py-2 font-mono text-2xs text-ink-2 transition-colors hover:bg-black/[0.05]">
            Load {Math.min(PAGE, audit.length - shown)} more
          </button>
        )}
        {audit.length === 0 && (
          <div className="rounded-2xl border border-black/[0.06] p-8 text-center text-ink-3">No audit records yet — decisions appear here as the gate evaluates them.</div>
        )}
      </Bezel>
```

- [ ] **Step 2: Verify the build compiles**

Run: `npm --prefix frontend run build`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/Governance.tsx
git commit -m "feat(ui): Governance absorbs the audit trail — real timestamps + pagination"
```

---

## Task 5: Delete the retired IntelliOps views + update mock fixtures

**Files:**
- Delete: `frontend/src/views/Overview.tsx`, `frontend/src/views/Pipeline.tsx`, `frontend/src/views/Audit.tsx`
- Modify: `frontend/src/data/mock.ts` if any deleted view was its only consumer of a mock export (leave shared exports)
- Test: `npm --prefix frontend run build` (must have zero dangling imports)

**Interfaces:** none produced; this removes dead code after Tasks 2-4 stopped routing to them.

- [ ] **Step 1: Confirm nothing still imports the retired views**

Grep `frontend/src` for `views/Overview`, `views/Pipeline`, `views/Audit`. After Task 2 only App.tsx referenced them and that's fixed. If anything else imports them, resolve it first.

- [ ] **Step 2: Delete the three files**

```bash
git rm frontend/src/views/Overview.tsx frontend/src/views/Pipeline.tsx frontend/src/views/Audit.tsx
```

- [ ] **Step 3: Verify the build compiles clean**

Run: `npm --prefix frontend run build`
Expected: PASS with no unresolved imports. If the build flags an unused export in `mock.ts` (e.g. `series`, `system` only used by a deleted view), leave shared ones; remove only exports that are now genuinely unreferenced (grep to confirm).

- [ ] **Step 4: Commit**

```bash
git add -A frontend/src
git commit -m "chore(ui): delete retired Overview/Pipeline/Audit views (absorbed into 3 pages)"
```

---

## Task 6: Trim the Meridian UI to Operations + minimal Dashboard

**Files:**
- Modify: `services/meridian/ui/src/App.tsx` (drop submit/reports routes + imports)
- Modify: `services/meridian/ui/src/components/AppShell.tsx` (drop Submit/Reports nav items)
- Delete: `services/meridian/ui/src/views/Submit.tsx`, `services/meridian/ui/src/views/Reports.tsx`
- Keep: `services/meridian/ui/src/views/Operations.tsx` (unchanged — the break panel + custom builder), `services/meridian/ui/src/views/Dashboard.tsx` (minimal, kept)
- Rebuild: `services/meridian/ui/dist/` so the gateway serves the trimmed app

**Interfaces:** none (self-contained Meridian UI). `useBackgroundTraffic` calls the backend endpoints `submitData`/`loadReports` directly — removing the VIEWS does NOT break it (verified: `services/meridian/ui/src/data/useBackgroundTraffic.ts` imports from `./api`, not the views).

- [ ] **Step 1: Reduce the Meridian `View` union + routes**

In `services/meridian/ui/src/App.tsx`: change line 9 to `export type View = "dashboard" | "operations";` and line 11 to `const VALID_VIEWS: View[] = ["dashboard", "operations"];`. Remove the `Submit`/`Reports` imports (lines 4-5) and their render lines (60-61). Keep `Dashboard` (with `lastTick`) and `Operations`.

- [ ] **Step 2: Reduce the Meridian nav**

In `services/meridian/ui/src/components/AppShell.tsx`, change `NAV_ITEMS` (lines 5-10) to:

```tsx
const NAV_ITEMS: { id: View; label: string; hint: string }[] = [
  { id: "dashboard", label: "Dashboard", hint: "System status" },
  { id: "operations", label: "Operations", hint: "Break a service" },
];
```

- [ ] **Step 3: Delete the two fluff views**

```bash
git rm services/meridian/ui/src/views/Submit.tsx services/meridian/ui/src/views/Reports.tsx
```

- [ ] **Step 4: Verify the Meridian UI builds**

Run: `npm --prefix services/meridian/ui run build`
Expected: PASS. If the build flags `submitData`/`loadReports`/`randomSubmission` as unused (they're still used by `useBackgroundTraffic`, which is retained), no change needed — only the views were removed. If it flags a genuinely-unused import in App.tsx/AppShell.tsx, remove it.

- [ ] **Step 5: Commit the source changes (dist/ is gitignored + Docker-built — do NOT commit dist)**

CONFIRMED: `services/meridian/ui/dist/` is gitignored, and `deploy/Dockerfile.meridian` is a multi-stage build that runs `npm run build` at image-build time (stage `ui`, `COPY --from=ui /ui/dist`). So the trimmed UI reaches the running stack via `docker compose up --build` — no `dist/` commit is needed or possible. Commit only the tracked source:

```bash
git add services/meridian/ui/src services/meridian/ui/package.json
git rm services/meridian/ui/src/views/Submit.tsx services/meridian/ui/src/views/Reports.tsx 2>/dev/null || true
git commit -m "feat(meridian): trim UI to Operations + minimal Dashboard (cut Submit/Reports)"
```

(The Step 4 local `npm run build` is only to VERIFY the trimmed source compiles — its `dist/` output stays uncommitted. To see it locally, run the Meridian UI dev server; to see it in the stack, rebuild the image.)

---

## Task 7: Mock-mode + honest-copy sweep + final verification

**Files:**
- Modify: `frontend/src/data/mock.ts` (ensure the merged views have representative mock data — metrics, outcomes, audit already have mock exports; confirm they're non-empty)
- Modify: any remaining buzzword copy in Incidents/Governance/System headers
- Test: full build + mock render + backend gate

**Interfaces:** none.

- [ ] **Step 1: Confirm mock mode renders all 3 pages**

Set `frontend/.env.local` to `VITE_DATA_MODE=mock` (or verify the mock data path). Ensure `mock.metrics`, `mock.outcomes`, `mock.audit`, `mock.situations`, `mock.playbooks`, `mock.system` all exist and are non-empty so the 3 pages render with content. (These already exist from prior work; just confirm none were removed in Task 5.)

- [ ] **Step 2: Honest-copy sweep**

Scan the three surviving views' header copy (`Incidents.tsx`, `Governance.tsx`, `System.tsx`) for buzzwords/overclaims and unexplained numbers. Every visible number must be real or labeled. Remove any remaining hardcoded literal dressed as data. Keep the copy plain and specific (no "seamless"/"unlock"/marketing filler).

- [ ] **Step 3: Run all gates**

```bash
npm --prefix frontend run build
npm --prefix services/meridian/ui run build
uv run pytest -m "not postgres and not kafka" -q
ruff check . && ruff format --check .
```

Expected: all PASS. (The backend suite must be unchanged — no backend files were touched. If `pytest`/`ruff` differ from baseline, something backend leaked in — investigate.)

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore(ui): mock-mode + honest-copy sweep; all gates green"
```

---

## Self-Review checklist (run after execution, before the PR)

1. **IntelliOps = 3 nav items** (Incidents, Governance, Settings), no break controls — Tasks 2, (no break panel anywhere).
2. **Every metric explained** — Task 3's `MetricCard` expands to meaning + formula + live counts; MTTR shows "—" when no data.
3. **Full incident story inline** on Incidents (the existing drill-down is retained; Pipeline folded in) — Tasks 2-3.
4. **Governance: real timestamps (no NaN) + pagination** — Tasks 1, 4.
5. **Meridian = Operations + minimal Dashboard**, Submit/Reports gone, break panel + custom builder intact, backend untouched — Task 6.
6. **Both builds clean; mock mode renders; backend suite green** — Task 7.
