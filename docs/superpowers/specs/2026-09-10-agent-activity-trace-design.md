# Agent Activity Trace + "Agent Activity" Console Tab — Design

**Status:** Draft for review
**Date:** 2026-09-10
**Author:** IntelliOps team (CodexManvik) + Claude
**Builds on:** PR #49 (the agentic runbook author) — master `dec90ac`

---

## 1. Problem

When a human clicks "Draft a runbook with AI", the `RunbookAuthorAgent` runs a
bounded tool-calling loop — it reasons, calls read-tools (system context, past
outcomes, human decisions, its own past decisions), and submits a runbook. Today
**none of that is visible.** The operator sees only the final proposal appear in
Governance. There is no way to watch *what the agent is doing* — which tools it
called, what they returned, and the reasoning between steps.

The user wants a page that shows this **step by step, expandable** (like a
CI-log view: each step is a row; click it to see the tool's arguments + result,
or the reasoning text), and **live** — the steps appear as the agent runs.

## 2. Goals

- **Capture** an ordered trace of every step the agent takes during a draft:
  each model turn (reasoning text), each tool call (name + arguments + a summary
  of the result), the final submit (drafted playbook + rationale + cited facts),
  and the terminal outcome.
- **Stream it live** to the console over SSE, so steps appear as they happen.
- **Persist it** in Postgres so a run's trace survives restarts and can be
  re-opened later.
- **Surface it** in a dedicated **"Agent Activity"** console tab: a feed of
  recent agent runs; click one to see its step-by-step trace (streaming live if
  the run is in flight, or the stored trace if complete).
- **Non-invasive**: capturing/streaming the trace MUST NOT change what
  `draft()` produces or its safety properties. Trace writes are best-effort.

## 3. Non-Goals

- No change to the drafting logic, the closed action catalog, HITL, or the
  safety gates. The trace is an observer.
- No trace for the deterministic ranker or the single-shot author — this is
  specifically the tool-calling `RunbookAuthorAgent`.
- No exposure of the raw LLM API key or full raw request/response bodies — the
  trace records the *semantic* steps (tool name, arguments, a result summary,
  reasoning text), not transport internals.
- No auth/RBAC redesign — the SSE endpoint reuses the read service's existing
  `?token` query-param pattern (EventSource can't set headers).

## 4. Background: what exists (build on this)

- **The agent loop** (`services/governance/adapters/runbook_author.py`,
  `RunbookAuthorAgent._run`, lines ~365-416): a `for _round in range(max_rounds)`
  loop. Per round it calls the model (`_call_model`), and if the response has
  `tool_calls` it dispatches each via `_handle_tool_call(call, toolbox)` and
  appends the result. The model's reasoning is `message.get("content")`; the
  terminal `submit_runbook` returns `(playbook, rationale, cited_facts)`. These
  are the exact trace hook points.
- **A proven SSE pattern** (`services/read/app.py:125-155`): an async generator
  over a pub-sub `asyncio.Queue` (`subscribe`/`unsubscribe`), `yield f"data:
  {json.dumps(event)}\n\n"`, 15s keepalive, headers `Cache-Control: no-cache` +
  `Connection: keep-alive` + `X-Accel-Buffering: no`.
- **A proven cross-thread pub-sub** (`services/read/projection.py:64-103`):
  `bind_loop(loop)` from the async lifespan; `subscribe()`/`unsubscribe()` on the
  loop thread; `publish(event)` from worker threads via
  `loop.call_soon_threadsafe`; `_deliver` with `QueueFull` drop-oldest. The
  agent's draft runs in a thread, so this is the exact marshaling we need.
- **The store + migration pattern**: `author_decisions` table
  (`common/db.py`) + `PostgresAuthorDecisionStore` + Alembic `0005`. The trace
  store mirrors it. Current head: `0005_author_decisions` → new migration `0006`.
- **The console**: `frontend/src/` — `views/{Overview,Incidents,Governance,
  System}.tsx`, `Shell.tsx` nav, `data/source.ts` (live-vs-mock switch),
  `data/api.ts`. Governance is proxied at `/api/gov/`, read at `/api/read/`
  (`deploy/nginx.conf`). The "Draft a runbook with AI" button is in
  `Incidents.tsx` (fires `proposePlaybook`).

## 5. Design overview

Three pieces: **capture** (in the agent), **transport** (async draft + SSE +
Postgres), and **display** (the console tab).

```
Console "Agent Activity" tab
  │  POST /api/gov/playbooks/draft-async {situation, requested_by}
  │     → returns { run_id } immediately (202)
  │  opens EventSource  /api/gov/agent-runs/{run_id}/stream?token=…
  ▼
Governance
  ├─ starts draft in a background thread, bound to run_id
  │     RunbookAuthorAgent.draft(situation, hint, trace=TraceCollector(run_id, hub))
  │        each step → trace.record(step) → hub.publish(run_id, step)  ── SSE ──▶ page
  │        AND → TraceStore.append_step(run_id, step)  (Postgres, best-effort)
  │     on completion → the normal propose_playbook path runs (records AuthorDecision,
  │        stores the ProposedPlaybook) → a final "outcome" step carries proposal_id
  │
  ├─ GET /agent-runs                     → recent runs (from TraceStore) for the feed
  ├─ GET /agent-runs/{run_id}            → the full stored trace (fetch-on-open / completed)
  └─ GET /agent-runs/{run_id}/stream     → SSE: replays stored steps, then live ones
```

### 5.1 Capture — `RunbookAuthorTrace` + a trace collector

A new contract `TraceStep` and an in-agent collector. The agent's `draft()`
gains an **optional** `trace` parameter (default `None` = today's behavior
exactly). When present, `_run` calls `trace.record(step)` at each hook point:

- **round start** → nothing emitted until the model responds.
- **model turn** → after `_call_model`, if the message has `content`, record a
  `model_turn` step (the reasoning text, truncated to a sane cap).
- **tool call** → for each `call` in `tool_calls`, record a `tool_call` step:
  `tool` (name), `arguments` (parsed), and — after dispatch — a `result_summary`
  (a compact, human-readable digest of the tool result dict, NOT the raw blob;
  e.g. `get_past_outcomes → {restart: 4/5, scale: 0/2}`).
- **submit** → record a `submit` step with the drafted playbook's `name` +
  `actions` + `rationale` + `cited_facts`.
- **outcome** → a terminal step: `succeeded` (+ `proposal_id`, filled by the
  caller after propose), `gave_up` (rounds exhausted), `rate_limited`, or
  `failed`.

**`TraceStep` contract (new, in common/contracts.py):**
```
run_id: str
seq: int                    # monotonic within a run, for ordering
kind: str                   # "model_turn" | "tool_call" | "submit" | "outcome"
ts: datetime
# kind-specific (all optional):
text: str | None            # model_turn: the reasoning (capped)
tool: str | None            # tool_call: tool name
arguments: dict | None      # tool_call: parsed args
result_summary: str | None  # tool_call: compact digest of the result
detail: dict | None         # submit: {name, actions, rationale, cited_facts}; outcome: {status, proposal_id?}
```

**Safety of capture:** `trace.record` is wrapped so a trace failure never
propagates into `draft()` (best-effort). `result_summary`/`text` are
**truncated** and treated as display data. Crucially, a tool result's raw
content is NOT replayed to the model via the trace — the trace is a side-channel
for the UI only; the agent's own message list is unchanged.

### 5.2 Transport — async draft, the run hub, SSE, and the store

- **`AgentRunHub`** (new, governance) — the cross-thread pub-sub, modeled on the
  read projection: `bind_loop(loop)` (async lifespan), `subscribe(run_id)` /
  `unsubscribe(run_id, q)` (per-run queues, on the loop thread from the SSE
  coroutine), `publish(run_id, step)` (from the draft thread via
  `call_soon_threadsafe`), drop-oldest on `QueueFull`. A run that has ended also
  records a terminal marker so a late subscriber's stream can close.
- **`POST /playbooks/draft-async`** (new) — validates the request + RBAC exactly
  like `propose_playbook`, mints a `run_id` (`run-<uuid8>`), starts a **daemon
  thread** that:
  1. builds a `TraceCollector(run_id, hub, trace_store)`,
  2. calls `runbook_author.draft(situation, hint, trace=collector)`,
  3. on a returned draft, runs the SAME normalize + `ProposedPlaybook` + audit +
     `AuthorDecision` logic `propose_playbook` uses today (factored into a shared
     helper so there is ONE code path), then emits the final `outcome` step with
     the `proposal_id`,
  4. on `None`, emits the appropriate `outcome` (gave_up/rate_limited/failed).
  Returns `202 {run_id}` immediately.
- **`GET /agent-runs/{run_id}/stream`** (new, SSE) — mirrors read's `/stream`:
  authorize via `?token`; subscribe to the hub for `run_id`; FIRST replay any
  already-stored steps for this run (so a page that connects mid-run or after
  completion still sees everything), THEN stream live steps from the queue;
  close when the terminal `outcome` step is seen; 15s keepalives; the SSE
  headers. `data: {json of TraceStep}\n\n`.
- **`GET /agent-runs`** (new) — recent runs for the feed: `run_id`, started_at,
  status (running/succeeded/gave_up/…), the situation signature, step count,
  proposal_id if any. From `TraceStore`.
- **`GET /agent-runs/{run_id}`** (new) — the full stored trace (all steps), for
  fetch-on-open of a completed run.
- **`TraceStore`** (new) — InMemory + Postgres, mirroring
  `AuthorDecisionStore`. `append_step(step)`, `steps(run_id) -> list[TraceStep]`,
  `recent_runs(limit) -> list[RunSummary]`, plus a run-header row updated on
  start/finish. New table(s) `agent_runs` + `agent_run_steps` (or one
  `agent_run_steps` table + a derived summary) in `common/db.py` + Alembic
  `0006_agent_run_traces.py` (down_revision `0005_author_decisions`). Writes are
  best-effort (a trace-store failure logs, never fails the draft).

### 5.3 The "Draft with AI" reconciliation

Today `Incidents.tsx` calls `proposePlaybook(situation, "oncall-alice")` which
POSTs `/playbooks/proposed` and waits for the finished proposal. With the async
model, the button instead calls `draftAsync(situation, requested_by)` →
`{run_id}`, and the UI can either (a) navigate to the Agent Activity tab focused
on that `run_id`, or (b) open the streaming trace inline. **We keep the existing
synchronous `POST /playbooks/proposed` endpoint unchanged** (nothing else breaks,
and it stays the simple path); the async endpoint is additive. The button is
rewired to the async flow so the operator sees the live trace.

### 5.4 Display — the "Agent Activity" tab

- A new nav entry in `Shell.tsx` and a `views/AgentActivity.tsx`.
- **Feed**: `GET /api/gov/agent-runs` → a list of recent runs (signature, status
  chip, time, step count). Auto-refreshes.
- **Detail**: selecting a run opens its trace. If the run is in flight, open an
  `EventSource` on `…/stream?token=…` and append steps live; if complete, `GET
  …/agent-runs/{run_id}` for the stored steps. Either way render an ordered list
  of **expandable step rows** matching the screenshot:
  - `tool_call` → row shows the tool name + a one-line result summary; expand →
    the arguments (pretty JSON) + the full result summary.
  - `model_turn` → row shows "Reasoning"; expand → the reasoning text.
  - `submit` → row shows "Drafted <name>"; expand → actions + rationale + cited
    facts.
  - `outcome` → a terminal status row (succeeded → links to the proposal in
    Governance; gave_up/failed → the reason).
- Mock mode (`VITE_DATA_MODE !== "live"`) shows a seeded example run so the tab
  renders without a backend.

## 6. Data flow (one real run)

1. Operator clicks "Draft with AI" on a gap incident → `POST /playbooks/draft-async`
   → `202 {run_id: "run-ab12cd34"}`.
2. UI opens `EventSource /api/gov/agent-runs/run-ab12cd34/stream?token=…`.
3. Governance's draft thread runs the agent; each step:
   `model_turn` ("The incident shows disk_io_wait with no CPU signal…") →
   `tool_call` get_system_context → `tool_call` get_past_outcomes (→ restart 4/5)
   → `tool_call` get_past_decisions → `model_turn` ("Prior restart worked; I'll
   draft restart + scale") → `submit` (Disk IO Remediation) → each streamed +
   stored.
4. The draft returns; the shared propose helper creates the `ProposedPlaybook`
   + `AuthorDecision`; a final `outcome` step (`succeeded`, `proposal_id`) is
   streamed + stored; the stream closes.
5. The page shows the completed, expandable trace and a link to the proposal in
   Governance (where the human approves it — unchanged).

## 7. Component changes (summary — details in the plan)

- **Contracts**: `TraceStep` (+ maybe a `RunSummary`).
- **DB**: `agent_run_steps` (+ `agent_runs` header) in `common/db.py` + Alembic
  `0006`.
- **Store**: `TraceStore` (InMemory + Postgres) in a new governance adapter;
  added to `Stores` + `make_stores`.
- **Agent**: `draft()` gains an optional `trace` collector param; `_run` records
  steps at the hook points (additive, best-effort, behavior unchanged when
  `trace is None`).
- **Governance**: `AgentRunHub` (pub-sub); the shared propose helper (factor
  `propose_playbook`'s post-draft body); `POST /playbooks/draft-async`; `GET
  /agent-runs`, `GET /agent-runs/{id}`, `GET /agent-runs/{id}/stream` (SSE);
  `bind_loop` in the lifespan.
- **Console**: `views/AgentActivity.tsx` + nav entry; `data/api.ts`
  `draftAsync` + `loadAgentRuns` + `loadAgentRun` + an EventSource helper;
  `data/source.ts` live/mock wiring + a mock run; rewire the Incidents "Draft
  with AI" button to the async flow.
- **Deploy/docs**: nginx already proxies `/api/gov/` (SSE-safe: confirm
  `proxy_buffering off` covers the new stream path); a docs note.

## 8. Safety & failure analysis

| Concern | Handling |
|---|---|
| Trace capture changes the draft | `trace` is optional + best-effort; when `None`, byte-identical behavior. `record()` wrapped so a trace error never reaches `draft()`. Existing agent tests must still pass unchanged. |
| Trace replays untrusted content to the model | It does NOT — the trace is a UI side-channel; the agent's own message list is untouched. Tool-result raw content is summarized for display only. |
| Draft thread crashes | The thread wraps the draft in try/except (like the outcomes consumer); it always emits a terminal `outcome` step and closes the stream; a store failure is logged. |
| SSE connection leaks / nginx buffering | Mirror read's proven headers (`X-Accel-Buffering: no`) + 15s keepalives + `unsubscribe` in `finally`; confirm nginx `proxy_buffering off` for the path. |
| A late subscriber misses early steps | The stream replays stored steps before going live, so a connection at any time sees the whole run. |
| Trace store write fails | Best-effort: logged, never fails the draft or the request. The live stream still works from the hub even if persistence is down. |
| run_id collisions | `run-<uuid8>`; the store keys on it. |
| Auth on the SSE endpoint | Same `?token` pattern as read's `/stream` (EventSource can't set headers); when AUTH_MODE=off (the default/dev), open like the rest. |
| Slim-boundary | All governance additions use stdlib + httpx only; no ML import. Verify `import services.governance.app` stays clean. |

## 9. Open questions for review

1. **Table shape**: one `agent_run_steps` table (append-only, derive run
   summaries by query) vs. a `agent_runs` header + `agent_run_steps` child. The
   header makes the feed cheap; the single table is simpler. Preference? (Design
   leans header + steps.)
2. **Async draft threading**: a plain `threading.Thread` per draft (matches the
   codebase's consumer pattern) vs. a small bounded worker. For human-initiated,
   low-frequency drafts a thread-per-draft is fine — agree?
3. **Button behavior**: on "Draft with AI", navigate to the Agent Activity tab
   focused on the run, or open the trace inline on the incident and also list it
   in the tab? (Design: navigate/focus the tab, since that's the chosen home;
   the incident can show a small "drafting… view activity" affordance.)
4. **Retention**: cap stored runs (e.g. keep last N) or keep all? (Design: keep
   all for now; a prune is a trivial follow-up.)
5. **Result-summary depth**: how much of each tool's result to show (one-line
   digest vs. a few key fields). (Design: a compact digest per tool, expandable;
   never the raw blob.)
