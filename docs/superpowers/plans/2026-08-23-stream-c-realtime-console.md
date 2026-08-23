# Stream C — Real-time Console + Live Pipeline + Apple Repaint — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Take the IntelliOps console from a 5s poll to a real-time, demo-grade product: SSE live updates, an animating incident-journey pipeline view, real-data dashboards, an audit explorer, and a full repaint to Apple's light website palette.

**Architecture:** A new `GET /stream` SSE endpoint on read-service, fed by a stdlib thread→async pub/sub inside `ReadModel`; a frontend `EventSource` subscription with poll fallback; a new Pipeline view (one flat stably-keyed list, `layout` FLIP across CSS-grid lanes); real-data Overview; an audit explorer; and a two-pass Apple light repaint. Everything opt-in behind the existing `VITE_DATA_MODE` / `AUTH_MODE` switches.

**Tech Stack:** FastAPI 0.133 / Starlette 1.0 (backend), React + TypeScript (strict) + Vite, framer-motion ^11, Tailwind v3.4, stdlib `asyncio`/`threading` (no new deps).

**Spec:** [docs/superpowers/specs/2026-08-23-stream-c-realtime-console-design.md](../specs/2026-08-23-stream-c-realtime-console-design.md)

## Global Constraints

- **Test-safe by default.** `VITE_DATA_MODE=mock` (dev/CI default) and `AUTH_MODE=off` (default) behavior must be byte-unchanged. New live behavior is opt-in.
- **No new dependency.** SSE fan-out is stdlib `asyncio`+`threading`. framer-motion (11.18.2) + Tailwind (3.4.15) already present.
- **Backend gate:** `uv run pytest` (read-service tests: `test_metrics.py`, `test_projection.py`, `test_pruning.py`, `test_read_api.py` must stay green — they call `ReadModel()` with no args and call `apply_*` with no loop bound), `uv run ruff check`, `uv run ruff format --check .` all clean.
- **Frontend gate:** `npm run build` clean (strict TS).
- **StrictMode-safe:** resting state is always the *visible* state (never `opacity:0` at rest); any effect opening an `EventSource` closes it in cleanup.
- **Additive only:** `data/types.ts` gets optional fields only; no `common/contracts.py` change; read-service passes a custom `auth_exempt` (additive) to `create_app`.
- **Commit trailer:** every commit ends with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## Task 1: Backend SSE transport — `ReadModel` pub/sub + `/stream` + query-auth

**Files:**
- Modify: `services/read/projection.py` (add pub/sub to `ReadModel`; fire nudge in each `apply_*`)
- Modify: `services/read/app.py` (custom `auth_exempt`, `bind_loop` in lifespan, `/stream` route, `_stream_authorized`)
- Test: `services/read/tests/test_stream.py` (new)

**Interfaces:**
- Produces: `GET /stream` SSE endpoint emitting `data: {"type":"changed"}\n\n` frames + `: keepalive` heartbeats; `ReadModel.subscribe()/unsubscribe()/publish()/bind_loop()`.
- Consumes: existing `ReadModel.apply_detected/apply_diagnosed/apply_outcome/apply_suppressed`, `create_app(auth_exempt=...)` from `services/base.py:30`, `common/auth.py` token model.

- [ ] **Step 1: Write failing tests for the pub/sub (sync, no loop needed)**

`services/read/tests/test_stream.py`:

> **NOTE (verified before dispatch):** the repo has NO `pytest-asyncio` / `anyio` async-test
> marker. Do NOT write `async def test_...` / `@pytest.mark.asyncio` — they won't run. Test the
> async pub/sub from **sync** test functions by driving a real loop with `asyncio.run(...)`. This
> avoids adding a dev dependency and matches the repo's marker-free test style.

```python
import asyncio
from services.read.projection import ReadModel


def test_publish_is_noop_when_no_loop_bound():
    # The existing sync tests call apply_* with no loop; publish must not raise.
    m = ReadModel()
    m.publish({"type": "changed"})  # no loop bound → silent no-op


def test_subscribe_receives_published_event():
    async def scenario():
        m = ReadModel()
        m.bind_loop(asyncio.get_running_loop())
        q = m.subscribe()
        m.publish({"type": "changed"})       # marshals via call_soon_threadsafe
        await asyncio.sleep(0)                # let the loop run the scheduled callback
        event = await asyncio.wait_for(q.get(), timeout=1.0)
        m.unsubscribe(q)
        return event, (q in m._subscribers)
    event, still_subscribed = asyncio.run(scenario())
    assert event == {"type": "changed"}
    assert still_subscribed is False


def test_full_queue_drops_oldest():
    async def scenario():
        m = ReadModel()
        m.bind_loop(asyncio.get_running_loop())
        q = m.subscribe(maxsize=1)
        m.publish({"n": 1})
        m.publish({"n": 2})
        await asyncio.sleep(0)
        return await asyncio.wait_for(q.get(), timeout=1.0)
    assert asyncio.run(scenario()) == {"n": 2}  # oldest dropped, newest kept
```

> To exercise `publish` from a real *thread* (closer to the consumer-thread reality), a stronger
> variant spawns `threading.Thread(target=lambda: m.publish(...))` inside `scenario()` and awaits
> the queue — optional but recommended; the loop must already be bound and running.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest services/read/tests/test_stream.py -v`
Expected: FAIL (`ReadModel` has no `bind_loop`/`subscribe`/`publish`). The tests are plain **sync** functions using `asyncio.run(...)` — no `pytest-asyncio` marker needed (the repo has none; do not add one).

- [ ] **Step 3: Add the pub/sub to `ReadModel`**

In `services/read/projection.py`, add `import asyncio` and `import threading` at the top. In `ReadModel.__init__` (keep the existing `max_outcomes`/`ttl_seconds`/`max_situations` signature **unchanged**), add:

```python
        self._subscribers: set[asyncio.Queue] = set()
        self._subs_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
```

Add these methods to `ReadModel`:

```python
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
        if loop is None:
            return
        with self._subs_lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                loop.call_soon_threadsafe(self._deliver, q, event)
            except RuntimeError:
                pass  # loop closed during shutdown

    def _deliver(self, q: asyncio.Queue, event: dict) -> None:
        # runs ON the loop thread
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
                q.put_nowait(event)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass
```

Fire a nudge at the **end** of each of `apply_detected`, `apply_diagnosed`, `apply_outcome`, `apply_suppressed`:

```python
        self.publish({"type": "changed"})
```

- [ ] **Step 4: Run the pub/sub tests to verify they pass**

Run: `uv run pytest services/read/tests/test_stream.py -v`
Expected: PASS (3 tests). Then run the existing read tests to confirm no regression:
Run: `uv run pytest services/read/tests/ -v`
Expected: all PASS (the `if self._loop is None: return` guard keeps the sync tests green).

- [ ] **Step 5: Wire `/stream` + query-auth into `app.py`**

In `services/read/app.py`: add imports `import asyncio`, `import hmac`, `import json`, and from fastapi `Request`, `HTTPException`; from `fastapi.responses` add `StreamingResponse` (keep existing `JSONResponse` if present, else add). Replace the bare `app = create_app("read-service")` with the custom exempt + add the lifespan `bind_loop`:

```python
def _auth_exempt(method: str, path: str) -> bool:
    # /stream is reached by the browser EventSource API, which cannot set the
    # Authorization header; it authenticates via ?token= inside the route.
    return method == "GET" and path == "/stream"


app = create_app("read-service", auth_exempt=_auth_exempt)
app.router.lifespan_context = lifespan
```

In the `lifespan` function, right after `app.state.model = model` and **before** `run_consumer`, add:

```python
    model.bind_loop(asyncio.get_running_loop())
```

Add the auth helper + route:

```python
def _stream_authorized(request: Request) -> bool:
    settings = get_settings()
    if settings.auth_mode != "token":
        return True
    token = request.query_params.get("token", "")
    return bool(settings.auth_token) and hmac.compare_digest(token, settings.auth_token)


@app.get("/stream")
async def stream(request: Request):
    if not _stream_authorized(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    model = getattr(app.state, "model", None)
    if model is None:
        return JSONResponse({"detail": "not ready"}, status_code=503)

    async def gen():
        q = model.subscribe()
        try:
            yield ": connected\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            model.unsubscribe(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 6: Write an API test for `/stream` auth**

Append to `services/read/tests/test_stream.py` — verify `_stream_authorized` directly (no live stream needed):

```python
def test_stream_authorized_off_mode(monkeypatch):
    from services.read import app as read_app
    from common.config import Settings
    monkeypatch.setattr(read_app, "get_settings", lambda: Settings(auth_mode="off"))

    class Req:  # minimal stub
        query_params: dict = {}
    assert read_app._stream_authorized(Req()) is True


def test_stream_authorized_token_mode_requires_match(monkeypatch):
    from services.read import app as read_app
    from common.config import Settings
    monkeypatch.setattr(
        read_app, "get_settings",
        lambda: Settings(auth_mode="token", auth_token="secret"),
    )

    class Req:
        def __init__(self, tok): self.query_params = {"token": tok}
    assert read_app._stream_authorized(Req("secret")) is True
    assert read_app._stream_authorized(Req("wrong")) is False
    assert read_app._stream_authorized(Req("")) is False
```

Adjust the `Settings(...)` construction to match `common/config.py`'s actual field names/prefix (verify with a quick read — settings may need env-var construction instead of kwargs). If `Settings` can't take kwargs directly, monkeypatch `get_settings` to return a `SimpleNamespace(auth_mode=..., auth_token=...)`.

- [ ] **Step 7: Run the full read-service suite + lint**

Run: `uv run pytest services/read/tests/ -v && uv run ruff check services/read/ && uv run ruff format --check services/read/`
Expected: all PASS, lint clean.

- [ ] **Step 8: Commit**

```bash
git add services/read/projection.py services/read/app.py services/read/tests/test_stream.py
git commit -m "feat(read): SSE /stream endpoint + ReadModel thread->async pub/sub

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Frontend subscription layer — `openStream` + `useLiveData` + poll fallback

**Files:**
- Modify: `frontend/src/data/api.ts` (add `openStream()`)
- Create: `frontend/src/data/stream.ts` (EventSource wrapper)
- Create: `frontend/src/hooks/useLiveData.ts` (SSE-driven, poll fallback)
- Modify: `frontend/src/views/Overview.tsx`, `Incidents.tsx`, `Governance.tsx` (swap `useData` → `useLiveData`)

**Interfaces:**
- Consumes: `READ` base + `AUTH_TOKEN` (`api.ts:3,6`), `loadSituations/loadOutcomes/loadMetrics` (`data/source.ts`).
- Produces: `openStream(): EventSource`, `useLiveData<T>(loader, initial)` — same return shape as `useData` (`{data, loading, error}`).

- [ ] **Step 1: Add `openStream` to `api.ts`**

Append to `frontend/src/data/api.ts` (reuses existing `READ` + `AUTH_TOKEN`):

```ts
export function openStream(): EventSource {
  const url = new URL(`${READ}/stream`);
  if (AUTH_TOKEN) url.searchParams.set("token", AUTH_TOKEN);
  return new EventSource(url.toString()); // no withCredentials — conflicts with wildcard CORS
}
```

- [ ] **Step 2: Create the subscription hook**

`frontend/src/hooks/useLiveData.ts` — SSE nudge re-runs the loader; falls back to 5s poll on error. Mirrors `useData`'s signature so views swap transparently:

```ts
import { useEffect, useState } from "react";
import { openStream } from "../data/api";

const LIVE = import.meta.env.VITE_DATA_MODE === "live";

export function useLiveData<T>(loader: () => Promise<T>, initial: T) {
  const [data, setData] = useState<T>(initial);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = () =>
      loader()
        .then((d) => alive && (setData(d), setError(null)))
        .catch((e) => alive && setError(String(e)))
        .finally(() => alive && setLoading(false));

    tick(); // initial load in every mode

    if (!LIVE) return () => { alive = false; }; // mock mode: one load, no stream/poll

    let pollId: number | undefined;
    const startPoll = () => {
      if (pollId === undefined) pollId = window.setInterval(tick, 5000);
    };

    let es: EventSource | null = null;
    try {
      es = openStream();
      es.onmessage = () => tick();      // {"type":"changed"} nudge → refetch
      es.onerror = () => startPoll();   // EventSource auto-reconnects; poll covers hard failures
    } catch {
      startPoll();
    }

    return () => {
      alive = false;
      es?.close();                       // StrictMode double-mount safety
      if (pollId !== undefined) window.clearInterval(pollId);
    };
  }, [loader]);

  return { data, loading, error };
}
```

- [ ] **Step 3: Swap the three existing views to `useLiveData`**

In `Overview.tsx`, `Incidents.tsx`, `Governance.tsx`: change the import `import { useData } from "../hooks/useData";` → `import { useLiveData } from "../hooks/useLiveData";` and replace each `useData(` call with `useLiveData(`. No other logic changes (identical return shape).

- [ ] **Step 4: Verify the build is clean**

Run: `cd frontend && npm run build`
Expected: clean (strict TS). Note: `stream.ts` is created in Task 4 if a richer store is needed; for now `openStream` in `api.ts` + the hook is sufficient — do NOT create an empty `stream.ts` (YAGNI). Update the Files list mentally: `stream.ts` is deferred.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/data/api.ts frontend/src/hooks/useLiveData.ts frontend/src/views/Overview.tsx frontend/src/views/Incidents.tsx frontend/src/views/Governance.tsx
git commit -m "feat(frontend): SSE subscription hook with poll fallback; swap views to useLiveData

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Apple light-theme repaint — Pass 1 (tokens/shadows/base) + Pass 2 (changelist)

**Files:**
- Modify: `frontend/tailwind.config.js` (4 token groups + boxShadow + `darkmode`→`darkMode` typo)
- Modify: `frontend/src/index.css` (base layer: color-scheme, body, selection, scrollbar, focus, grain/mesh/bezel)
- Modify: `frontend/index.html` (remove `class="dark"` — cosmetic)
- Modify: `frontend/src/components/primitives.tsx`, `components/Shell.tsx`, `views/Overview.tsx`, `views/Incidents.tsx`, `views/Governance.tsx`, `hooks/useToast.tsx` (the ~55-utility changelist)

**Interfaces:** Consumes nothing new; produces the light theme every later view inherits.

- [ ] **Step 1: Swap the token + shadow block in `tailwind.config.js`**

Fix `darkmode:` → `darkMode:` (line ~4). Replace the `colors` block and `boxShadow` block with:

```js
      colors: {
        ground: { DEFAULT: "#FFFFFF", raised: "#FFFFFF", sunken: "#F5F5F7" },
        ink: { DEFAULT: "#1D1D1F", 2: "#6E6E73", 3: "#86868B", 4: "#C7C7CC" },
        signal: { DEFAULT: "#0071E3", dim: "#0058B0", glow: "rgba(0,113,227,0.14)" },
        sev: { ok: "#34C759", warn: "#FF9500", crit: "#FF3B30", info: "#5E5CE6" },
      },
```

```js
      boxShadow: {
        lift: "0 1px 2px rgba(0,0,0,0.04), 0 12px 32px -12px rgba(0,0,0,0.12)",
        glow: "0 0 0 1px rgba(0,113,227,0.35), 0 8px 24px -8px rgba(0,113,227,0.20)",
        inset: "inset 0 1px 0 rgba(255,255,255,0.6), inset 0 0 0 1px rgba(0,0,0,0.04)",
      },
```

Keep `fontFamily`, `fontSize`, `letterSpacing`, `borderRadius`, `transitionTimingFunction`, `keyframes`, `animation` unchanged (colorless).

- [ ] **Step 2: Rewrite the `index.css` base layer**

Edits (line anchors approximate — match by content):
- `color-scheme: dark` → `color-scheme: light`
- body `background: #080b10` → `#FFFFFF`; `color: #eaf0f7` → `#1D1D1F`
- `::selection` `background: rgba(61,214,208,0.28)` → `rgba(0,113,227,0.20)`; color `#eaf0f7` → `#1D1D1F`
- scrollbar-color `rgba(255,255,255,0.14)` → `rgba(0,0,0,0.20)`; thumb `rgba(255,255,255,0.12)` → `rgba(0,0,0,0.18)`; hover `0.2` → `rgba(0,0,0,0.28)`
- `:focus-visible` outline `#3dd6d0` → `#0071E3`
- Delete the `.grain::after` rule entirely
- `.mesh` gradients → a single faint wash: `radial-gradient(900px 560px at 82% -6%, rgba(0,113,227,0.04), transparent 60%)`
- `.bezel` background `rgba(255,255,255,0.04)` → `rgba(0,0,0,0.02)`; border `rgba(255,255,255,0.08)` → `rgba(0,0,0,0.08)`
- `.bezel-core` box-shadow `inset 0 1px 1px rgba(255,255,255,0.07)` → `inset 0 1px 0 rgba(255,255,255,0.6), inset 0 0 0 1px rgba(0,0,0,0.05)`

- [ ] **Step 3: Remove `class="dark"` from `index.html`**

Line 2: `<html lang="en" class="dark">` → `<html lang="en">`. Also strip `grain` and `mesh` from `Shell.tsx:27,29` className strings (grain deleted; mesh recolored via CSS so the class stays — keep `mesh`, remove only `grain`).

- [ ] **Step 4: Apply the load-bearing utility changelist**

The rule: every `white/[0.0x]` → `black/[0.0x]` at ~2× alpha. Apply exactly (file:line from the verified findings):

**`primitives.tsx`:** L28 `border-white/[0.07]`→`border-black/[0.08]`, `bg-white/[0.035]`→`bg-black/[0.02]`; **L33 `bg-ground-raised`→`bg-ground-sunken`** (white-on-white MUST-fix), `border-white/[0.05]`→`border-black/[0.06]`; L46 `border-white/10`→`border-black/[0.08]`, `bg-white/[0.03]`→`bg-signal/[0.06]`, **`text-signal`→`text-signal-dim`** (AA at 11px); **L72 `text-ground-sunken`→`text-white`** (button-contrast MUST-fix), teal box-shadows→`shadow-[0_8px_24px_-6px_rgba(0,113,227,0.35)]` / hover `shadow-[0_12px_32px_-6px_rgba(0,113,227,0.45)]`; L73 `bg-white/[0.05]`→`bg-black/[0.04]`, `border-white/10`→`border-black/[0.10]`, hover `bg-white/[0.08]`→`bg-black/[0.06]`; **L80 `bg-black/10`→`bg-white/20`** (primary icon disc), `bg-white/10`→`bg-black/[0.06]` (ghost); L97 `bg-white/[0.05]`→`bg-black/[0.05]`, `border-white/10`→`border-black/[0.10]`; L117 `bg-white/[0.05]`→`bg-black/[0.05]`; L137 default `"#3DD6D0"`→`"#0071E3"`; L166 `stroke="white" strokeOpacity="0.05"`→`stroke="black" strokeOpacity="0.08"`.

**`Shell.tsx`:** L33 `border-white/[0.08]`→`border-black/[0.08]`, `bg-ground/70`→`bg-white/70`; L37 `shadow-[0_0_10px_#3DD6D0]`→`shadow-[0_0_8px_rgba(0,113,227,0.5)]`; L54 `bg-white/[0.08]`→`bg-black/[0.06]`, `ring-white/[0.06]`→`ring-black/[0.06]`; L67 `border-white/[0.07]`→`border-black/[0.08]`, `bg-white/[0.03]`→`bg-black/[0.03]`; L75 `bg-white/[0.05]`→`bg-black/[0.05]`; L103 `bg-ground/85`→`bg-white/85`. (L80/85/90 `bg-ink` stays — dark ink on white is now correct.)

**`Overview.tsx`:** L72 `bg-signal/[0.10]`→`bg-signal/[0.06]`; L85 `"#3DD6D0"`→`"#0071E3"`, L94 `"#43D18A"`→`"#34C759"`, L97 `"#6E8BFF"`→`"#5E5CE6"`; L130 `hover:bg-white/[0.03]`→`hover:bg-black/[0.03]`; L153 `border-white/[0.06]`→`border-black/[0.06]`, `bg-white/[0.02]`→`bg-black/[0.02]`; L159 `bg-white/[0.05]`→`bg-black/[0.05]`; L162 `bg-white/[0.06]`→`bg-black/[0.08]`.

**`Incidents.tsx`:** L113 inactive `border-white/[0.06]`→`border-black/[0.06]`, `bg-white/[0.02]`→`bg-black/[0.02]`, `hover:bg-white/[0.04]`→`hover:bg-black/[0.04]`; **L116 `bg-ground-raised/60`→`bg-ground-sunken`** (white-on-white MUST-fix); L162 `border-white/[0.07]`→`border-black/[0.08]`, `bg-white/[0.03]`→`bg-black/[0.03]`; L168 `border-white/[0.05]`→`border-black/[0.06]`, `bg-white/[0.015]`→`bg-black/[0.02]`; L177 `bg-white/[0.04]`→`bg-black/[0.05]`; L200 `border-white/[0.05]`→`border-black/[0.06]`, `bg-white/[0.015]`→`bg-black/[0.02]`; L206 `bg-white/[0.06]`→`bg-black/[0.08]`; L209 `bg-white/[0.05]`→`bg-black/[0.05]`; L217 `border-white/[0.06]`→`border-black/[0.06]`, `bg-ground-sunken/60`→`bg-black/[0.03]`; L247 `bg-white/[0.05]`→`bg-black/[0.05]`; **L251 `text-ground-sunken`→`text-white`** (approve button MUST-fix); L255 `border-white/10`→`border-black/[0.10]`, `bg-white/[0.04]`→`bg-black/[0.04]`, `hover:bg-white/[0.07]`→`hover:bg-black/[0.06]`; L270 `border-white/[0.06]`→`border-black/[0.06]`.

**`Governance.tsx`:** L58 `border-white/[0.06]`→`border-black/[0.06]`, `bg-white/[0.02]`→`bg-black/[0.02]`; L61 `bg-white/[0.04]`→`bg-black/[0.05]`; L84 `hover:bg-white/[0.025]`→`hover:bg-black/[0.03]`; L98 `border-white/[0.06]`→`border-black/[0.06]`; L119 `bg-white/[0.02]`→`bg-black/[0.03]`; L136 `bg-white/[0.02]`→`bg-black/[0.03]`; L139 `bg-white/[0.05]`→`bg-black/[0.05]`.

**`useToast.tsx`:** L28 — `shadow-lift` re-tuned via config (OK); if the `sev-crit/10`/`sev-ok/10` fills read too faint on white, bump to `/12`.

*(Any `white/[0.0x]` not listed above but discovered during the browser check in Step 6 gets the same `black/[0.0x]` at ~2× alpha treatment — the list is the verified set; grep confirmed no other hardcoded hexes.)*

- [ ] **Step 5: Verify the build is clean**

Run: `cd frontend && npm run build`
Expected: clean.

- [ ] **Step 6: Browser render check (build-clean is NOT sufficient)**

Start the frontend preview (`VITE_DATA_MODE=mock` so no backend needed), open each view (Overview, Incidents, Governance) in the Browser pane, and confirm: cards separate from the white page (Bezel cores visible), text contrast reads, no invisible white-on-white hairlines, accent is Apple blue not teal, no leftover grain/glow. Screenshot Overview as proof. Fix any missed hairline (same `black/[0.0x]` rule), rebuild, re-check.

- [ ] **Step 7: Commit**

```bash
git add frontend/tailwind.config.js frontend/src/index.css frontend/index.html frontend/src/components/primitives.tsx frontend/src/components/Shell.tsx frontend/src/views/Overview.tsx frontend/src/views/Incidents.tsx frontend/src/views/Governance.tsx frontend/src/hooks/useToast.tsx
git commit -m "feat(frontend): repaint to Apple light palette (tokens + ~55-utility re-tune)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: The live Pipeline view (incident-journey lanes)

**Files:**
- Create: `frontend/src/views/Pipeline.tsx`
- Modify: `frontend/src/components/Shell.tsx` (add the "Pipeline" tab + `View` type)
- Modify: `frontend/src/App.tsx` (route the new view)
- Modify: `frontend/src/data/mock.ts` (a scripted-timer incident set for mock mode)

**Interfaces:**
- Consumes: `Situation`/`SituationStatus` (`data/types.ts`), `useLiveData` + `loadSituations`, `decideApproval` (`data/source.ts`), `springSoft`/`SevChip`/`StatusChip`/`timeAgo` (`primitives.tsx`).
- Produces: `View` union gains `"pipeline"`.

- [ ] **Step 1: Add the `View` type + tab in `Shell.tsx`**

`export type View = "overview" | "incidents" | "pipeline" | "governance";` and add a tab entry (use a Phosphor light icon, e.g. `FlowArrow` or `Path`): `{ id: "pipeline", label: "Pipeline", icon: <FlowArrow size={17} weight="light" /> }`. Import the icon.

- [ ] **Step 2: Route it in `App.tsx`**

Add `{view === "pipeline" && <Pipeline />}` and `import { Pipeline } from "./views/Pipeline";`.

- [ ] **Step 3: Build `Pipeline.tsx` — ONE flat list, `layout` FLIP across grid columns**

CRITICAL: a single stably-keyed list placed by CSS grid-column. Do NOT render five per-lane `.map()` lists (that unmounts/remounts across parents → cards pop, not glide). Filter `suppressed` before mapping.

```tsx
import { LayoutGroup, AnimatePresence } from "framer-motion";
import { motion, springSoft, SevChip, StatusChip, timeAgo, CTA } from "../components/primitives";
import { useLiveData } from "../hooks/useLiveData";
import { loadSituations, decideApproval } from "../data/source";
import { Reveal as Section } from "../hooks/useReveal";
import type { Situation } from "../data/types";

const LANES = ["detected", "diagnosed", "gate", "acting", "resolved"] as const;
type Lane = (typeof LANES)[number];
const LANE_LABEL: Record<Lane, string> = {
  detected: "Detected", diagnosed: "Diagnosed", gate: "Gate · HITL", acting: "Acting", resolved: "Resolved",
};

function laneOf(s: Situation): Lane {
  if (s.status === "resolved") return "resolved";
  if (s.status === "failed") return "gate";
  if (s.status === "acting") return s.hitl_mode === "hitl" ? "gate" : "acting";
  if (s.status === "diagnosed") return "diagnosed";
  return "detected";
}

const prefersReduced =
  typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export function Pipeline() {
  const { data: situations } = useLiveData(loadSituations, [] as Situation[]);
  const live = situations.filter((s) => !s.suppressed);

  return (
    <div className="space-y-5">
      <Section>
        <h1 className="text-3xl font-semibold tracking-tightest sm:text-4xl">Live pipeline</h1>
        <p className="mt-2 max-w-[60ch] text-base text-ink-2">
          Every incident, moving through the closed loop in real time — detected, diagnosed, gated for a
          human, remediated, resolved.
        </p>
      </Section>

      {/* Lane headers */}
      <div className="grid grid-cols-5 gap-3">
        {LANES.map((lane) => (
          <div key={lane} className="rounded-2xl border border-black/[0.06] bg-black/[0.015] px-3 py-2 text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">
            {LANE_LABEL[lane]}
          </div>
        ))}
      </div>

      {/* ONE flat list; each card placed by gridColumnStart = its lane index. layout FLIPs it across columns. */}
      <LayoutGroup>
        <div className="grid grid-cols-5 items-start gap-3">
          <AnimatePresence initial={false}>
            {live.map((s) => {
              const lane = laneOf(s);
              const col = LANES.indexOf(lane) + 1;
              return (
                <motion.div
                  key={s.id}
                  layout={prefersReduced ? false : "position"}
                  transition={{ layout: springSoft }}
                  initial={{ opacity: 0, scale: 0.96 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.96, transition: { duration: 0.2 } }}
                  style={{ gridColumnStart: col }}
                  className="rounded-4xl border border-black/[0.08] bg-black/[0.02] p-1.5"
                >
                  <div className="rounded-[calc(2rem-6px)] bg-ground-sunken p-3">
                    <div className="flex items-center gap-2">
                      <SevChip sev={s.severity} />
                      <StatusChip status={s.status} />
                      <span className="ml-auto font-mono text-2xs text-ink-3">{timeAgo(s.first_seen)}</span>
                    </div>
                    <div className="mt-2 text-sm font-medium text-ink">{s.title}</div>
                    <div className="mt-1 font-mono text-2xs text-signal-dim">{s.id}</div>
                    {lane === "gate" && s.suggested_runbook_id && (
                      <div className="mt-3 flex gap-2">
                        <CTA icon={false} onClick={() => decideApproval(s.id, "allow")}>Approve</CTA>
                        <CTA icon={false} variant="ghost" onClick={() => decideApproval(s.id, "deny")}>Reject</CTA>
                      </div>
                    )}
                  </div>
                </motion.div>
              );
            })}
          </AnimatePresence>
        </div>
      </LayoutGroup>

      {live.length === 0 && (
        <div className="rounded-4xl border border-black/[0.06] p-10 text-center text-ink-3">
          Waiting for incidents — break the demo workload to watch the loop move.
        </div>
      )}
    </div>
  );
}
```

Verify `decideApproval`'s actual signature in `data/api.ts:24` and match the call (arg order/values `"allow"`/`"deny"` vs a boolean — adjust to the real contract).

- [ ] **Step 4: Mock-mode scripted incidents**

In `mock.ts`, add a small set of situations that a timer advances through statuses (detected→diagnosed→acting→resolved) so the view animates with `VITE_DATA_MODE=mock`. Keep it self-contained; do not alter the existing `situations` export used by Incidents unless additive.

- [ ] **Step 5: Verify build + browser animation check**

Run: `cd frontend && npm run build` (clean). Then in the Browser pane (mock mode), open the Pipeline tab and confirm cards render in the right lanes and a status change **glides** a card across columns (not a pop). Screenshot as proof. If cards pop, the flat-list/grid-column invariant was broken — re-check Step 3.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/Pipeline.tsx frontend/src/components/Shell.tsx frontend/src/App.tsx frontend/src/data/mock.ts
git commit -m "feat(frontend): live incident-journey pipeline view (layout FLIP across lanes)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Real-data Overview — kill mock-in-live

**Files:**
- Modify: `frontend/src/views/Overview.tsx` (remove mock imports in live mode; real fleet + metric-history sparklines)
- Modify: `frontend/src/data/api.ts` + `frontend/src/data/source.ts` (a `loadFleet` if the endpoint route is chosen)
- Optionally Modify: `services/read/app.py` (a `GET /fleet` endpoint) — OR derive fleet client-side. Pick client-side derivation first (no backend change); add `/fleet` only if needed.

**Interfaces:** Consumes `metrics.alertsIngested` (real), a rolling metric buffer; produces real fleet-health + real sparklines in live mode.

- [ ] **Step 1: Add a rolling metric-history buffer**

In `Overview.tsx`, keep a `useRef`/state buffer of the last ~40 `metrics` values (pushed each time `useLiveData(loadMetrics)` updates). Use the buffer as the sparkline series **in live mode**; keep `series(...)` **only** in mock mode. Replace hardcoded "8,420 raw alerts" with `metrics.alertsIngested`.

- [ ] **Step 2: Real fleet health**

Client-side: derive fleet from the services the console already knows (ping each service's always-exempt `/health` via `fetch`, no token needed), or add `GET /fleet` to read-service that does the pings server-side. Choose client-side derivation (simplest, no backend change). In mock mode keep the canned `services` list.

- [ ] **Step 3: Guard mock imports behind mode**

The `import { metrics as mockMetrics, series, services } from "../data/mock"` stays, but its uses become mock-mode-only fallbacks. In live mode, none of the three hardcoded sources feed the rendered numbers.

- [ ] **Step 4: Verify build + browser check (both modes)**

Run: `cd frontend && npm run build` (clean). Browser check in mock mode (canned values show) and, if a backend is up, live mode (real numbers). Confirm no synthetic sparkline noise in live.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/Overview.tsx frontend/src/data/api.ts frontend/src/data/source.ts
git commit -m "feat(frontend): real-data Overview — kill mock sparklines/fleet in live mode

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Audit-trail explorer

**Files:**
- Create: `frontend/src/views/Audit.tsx` (or a rich panel added to `Governance.tsx`)
- Modify: `frontend/src/components/Shell.tsx` + `App.tsx` if a new tab

**Interfaces:** Consumes `loadAudit` (`data/source.ts`) + `AuditRow` (`types.ts`). Read-only.

- [ ] **Step 1: Build the explorer**

A filterable table over `loadAudit()`: filter inputs for actor, decision (`allow`/`deny`/`pending`), and `correlation_id`; render the trail with the existing chip/mono styling. Use `useLiveData(loadAudit, [])` so it updates live. Null-safe (empty state when no rows). Decide tab vs. Governance-panel — a tab is cleaner given it's a distinct concern; add it to `View` + `Shell` + `App` the same way as Pipeline.

- [ ] **Step 2: Verify build + browser check**

Run: `cd frontend && npm run build` (clean). Browser check: filters narrow the rows; empty state renders.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/Audit.tsx frontend/src/components/Shell.tsx frontend/src/App.tsx
git commit -m "feat(frontend): audit-trail explorer (filter by actor/decision/correlation_id)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Docs + ADR

**Files:**
- Create: `docs/UI.md`
- Modify: `flow.md` (note the real-time read path + pipeline view), `README.md` (Stream C shipped)
- Modify: `architectural.md` (ADR-018 — real-time console read path / SSE)

**Interfaces:** None (docs).

- [ ] **Step 1: Write `docs/UI.md`**

Each view (Overview, Incidents, Pipeline, Governance, Audit): what it shows, the mock/live modes, the SSE architecture (thread→async fan-out, query-token auth, poll fallback), the Apple-theme note. Include screenshots from the browser checks.

- [ ] **Step 2: ADR-018**

Add to `architectural.md` (verify the next ADR number is 018 — last was 017): document the SSE read-path decision (SSE over WebSocket, query-param token under the shared-token model, stdlib fan-out, lossy-but-live backpressure since the projection is rebuildable), and the console real-time/Apple-repaint. State the honest limit (query-token acceptable only under the shared-demo-token model).

- [ ] **Step 3: Touch `flow.md` + `README.md`**

`flow.md`: the read-model now pushes over SSE; the console shows a live pipeline. `README.md`: Stream C (real-time console + pipeline view) shipped; ADR count → 18.

- [ ] **Step 4: Commit**

```bash
git add docs/UI.md flow.md README.md architectural.md
git commit -m "docs(stream-c): UI guide, ADR-018 (SSE read path), flow/README updates

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-review notes (author)

- **Spec coverage:** All 8 acceptance criteria map to tasks — live<1s (T1+T2), pipeline (T4), build/mock/live (every FE task), no-regression+browser-verify (T3/T4/T5), no-mock-in-live (T5), audit explorer (T6), gated endpoints (T1 `/stream`, T5 `/fleet` client-side avoids it), off-by-default (T1/T2 guards).
- **Type consistency:** `useLiveData` mirrors `useData`'s `{data,loading,error}`; `View` union extended consistently in Shell+App; `laneOf` uses only real `SituationStatus`/`hitl_mode` values from `types.ts`.
- **Known verify-before-code points flagged inline:** the `pytest-asyncio` marker (T1 S2), `Settings(...)` construction shape (T1 S6), `decideApproval` real signature (T4 S3). Implementers verify these against the live code, not assume.
- **YAGNI:** dropped the separate `stream.ts` (folded into `api.ts`+hook); `/fleet` deferred to client-side derivation unless proven necessary.
