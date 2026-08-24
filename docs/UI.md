# docs/UI.md — The React operator console

A guide to the console for the demo and the report: what each view shows, how mock vs. live
mode differ, the real-time transport underneath, and the Apple-light visual repaint. Read this
alongside [docs/OPERATIONS.md](OPERATIONS.md) (env switches, the auth model) and
[docs/DEMO.md](DEMO.md) (the guided walkthrough script).

This document is text-first. It has no screenshots — see **Screenshots** at the end for why and
what to do about it.

## Modes: mock vs. live

The console runs in one of two data modes, selected by `VITE_DATA_MODE` in `frontend/.env.local`:

- **`mock` (default in dev/CI).** No backend required. Every view reads static/scripted data from
  `frontend/src/data/mock.ts`. Data loads once per view mount — no polling, no SSE connection. This
  is what `npm run dev` gives you out of the box and what CI's `npm run build` type-checks against.
- **`live`.** The console talks to the real `read-service` (`VITE_READ_URL`, default
  `http://localhost:8007`) and `governance-service` (`VITE_GOV_URL`, default
  `http://localhost:8005`). Views subscribe to the SSE stream for near-real-time updates, with a
  5-second-poll fallback if the stream can't be used. See [Run it live](../README.md#run-it-live-real-data-local-free)
  in the README for the full local setup.

Every view in this doc calls out what changes between the two modes.

## The five views

The console has five tabs, in this order: **Overview · Incidents · Pipeline · Governance ·
Audit** (`frontend/src/components/Shell.tsx`).

### Overview

The landing dashboard: a hero noise-reduction metric, MTTR and auto-remediated-% mini-stats with
sparklines, a fleet-health strip, a live outcomes ticker, and playbook-graduation progress bars.

- **Mock mode:** all figures — the sparkline series, the six-service fleet list, the metrics —
  come from `frontend/src/data/mock.ts`, unchanged from before this effort.
- **Live mode:** metrics (`noiseReductionPct`, `mttrMinutes`, `autoRemediatedPct`,
  `alertsIngested`, etc.) come from the real `GET /metrics` on the read-service. Sparklines are
  built from a rolling client-side buffer (last 40 samples, `frontend/src/views/Overview.tsx`) of
  real `/metrics` snapshots fed by each live update — real recent history, not synthetic noise.
  **Fleet health** is derived in the browser, not faked: the console can only reach the two
  services it has a configured base URL for (`read` and `governance`, via `VITE_READ_URL` /
  `VITE_GOV_URL`), so it pings their `GET /health` directly. The other four backend services run
  on ports the frontend has no env var for and are shown **"degraded,"** never invented as
  "ok" — this is the fix for the mock-leaking-into-live bug the effort set out to close. There is
  no `/fleet` endpoint on the read-service; this is entirely a frontend derivation, one of the two
  options the design spec allowed.

### Incidents

The existing HITL work queue: open situations with severity, status, and suggested runbook, with
Approve/Reject actions that call `POST /approvals/{id}/decide` on governance. Unchanged by this
effort except for its data source, which now goes through `useLiveData` instead of the old
poll-only `useData` (see **SSE architecture** below) — the view's own logic and layout did not
change.

### Pipeline (new)

A new tab: five stage lanes — **Detected → Diagnosed → Gate · HITL → Acting → Resolved**
(`frontend/src/views/Pipeline.tsx`). Every open incident renders as one card; as its status
changes, the card glides from its old lane to its new one instead of popping in fresh.

- **How the animation works:** all cards live in a single flat, stably-keyed list (one
  `list.map(s => <motion.div key={s.id} layout="position">)`), with each card's lane derived from
  its status and placed by CSS grid column. Because the element instance persists across
  re-renders (same key, same parent), framer-motion's `layout` prop can measure the card's box
  before and after a lane change and FLIP-animate the move — this is what makes the glide work;
  five separate per-lane lists (one `.map()` per lane) would not, because a card moving lanes
  would unmount from one list and mount fresh in another, with no prior box to animate from.
- **Lane mapping** (`laneOf` in `Pipeline.tsx`): `resolved` → Resolved; `failed` → Gate (a failed
  attempt is routed back to a human, not to Acting); `acting` → Gate if `hitl_mode` is `"hitl"`,
  else Acting; `diagnosed` → Diagnosed; anything else → Detected. Suppressed situations are
  filtered out before lane placement — they don't appear in any lane.
- **The Gate lane carries the approve/reject actions**, reusing the same `decideApproval` call
  Incidents uses.
- **Mock mode:** a small scripted set of three incidents advances through
  detected → diagnosed → acting → resolved on a 2.6s timer, staggered so no lane sits empty — the
  view demos with no backend running.
- **Live mode:** driven by the real situations list from `/situations`, refreshed on each SSE
  nudge (or the 5s poll fallback). Because state arrives as whole-list snapshots rather than
  per-transition events, a card can visibly skip a lane between two updates (e.g.
  detected → diagnosed in one refresh); the FLIP animation interpolates directly from the old box
  to the new one regardless of how many lanes it crossed, so this reads correctly rather than
  glitching.
- Honors `prefers-reduced-motion` (the layout transition is disabled, not just shortened, when the
  OS setting is on).

### Governance

The control-plane view: RBAC/reversible-only/HITL gate explainer cards, the audit trail, and the
playbook registry. Unchanged by this effort except, like Incidents, its data now flows through
`useLiveData`.

### Audit (new)

A new tab: a filterable explorer over the audit trail (`frontend/src/views/Audit.tsx`),
independent of the audit panel already embedded in Governance. Filters by **actor** (substring
match), **decision** (`allow` / `deny` / `pending`, or "All"), and **correlation_id** (substring
match) — reconstructing one incident's full journey across services from the audit log alone.
Read-only: it only calls `GET /audit` via `loadAudit`/`useLiveData`, never writes. Null-safe empty
states are shown both when there is no audit data at all and when filters produce zero matches.

## SSE architecture (live mode)

Before this effort, every view polled its backend endpoint every 5 seconds. Live mode now pushes:
the read-service exposes `GET /stream` (Server-Sent Events), and the console's `useLiveData` hook
opens one `EventSource` per data loader in live mode, re-running the existing `/situations`,
`/outcomes`, `/metrics`, `/audit`, `/playbooks` fetches whenever a nudge arrives.

**What's on the wire.** The stream carries a single generic event shape, `{"type": "changed"}` —
not a diff, not a typed payload per mutation. On receipt the client just re-runs its existing
loader and re-fetches the full snapshot. This keeps the client contract trivial: no per-event
schema to keep in sync, and the projection needs no per-event serialization logic. A `: keepalive`
comment line is sent every 15 seconds so intermediary proxies don't time out an idle connection.

**Thread → async fan-out.** The read-service's projection (`ReadModel`,
`services/read/projection.py`) is mutated by consumer threads — one daemon thread per Redis
Streams topic (`services/read/consumer.py`) — while `/stream`'s SSE generators run on the uvicorn
event loop. `asyncio.Queue` isn't safe to write to from another thread, so `ReadModel` uses the one
stdlib primitive built for exactly this handoff: `loop.call_soon_threadsafe(...)`. Each `/stream`
connection gets its own `asyncio.Queue` via `ReadModel.subscribe()` (called on the loop thread,
from inside the route); every `apply_detected/apply_diagnosed/apply_outcome/apply_suppressed` call
finishes by calling `publish({"type": "changed"})`, which snapshots the subscriber set under a
lock and schedules delivery onto the loop for each one. No new dependency — this is `asyncio` +
`threading` from the standard library, nothing else.

**Backpressure: lossy, not blocking.** Each subscriber queue is bounded (maxsize 1000). If a
client falls behind and its queue fills, the oldest queued event is dropped to make room for the
new one — the delivery never blocks, and the consumer thread publishing the event never sees
`QueueFull`. This is intentionally lossy: the read-model projection is rebuildable from the Redis
event stream at any time, so a client that missed a nudge simply gets the next one (or reconnects
and re-fetches the full snapshot). Blocking or buffering unboundedly would trade a dropped
UI-refresh-hint for memory growth or a stalled consumer thread — the wrong trade for a "live but
disposable" nudge stream.

**Auth under `AUTH_MODE=token`: query-param token, `/stream` only.** The browser's `EventSource`
constructor takes only a URL and a `withCredentials` flag — it cannot set an `Authorization`
header, so the standard Bearer-header gate the rest of the API uses ([ADR-017](../architectural.md#adr-017--edge-authentication))
is structurally unreachable for this one endpoint. The read-service instead accepts the token as
`?token=` on `GET /stream` only, validated in-route with the same `hmac.compare_digest` timing-safe
comparison the header path uses (mirroring `common/auth.py:is_authorized`, including its
`bool(auth_token)` guard against an empty-token accidental-open). A custom `auth_exempt` predicate
passed into `create_app` exempts exactly `GET /stream` from the header gate — every other endpoint
on every service is unaffected. On the frontend, `openStream()` (`frontend/src/data/api.ts`) builds
the `EventSource` URL with `URL.searchParams.set("token", AUTH_TOKEN)` when a token is configured,
reusing the same `VITE_AUTH_TOKEN` the rest of the console already sends as a Bearer header.

**Honest limit.** This is acceptable specifically because the console's auth model is a **shared
demo token** — `docs/OPERATIONS.md` already documents `VITE_AUTH_TOKEN` as baked into the client
bundle at build time, so anyone who can load the bundle already has it. Putting a copy of that same
token in a query string (and therefore in access logs, and in a `Referer` header if one is ever
sent onward) leaks nothing a bundle-holder didn't already have. **If per-user tokens or a real IdP
are ever introduced, `/stream` auth must be revisited** — this decision does not extend to that
model. See [ADR-018](../architectural.md#adr-018--real-time-console-read-path-sse) for the full
reasoning, including why a cookie-based alternative was rejected.

**Fallback to polling.** `useLiveData` (`frontend/src/hooks/useLiveData.ts`) opens the
`EventSource` in live mode and refreshes on every message; if the stream reports an error that
`EventSource`'s own auto-reconnect doesn't resolve, the hook falls back to the same 5-second
`setInterval` poll the console used before this effort. Mock mode is untouched — one load per
mount, no stream, no poll.

## The Apple-light repaint

The whole console was repainted from its original dark theme to Apple's actual light website
palette: white (`#FFFFFF`) and near-white (`#F5F5F7`) backgrounds, `#1D1D1F` ink with a
`6E6E73` / `86868B` / `C7C7CC` gray ramp, `#0071E3` system blue as the accent (replacing the
previous teal), and Apple's system colors for status — `#34C759` green (ok), `#FF9500` amber
(warn), `#FF3B30` red (critical), `#5E5CE6` indigo (info).

This is centralized in four `tailwind.config.js` token groups (`ground`, `ink`, `signal`, `sev`)
plus a re-tuned shadow set (`lift`, `glow`, `inset`), so most components pick up the new palette
automatically through the tokens they already used. On top of that, roughly 55 hardcoded utility
classes that assumed a dark background — `white/[0.0x]` hairlines and fills, inset-white
highlights, teal glow shadows, dark-tuned button text — were individually re-tuned to their
light-mode equivalents (generally `black/[0.0x]` at roughly double the alpha, since black-on-white
needs more opacity to read than white-on-dark did). `index.css`'s base layer also flipped
(`color-scheme: dark → light`, body background/ink, `::selection`, focus ring), the film-grain
overlay was removed outright (Apple's site has none), and the ambient mesh gradient was softened to
a faint near-white wash.

There is no dark/light toggle — this was a one-way repaint to light, not a theme system. A toggle
was considered and declined for this effort.

## Screenshots

This effort did not capture screenshots of the running console — the views above are described
text-first instead. If you are building this doc out for a demo deck or a written report, the
five natural capture points are:

- (screenshot: Overview — live mode, fleet health strip + sparklines populated)
- (screenshot: Incidents — an open situation awaiting approval)
- (screenshot: Pipeline — mid-animation, a card gliding between lanes)
- (screenshot: Governance — the gate explainer cards + audit panel)
- (screenshot: Audit — a filtered result set)

No screenshot files exist in this repo for this effort; do not treat any image elsewhere in the
repo as one of these five unless you added it yourself.
