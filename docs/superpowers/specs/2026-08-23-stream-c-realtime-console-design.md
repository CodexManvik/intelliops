# Stream C — Real-time Console + Live Pipeline View + Apple Repaint — Design Spec

**Date:** 2026-08-23
**Owner (this effort):** Manvik (integration lead, building Stream C on Member B's behalf; handed off via PR)
**Status:** design approved in brainstorming; hard unknowns research-verified (adversarial pass); ready for an implementation plan.

## Goal

Take the IntelliOps operator console from a 5-second poll to a real-time,
demo-grade product: sub-second live updates over SSE, a **live incident-journey
pipeline view** that animates incidents through the closed loop as events flow,
richer real-data dashboards (no mock leaking into live mode), an audit-trail
explorer, and a full repaint to **Apple's actual light website palette**.

## Non-goals

- No WebSocket (one-way server→client only; SSE is the fit — see Decision 1).
- No change to the write path or any backend contract's *meaning* (additive fields only).
- No per-user auth / IdP — the shared `VITE_AUTH_TOKEN` demo-token model is unchanged (documented limit).
- No dark/light toggle — this is a single-commit repaint to light (a toggle was offered and declined).
- No topology graph — the pipeline view is incident-journey lanes (chosen over a service topology graph).

## Global Constraints

- **Test-safe by default.** `VITE_DATA_MODE=mock` (default in dev/CI) and `AUTH_MODE=off` (default)
  behavior must be byte-unchanged. New live behavior is opt-in.
- **`npm run build` stays clean** (strict TS) and **`uv run pytest` / `ruff check` / `ruff format --check .`** stay green.
- **No new heavy dependency.** The SSE fan-out is stdlib-only (`asyncio` + `threading`). framer-motion (^11, installed 11.18.2) and Tailwind (v3.4.15) are already present.
- **StrictMode-safe animations.** Resting state is always the *visible* state; never let `opacity:0` be a resting state (the codebase's existing rule — App.tsx:12-14, view.css, useReveal.tsx). Effects that open an `EventSource` must close it in cleanup (StrictMode double-mounts effects in dev).
- **Commit trailer:** every commit ends with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Shared-file coordination:** this effort touches `services/base.py` usage (additive: read-service passes a custom `auth_exempt`), `services/read/app.py` + `projection.py` (read-service, Member B-adjacent, owned here), and `frontend/src/data/types.ts` (additive optional field only). No `common/contracts.py` change.

---

## Architecture overview

Three layers, built in dependency order:

1. **Backend transport** — a new `GET /stream` SSE endpoint on the read-service, fed by a
   stdlib pub/sub inside `ReadModel`. This is the foundation; everything real-time depends on it.
2. **Frontend subscription** — an `EventSource` client + a `useLiveData` hook that replaces the
   5s poll *in live mode only*, with graceful fallback to polling.
3. **Views** — the new Pipeline view, the real-data Overview, the audit explorer, and the
   Apple repaint (a cross-cutting restyle of everything).

Data shapes are unchanged: the SSE event is a tiny nudge (`{"type": "changed"}`); on any event
the client re-fetches the existing `/situations` `/outcomes` `/metrics` snapshots. This keeps the
client contract trivial and means the projection needs no per-event serialization.

---

## Decision 1 — Transport: SSE (not WebSocket)

One-way server→client. SSE runs over plain HTTP through the existing FastAPI/auth/CORS/proxy path
unchanged, `EventSource` has built-in auto-reconnect, and there is no client→server realtime need
(approve/reject stays the existing POST). WebSocket would add connection-lifecycle complexity and a
special auth-upgrade path for zero gain.

**Verified safe:** installed stack is FastAPI 0.133.1 / Starlette 1.0.1. `BaseHTTPMiddleware`
(the `_auth_gate` in `services/base.py`) streams responses via an anyio task group in this version —
it does **not** buffer the body — so a `StreamingResponse(media_type="text/event-stream")` passes
through the auth middleware without being buffered/broken. (The old pre-fix BaseHTTPMiddleware
SSE-buffering bug is not present here.)

---

## Decision 2 — SSE auth under `AUTH_MODE=token`: query-param token on `/stream` only

The browser `EventSource` constructor takes only a URL + a `withCredentials` flag — **it cannot set
the `Authorization` header**, so the existing Bearer-header gate is structurally unreachable for SSE.

**Chosen:** accept the token as `?token=` on `/stream` **only**, validated in-route with the same
`hmac.compare_digest` the header path uses. The read-service passes a custom `auth_exempt` predicate
that exempts exactly `GET /stream` from the header gate; `/stream` then runs its own query-token check.

**Why this is acceptable here:** the one downside is the token landing in access logs / referer — but
`docs/OPERATIONS.md` already documents `VITE_AUTH_TOKEN` as a **shared demo token baked into the client
bundle** ("anyone who can load the bundle has it"). A copy in a log leaks nothing a bundle-holder
didn't already have. A cookie path was rejected: the console is cross-origin (`:5173` bundle → `:8007`
read-service) and would force credentialed CORS (`allow_credentials=True` + a non-wildcard origin +
`SameSite=None; Secure`) — far more surface for a public demo token. **This decision is scoped to the
shared-token model; if per-user tokens are ever introduced, `/stream` auth must be revisited.**

**Exact backend shape** (`services/read/app.py`):

```python
import hmac
import json
import asyncio
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

def _auth_exempt(method: str, path: str) -> bool:
    # /stream is reached by the browser EventSource API, which cannot set the
    # Authorization header; it authenticates via ?token= inside the route
    # instead. Everything else keeps the standard Bearer-header gate. (/health
    # and /ready stay exempt via the base.py short-circuit, so they need not be
    # re-listed here.)
    return method == "GET" and path == "/stream"

app = create_app("read-service", auth_exempt=_auth_exempt)
app.router.lifespan_context = lifespan

def _stream_authorized(request: Request) -> bool:
    settings = get_settings()
    if settings.auth_mode != "token":        # AUTH_MODE=off → open, unchanged
        return True
    token = request.query_params.get("token", "")
    return bool(settings.auth_token) and hmac.compare_digest(token, settings.auth_token)
```

The `bool(settings.auth_token)` guard preserves the documented no-accidental-open-fallback rule
(a token-mode service with an empty `AUTH_TOKEN` rejects everything). This mirrors
`common/auth.py:is_authorized` exactly, swapping header extraction for `query_params`.

**Frontend URL build** (`frontend/src/data/api.ts`, reusing existing `READ` base + `AUTH_TOKEN`):

```ts
export function openStream(): EventSource {
  const url = new URL(`${READ}/stream`);
  if (AUTH_TOKEN) url.searchParams.set("token", AUTH_TOKEN);  // percent-encodes +, /, =
  return new EventSource(url.toString());   // NO { withCredentials: true } — conflicts with wildcard CORS
}
```

`EventSource` GET is a CORS "simple request" (no preflight); the existing wildcard-method,
no-credentials CORS in `services/base.py` returns the right `Access-Control-Allow-Origin`.

---

## Decision 3 — Thread→async SSE fan-out inside `ReadModel`

The hard constraint: `apply_detected/apply_diagnosed/apply_outcome/apply_suppressed` run on **daemon
consumer threads** (`services/read/consumer.py`, one thread per topic), while SSE generators drain on
the **uvicorn event loop**. `asyncio.Queue` is *not* thread-safe to call cross-thread — but
`loop.call_soon_threadsafe(...)` is the one asyncio API designed for exactly this: it schedules a
callback to run *on* the loop thread, where `queue.put_nowait` is then safe.

**Design (stdlib only, no janus / no run_in_executor):**

`ReadModel` gains a subscriber registry (a `set[asyncio.Queue]` guarded by a `threading.Lock`), a
captured event loop reference, and `subscribe()/unsubscribe()/publish()`. Consumer threads call
`publish(event)` at the end of each `apply_*`; `publish` marshals delivery onto the loop for every
subscriber. Each `/stream` generator owns exactly one queue.

```python
# services/read/projection.py — additions to ReadModel
import asyncio
import threading

# in __init__ (keep the existing max_outcomes/ttl_seconds/max_situations signature intact):
self._subscribers: set[asyncio.Queue] = set()
self._subs_lock = threading.Lock()
self._loop: asyncio.AbstractEventLoop | None = None

def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
    """Called once from the async lifespan so consumer threads can hand off."""
    self._loop = loop

def subscribe(self, maxsize: int = 1000) -> asyncio.Queue:
    """MUST be called on the event-loop thread (from the /stream coroutine)."""
    q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
    with self._subs_lock:
        self._subscribers.add(q)
    return q

def unsubscribe(self, q: asyncio.Queue) -> None:
    with self._subs_lock:
        self._subscribers.discard(q)

def publish(self, event: dict) -> None:
    """Called from consumer THREADS. Marshals delivery onto the loop."""
    loop = self._loop
    if loop is None:                          # events before /stream ever connects → no-op
        return
    with self._subs_lock:
        subs = list(self._subscribers)        # snapshot; lock never held across the handoff
    for q in subs:
        try:
            loop.call_soon_threadsafe(self._deliver, q, event)
        except RuntimeError:
            pass                              # loop closed during shutdown

def _deliver(self, q: asyncio.Queue, event: dict) -> None:
    # runs ON the loop thread
    try:
        q.put_nowait(event)
    except asyncio.QueueFull:                 # slow client: lossy-but-live (projection is rebuildable)
        try:
            q.get_nowait()
            q.put_nowait(event)
        except (asyncio.QueueEmpty, asyncio.QueueFull):
            pass
```

Fire a single generic nudge from each mutation point (append at the end of each `apply_*`):
`self.publish({"type": "changed"})`. The browser re-fetches the three snapshots on any event, which
is the simplest possible client contract and needs no per-event serialization.

**Backpressure = drop, not block.** The projection docstring already states it is rebuildable from the
stream; a client that falls behind reconnects and re-`GET`s `/situations`. So a bounded queue that
drops the oldest event on overflow is strictly better than unbounded buffering or a stalled consumer
thread. The drop path runs on the loop thread (via `call_soon_threadsafe`), so the consumer thread
never blocks or sees `QueueFull`.

`bind_loop` is called from the async lifespan (which runs on the loop) right after `app.state.model = model`.
The `if self._loop is None: return` guard keeps the existing **synchronous** `ReadModel` unit tests
(which call `apply_*` with no loop bound) passing unchanged.

**The `/stream` generator** (`services/read/app.py`):

```python
@app.get("/stream")
async def stream(request: Request):
    if not _stream_authorized(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    model = getattr(app.state, "model", None)
    if model is None:
        return JSONResponse({"detail": "not ready"}, status_code=503)

    async def gen():
        q = model.subscribe()                 # created on the loop thread — correct
        try:
            yield ": connected\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"    # heartbeat defeats proxy idle timeout
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            model.unsubscribe(q)              # idempotent; fires on client disconnect (generator close)
    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
```

`X-Accel-Buffering: no` + `Cache-Control: no-cache` stop intermediary proxies from buffering the
stream (which would swallow the heartbeat). Disconnect cleanup relies on Starlette closing the
generator (the `finally` runs); `unsubscribe` is idempotent.

---

## Decision 4 — Frontend subscription layer (SSE with poll fallback)

New `frontend/src/data/stream.ts` + a `useLiveData` hook. In **live mode**, open one `EventSource`
via `openStream()`; on each event (or the initial `: connected`), re-run the existing
`loadSituations/loadOutcomes/loadMetrics` from `data/source.ts`. On `EventSource` `error` that fails
to auto-recover, fall back to the existing 5s poll. In **mock mode**, behavior is unchanged (the
existing `useData` poll / static mock). The effect **must** close the `EventSource` in cleanup
(StrictMode double-mount → otherwise two live connections in dev).

The three existing views change only by swapping `useData` → `useLiveData`, which is transparent
(identical data shapes). No view logic changes.

---

## Decision 5 — The live Pipeline view (incident-journey lanes)

A new fourth view. Five stage lanes: **Detected → Diagnosed → Gate → Acting → Resolved**. Each live
incident is a card; when its status changes, the card **glides** from its old lane to the new one.

### CRITICAL correctness note (caught by adversarial verify)

The naive approach — five separate per-lane `.map()` lists, each keyed by `s.id`, relying on the
`layout` prop to FLIP a card across lanes — **does not work**. React keys are scoped to siblings within
one parent; when a card changes lane it unmounts from lane A's list and mounts a *fresh* instance in
lane B's list, so `layout` has no prior snapshot to FLIP from → the card **pops**, it does not glide.

**Chosen: Option A — one flat, stably-keyed list; lane placement via CSS grid-column.**

Render a SINGLE `list.map(s => <motion.div key={s.id} layout="position" ...>)`. The element *instance
persists across renders* (same key, same single parent), so plain `layout` measures the card's box
before and after and FLIP-animates it across columns. Place each card in its lane by deriving the CSS
grid column from `laneOf(s)`. This is the only shape where plain `layout` produces a real glide.

*(Option B — keep five per-lane containers but use `layoutId={s.id}` on each card, the same primitive
as Shell.tsx's `tabpill` — also works but does a measured crossfade between two instances; Option A is
cleaner with no ghosting and is the spec's choice.)*

### `laneOf` mapping (corrections applied)

```ts
const LANES = ["detected", "diagnosed", "gate", "acting", "resolved"] as const;
type Lane = typeof LANES[number];

// Filter suppressed BEFORE mapping — do NOT rely on a fallthrough (it would dump
// suppressed cards into Detected). Suppressed situations are shown in a separate
// muted strip, not the lanes.
function laneOf(s: Situation): Lane {
  if (s.status === "resolved") return "resolved";
  if (s.status === "failed") return "gate";       // failed awaits operator attention at the gate
  if (s.status === "acting") return s.hitl_mode === "hitl" ? "gate" : "acting";
  if (s.status === "diagnosed") return "diagnosed";
  return "detected";
}
```

Note: this deliberately maps `failed` → **gate** (operator attention), which differs from
`Incidents.tsx:84` (`failed` → acting). That is intentional for the pipeline view's "where does a
human look" framing; the spec states it explicitly rather than claiming to mirror Incidents.

### Data flow reality (correction applied)

**There is no SSE in the current data flow, and the pipeline view must not assume per-transition
events.** State arrives as whole-list snapshots (5s poll today; SSE-nudged re-fetch after this work).
A card can therefore **cross two lanes in a single snapshot** (e.g. detected → diagnosed between
ticks). The animation must tolerate multi-lane jumps: `layout="position"` FLIP interpolates directly
from the old box to the new box regardless of how many columns it skipped, so this is handled — but
the design must not rely on seeing every intermediate stage.

### StrictMode-safe enter/exit

`AnimatePresence initial={false}` so first-render cards appear at full opacity (no mount animation to
strand under StrictMode's double-invoke — the codebase's existing rule). Only genuinely-new cards
(appended after mount) get an enter animation; the resting/`animate` state is fully visible. `layout`
FLIP is transform-based and has no opacity-stranding failure mode.

### Age-out ordering

A resolved card that then ages out must not exit *simultaneously* with its move-to-Resolved (a
layout-move + AnimatePresence-exit on the same commit can snap). Remove resolved cards from the list
on a delayed timer, **after** they have settled in the Resolved lane.

### Gate lane carries the approve action

The card in the Gate lane surfaces the same approve/reject as `Incidents.tsx`, reusing
`decideApproval` from `source.ts` (token-aware). In live mode the SSE-nudged re-fetch drives the card
forward after approval — no optimistic hack needed. `springSoft` (primitives.tsx:9) is the layout
transition so the glide matches the app feel. Honor `prefers-reduced-motion` (gate the layout
transition to instant), matching `view.css`.

### Mock mode

Mock mode animates a small scripted set of incidents on a timer so the view demos with no backend
(`VITE_DATA_MODE=mock`), satisfying the "works in mock and live" criterion.

---

## Decision 6 — Real-data Overview (kill mock-in-live)

`Overview.tsx:3` today imports `series`, `services`, and `metrics as mockMetrics` from `mock.ts` and
uses them **even in live mode** — a credibility bug for an auth-on real-data demo. Fixes:

- **Fleet health** (the `services` list): read-service gains a small `GET /fleet` endpoint that
  reports each service's reachability (pings each `/health`), or the frontend derives it. No hardcoded
  `services` list in live mode. Note `/health` is **always auth-exempt** on every service (the
  `base.py` short-circuit), so `/fleet`'s internal pings need **no token** even under `AUTH_MODE=token`
  — simplest correct choice. (`/fleet` itself is gated by the standard header gate like the other read
  endpoints.) The frontend-derives-it alternative is acceptable too and avoids a new endpoint entirely;
  the plan picks one.
- **Sparklines** (`series(...)`): maintain a client-side rolling buffer of the last N real `/metrics`
  samples (fed by the SSE-nudged re-fetches) and use *that* as the sparkline series — real history,
  not synthetic noise. The hardcoded "8,420 raw alerts" → `metrics.alertsIngested`.
- **Mock mode keeps its canned values** unchanged.

---

## Decision 7 — Audit-trail explorer

A view (or a rich panel on Governance) over the existing `/audit` data: filter by actor / decision
(allow·deny·pending) / `correlation_id`, rendering the allow-deny-pending trail. Uses the persisted
Postgres audit records (the persistence work). Read-only, null-safe, off the write path.

---

## Decision 8 — Apple light-theme repaint (`#F5F5F7`/white · `#1D1D1F` ink · `#0071E3` blue)

Everything routes through 4 Tailwind token groups, so the repaint is centralized in
`tailwind.config.js` — **but** ~55 dark-assuming utilities use literal `white/black` colors, inset-white
highlights, dark-tuned glows, and near-black button-text-on-accent that a token swap alone will NOT
fix. Do it in two passes.

### Pass 1 — tokens + shadows + base layer

New `tailwind.config.js` color + shadow block (Apple palette). Also fix the pre-existing
`darkmode:` → `darkMode:` typo (line 4) while editing (currently silently ignored; no `dark:` variants
exist, so it's cosmetic — and removing `class="dark"` from `index.html:2` is likewise a no-op, not a
functional switch).

```js
colors: {
  ground: { DEFAULT: "#FFFFFF", raised: "#FFFFFF", sunken: "#F5F5F7" },   // sunken role INVERTS: now the light wash
  ink:    { DEFAULT: "#1D1D1F", 2: "#6E6E73", 3: "#86868B", 4: "#C7C7CC" },
  signal: { DEFAULT: "#0071E3", dim: "#0058B0", glow: "rgba(0,113,227,0.14)" },  // dim = AA-safe blue for tiny labels
  sev:    { ok: "#34C759", warn: "#FF9500", crit: "#FF3B30", info: "#5E5CE6" },  // Apple system colors
},
boxShadow: {
  lift:  "0 1px 2px rgba(0,0,0,0.04), 0 12px 32px -12px rgba(0,0,0,0.12)",       // soft cool-gray, never harsh
  glow:  "0 0 0 1px rgba(0,113,227,0.35), 0 8px 24px -8px rgba(0,113,227,0.20)", // selection ring, not a halo
  inset: "inset 0 1px 0 rgba(255,255,255,0.6), inset 0 0 0 1px rgba(0,0,0,0.04)",
},
```

`index.css` base-layer edits (outside the token system): `color-scheme: dark → light`;
body `background #080b10 → #FFFFFF`, `color #eaf0f7 → #1D1D1F`; `::selection` teal → `rgba(0,113,227,0.20)`/`#1D1D1F`;
scrollbar white-alpha → black-alpha; `:focus-visible` outline `#3dd6d0 → #0071E3`; **delete `.grain`**
(Apple has no film grain — also strip the `grain` class from `Shell.tsx:27`); **reduce `.mesh`** to a
faint near-white wash `radial-gradient(900px 560px at 82% -6%, rgba(0,113,227,0.04), transparent 60%)`
(or delete and strip `mesh` from `Shell.tsx:29`); `.bezel`/`.bezel-core` inset-white highlights → light-safe.

### Pass 2 — the ~55-utility changelist (mechanical, exhaustive)

The rule: **every `white/[0.0x]` hairline/fill → `black/[0.0x]` at roughly 2× the alpha** (black-on-white
needs more alpha than white-on-dark to read). A missed `white/[0.06]` hairline is invisible on a white
card and the whole double-bezel system collapses. Full file:line changelist is carried into the
implementation plan; the load-bearing items:

- **Two `text-ground-sunken`-as-button-text traps → `text-white`** (`primitives.tsx:72` CTA primary,
  `Incidents.tsx:251` approve button). Dark text on Apple blue fails contrast.
- **`bg-black/10` icon disc on the accent-fill button → `bg-white/20`** (`primitives.tsx:80`).
- **Two white-on-white MUST-fixes** (not "verify"): `primitives.tsx:33` Bezel core `bg-ground-raised`
  (solid white on white) and `Incidents.tsx:116` `bg-ground-raised/60` — give the core a real light
  surface (`bg-ground-sunken` #F5F5F7) so cards separate from the page; the 4%-black inset alone is too
  weak as the sole delimiter for every card.
- **Tiny `text-signal` labels → `text-signal-dim` (#0058B0)** where the text is ≤11px (Eyebrow
  `primitives.tsx:46`, mono captions) — #0071E3 on white is exactly 4.5:1, borderline at 11px; #0058B0
  is ~6.9:1.
- **5 hardcoded sparkline hexes** → Apple palette: `primitives.tsx:137` default `#3DD6D0→#0071E3`;
  `Overview.tsx:85 #3DD6D0→#0071E3`, `:94 #43D18A→#34C759`, `:97 #6E8BFF→#5E5CE6`.
- **Sparkline grid** `stroke="white" strokeOpacity=0.05 → stroke="black" 0.08` (`primitives.tsx:166`);
  consider lowering the area-fill top stop (0.28→~0.18) so it isn't heavy on white.
- **Literal teal shadows** → blue: `Shell.tsx:37 shadow-[0_0_10px_#3DD6D0]`, `primitives.tsx:72` CTA
  box-shadows.
- **Glass surfaces** `bg-ground/70` / `bg-ground/85` → `bg-white/70` / `bg-white/85` (`Shell.tsx:33,103`).
- **Hero glow blob** `bg-signal/[0.10] blur-3xl → bg-signal/[0.06]` or remove (`Overview.tsx:72`).
- ~40 further `white/[0.0x] → black/[0.0x]` hairline/fill inversions across `primitives.tsx`,
  `Shell.tsx`, `Overview.tsx`, `Incidents.tsx`, `Governance.tsx`, `useToast.tsx` (full list in the plan).

### Verification for the repaint

`npm run build` clean is necessary but **not sufficient** — a light-on-light hairline builds fine and
looks broken. The plan's repaint tasks end with a **browser render check** of every view (Overview,
Incidents, Governance, the new Pipeline, the audit explorer) in the Browser pane, confirming card
separation, contrast, and the new pipeline animation — not just a green build.

---

## Acceptance criteria (from WORKPLAN Stream C, + this effort)

1. **Live <1s:** a new situation / status change appears in the console within ~1s of the event via
   SSE, with graceful fallback to polling if the stream drops.
2. **Animating pipeline view:** an incident visibly animates through the stage lanes during a
   `chaos.sh` / break run — the UI demo centerpiece.
3. **`npm run build` clean** (strict TS); works in **`VITE_DATA_MODE=mock`** (no backend) **and** **`live`**.
4. **No regression** to the existing three views or empty-state safety; the repaint is verified in the
   browser, not just build-clean.
5. **No mock in live:** Overview shows real fleet health + real metric-history sparklines under live mode.
6. **Audit explorer** over the persisted audit trail, filterable, null-safe, off the write path.
7. **New read endpoints** (`/stream`, `/fleet`) are null-safe, don't touch the write path, and under
   `AUTH_MODE=token` are gated (`/stream` via query-token; `/fleet` via the standard header gate).
8. **`AUTH_MODE=off` + mock mode byte-unchanged;** `uv run pytest` / `ruff` green.

## Suggested task ordering (for the plan)

1. Backend transport: `ReadModel` pub/sub + `/stream` + query-auth + tests (sync tests stay green).
2. Frontend subscription: `openStream` + `stream.ts` + `useLiveData` + poll fallback; swap the 3 views.
3. Apple repaint Pass 1 (tokens/shadows/base) + Pass 2 (changelist) + browser render check.
4. Pipeline view (Option A flat list + `laneOf` + gate-approve + mock timer).
5. Real-data Overview (`/fleet` + sparkline buffer) — kill mock-in-live.
6. Audit explorer.
7. `docs/UI.md` + `flow.md`/README touch-ups + ADR (SSE read-path / real-time console).

Transport + repaint land first (everything depends on them); the pipeline view depends on the
subscription layer; Overview/audit/docs follow.
