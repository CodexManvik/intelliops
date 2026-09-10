# Agent Activity Trace + "Agent Activity" Console Tab — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture the `RunbookAuthorAgent`'s step-by-step trace (each model turn's reasoning, each tool call + a result summary, the final submit, the terminal outcome), persist it in Postgres, stream it live to the console over SSE, and surface it in a dedicated "Agent Activity" console tab with expandable step rows — without changing what `draft()` produces or its safety properties.

**Architecture:** The agent gains an optional, best-effort `trace` collector it calls at each loop hook point. A `POST /playbooks/draft-async` mints a `run_id`, runs the draft in a daemon thread, and (via an `AgentRunHub` cross-thread pub-sub modeled on the read service's projection) publishes each `TraceStep` to SSE subscribers and appends it to a Postgres `TraceStore`. On completion the thread runs the SAME `_finalize_proposal` helper the synchronous `propose_playbook` uses (one code path) and emits a terminal `outcome` step carrying the `proposal_id`. The console's "Agent Activity" tab lists recent runs and, per run, renders the trace — streaming live via `EventSource` if in flight, or from the stored trace if complete.

**Tech Stack:** Python 3.11, FastAPI (`StreamingResponse` SSE), Pydantic v2, SQLAlchemy Core + Alembic, asyncio pub-sub, threading; React 18 + Vite + `EventSource`. Mirrors `services/read/app.py` (SSE) + `services/read/projection.py` (pub-sub) + `author_decisions`/`PostgresAuthorDecisionStore` (store).

**Spec:** `docs/superpowers/specs/2026-09-10-agent-activity-trace-design.md`

## Global Constraints

- **The trace never changes drafting.** `RunbookAuthorAgent.draft()`'s `trace` param defaults to `None` → byte-identical behavior. When present, every `trace.record(...)` is wrapped best-effort (a trace error never reaches `draft()`). ALL existing `test_runbook_author_agent.py` / `test_runbook_author.py` tests must still pass unchanged.
- **The trace is a UI side-channel only.** It is NEVER fed back into the agent's message list. Tool-result raw content is *summarized* for display, not replayed to the model. Preserves the "untrusted model output" and safety-gate invariants from PR #49.
- **One propose code path.** Factor `propose_playbook`'s post-draft body (unpack 3-tuple/2-tuple → normalize with server id → `ProposedPlaybook` → audit → `AuthorDecision` best-effort → return proposal) into a shared `_finalize_proposal(...)` used by BOTH the sync endpoint and the async draft thread. The safety gate (`Playbook.model_validate` inside the agent) and the server-assigned `ai-<sig>-<uuid>` id (never a model id) are unchanged.
- **Never raises / best-effort persistence.** The draft thread wraps the draft in try/except (like the outcomes consumer), always emits a terminal `outcome` step, and closes the stream. A `TraceStore` write failure logs, never fails the draft or the request; the live stream still works from the hub even if persistence is down.
- **SSE is nginx-safe.** Mirror read's headers exactly (`Cache-Control: no-cache`, `Connection: keep-alive`, `X-Accel-Buffering: no`), 15s keepalives, `unsubscribe` in `finally`. Confirm nginx `proxy_buffering off` covers `/api/gov/`.
- **Off-by-default unchanged.** A trace only exists when the agent runs, which is already gated by `runbook_author_mode=="openai"`. No new Secret; the sync `POST /playbooks/proposed` endpoint stays unchanged.
- **Slim-boundary (ADR-022).** All governance additions use stdlib + httpx only. `import services.governance.app` must not import torch/numpy/sentence_transformers.
- **Every commit ends with:** `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Locked decisions (spec §9 open questions)

- Table shape: **`agent_runs` header + `agent_run_steps` child** (cheap feed).
- Async draft: **`threading.Thread` per draft** (matches the consumer pattern; human-initiated + low-frequency).
- Button: **"Draft with AI" navigates to the Agent Activity tab focused on the run**; the incident keeps a small "drafting… view activity" affordance.
- Retention: **keep all runs** (prune is a trivial follow-up).
- Result summary: **compact per-tool digest, expandable**; never the raw blob.

---

### Task 1: `TraceStep` + `RunSummary` contracts + `TraceCollector` (best-effort) + tests

**Files:**
- Modify: `common/contracts.py` (add `TraceStep`, `RunSummary`, and step-kind constants/enums matching the surrounding style)
- Create: `services/governance/adapters/trace_collector.py` (`TraceCollector`)
- Test: `services/governance/tests/test_trace_collector.py`

**Interfaces:**
- Produces: `TraceStep` (fields per spec §5.1: `run_id, seq, kind, ts, text?, tool?, arguments?, result_summary?, detail?`); `RunSummary` (`run_id, started_at, status, signature, step_count, proposal_id?`). `TraceCollector(run_id, sink=None)` with methods `model_turn(text)`, `tool_call(tool, arguments, result_summary)`, `submit(detail)`, `outcome(status, proposal_id=None)` — each builds a `TraceStep` with a monotonic `seq`, calls `self._emit(step)`, and NEVER raises (wrap the sink call in try/except). `_emit` forwards to an optional `sink` callable `(TraceStep) -> None` (the hub+store wiring is Task 4/5; here the sink is injectable for tests).
- Consumes: nothing new.

- [ ] **Step 1: Write the failing test**

```python
# services/governance/tests/test_trace_collector.py
from common.contracts import TraceStep
from services.governance.adapters.trace_collector import TraceCollector


def test_records_ordered_steps_with_monotonic_seq():
    seen = []
    c = TraceCollector("run-1", sink=seen.append)
    c.model_turn("thinking about disk io")
    c.tool_call("get_past_outcomes", {"signature": "sig-x"}, "restart 4/5")
    c.submit({"name": "Fix", "actions": ["restart"], "rationale": "r", "cited_facts": ["restart 4/5"]})
    c.outcome("succeeded", proposal_id="prop-1")
    assert [s.kind for s in seen] == ["model_turn", "tool_call", "submit", "outcome"]
    assert [s.seq for s in seen] == [0, 1, 2, 3]
    assert seen[0].text == "thinking about disk io"
    assert seen[1].tool == "get_past_outcomes" and seen[1].arguments == {"signature": "sig-x"}
    assert seen[1].result_summary == "restart 4/5"
    assert seen[3].detail == {"status": "succeeded", "proposal_id": "prop-1"}
    assert all(s.run_id == "run-1" for s in seen)


def test_text_is_truncated():
    seen = []
    c = TraceCollector("run-1", sink=seen.append)
    c.model_turn("x" * 5000)
    assert len(seen[0].text) < 5000  # capped


def test_never_raises_when_sink_raises():
    def boom(_step):
        raise RuntimeError("sink down")
    c = TraceCollector("run-1", sink=boom)
    # must not propagate
    c.model_turn("t")
    c.tool_call("get_system_context", {}, "unconfigured")
    c.outcome("failed")


def test_no_sink_is_a_noop():
    c = TraceCollector("run-1")  # sink=None
    c.model_turn("t")            # does not raise, does nothing
    c.outcome("gave_up")
```

- [ ] **Step 2: Run to verify it fails.** `uv run pytest services/governance/tests/test_trace_collector.py -v` → FAIL.

- [ ] **Step 3: Add contracts.** In `common/contracts.py`, after the `AuthorDecision` block. Use a str-enum `TraceStepKind` (values `model_turn`/`tool_call`/`submit`/`outcome`) matching the codebase's enum style (`HitlMode`, `AuthorDecisionDisposition`), OR plain `str` if simpler — match the surrounding convention. `TraceStep` fields exactly per spec §5.1. `RunSummary` per spec §5.2. Add a module constant `_TRACE_TEXT_CAP = 4000`.

- [ ] **Step 4: Implement `TraceCollector`.**
```python
# services/governance/adapters/trace_collector.py
"""TraceCollector: the agent's step recorder. Best-effort — a sink failure or
any error while recording NEVER propagates into draft(). The trace is a UI
side-channel; it is never fed back into the agent's own message list."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from common.contracts import TraceStep

logger = logging.getLogger("intelliops.governance.trace")

_TEXT_CAP = 4000


class TraceCollector:
    def __init__(self, run_id: str, sink: Callable[[TraceStep], None] | None = None) -> None:
        self._run_id = run_id
        self._sink = sink
        self._seq = 0

    def _emit(self, kind: str, **fields) -> None:
        try:
            step = TraceStep(run_id=self._run_id, seq=self._seq, kind=kind,
                             ts=datetime.now(UTC), **fields)
            self._seq += 1
            if self._sink is not None:
                self._sink(step)
        except Exception:  # best-effort: a trace failure must never break drafting
            logger.warning("trace record failed (%s); continuing", kind, exc_info=True)

    def model_turn(self, text: str | None) -> None:
        if not text:
            return
        self._emit("model_turn", text=text[:_TEXT_CAP])

    def tool_call(self, tool: str, arguments: dict, result_summary: str) -> None:
        self._emit("tool_call", tool=tool, arguments=arguments,
                   result_summary=(result_summary or "")[:_TEXT_CAP])

    def submit(self, detail: dict) -> None:
        self._emit("submit", detail=detail)

    def outcome(self, status: str, proposal_id: str | None = None) -> None:
        self._emit("outcome", detail={"status": status, "proposal_id": proposal_id})
```
(Note: incrementing `_seq` before the sink call means a sink exception still advances seq — acceptable; the test asserts 0..3 with a passing sink. If the enum route is taken, pass `TraceStepKind(kind)`.)

- [ ] **Step 5: Run tests → PASS.** Also `uv run pytest services/governance/tests/ -q` (no regressions).

- [ ] **Step 6: Commit.**
```bash
git add common/contracts.py services/governance/adapters/trace_collector.py services/governance/tests/test_trace_collector.py
git commit -m "feat(governance): TraceStep/RunSummary contracts + best-effort TraceCollector"
```

---

### Task 2: `TraceStore` (InMemory + Postgres) + `agent_runs`/`agent_run_steps` tables + Alembic 0006 + make_stores

**Files:**
- Modify: `common/db.py` (add `agent_runs` + `agent_run_steps` tables on `METADATA`)
- Create: `alembic/versions/0006_agent_run_traces.py`
- Create: `services/governance/adapters/trace_store.py` (InMemory + Postgres)
- Modify: `common/stores.py` (`Stores.trace_store` + both `make_stores` branches)
- Test: `services/governance/tests/test_trace_store.py` (no-DB shape test) + `tests/test_postgres_agent_traces.py` (testcontainers, mirroring `tests/test_postgres_author_decisions.py`) + a one-line TRUNCATE add in `tests/db_fixtures.py`

**Interfaces:**
- Produces: `TraceStore` protocol + `InMemoryTraceStore` / `PostgresTraceStore` with: `start_run(run_id, signature, ts)`, `append_step(step: TraceStep)`, `finish_run(run_id, status, proposal_id, ts)`, `steps(run_id) -> list[TraceStep]`, `recent_runs(limit=50) -> list[RunSummary]`. `Stores.trace_store`.
- Consumes: `common.db.METADATA`, `to_payload`/`from_payload`, `TraceStep`, `RunSummary`.

- [ ] **Step 1: Inspect the pattern.** Read `common/db.py` `author_decisions` Table + `services/governance/adapters/author_decision_store.py` `PostgresAuthorDecisionStore` + `alembic/versions/0005_author_decisions.py`. Verify current head: `uv run alembic heads` (expect `0005_author_decisions`).

- [ ] **Step 2: Tables in `common/db.py`.** `agent_runs` (`run_id` PK-ish/unique, `signature`, `status`, `proposal_id` nullable, `started_at`, `finished_at` nullable, `payload` JSON) + `agent_run_steps` (`id` autoincrement PK, `run_id` indexed, `seq`, `kind`, `ts`, `payload` JSON). Use the portable `_JSON` variant the file already uses (NOT raw JSONB). Index `agent_run_steps.run_id` and `agent_runs.started_at` (for the feed).

- [ ] **Step 3: Alembic `0006_agent_run_traces.py`.** `revision="0006_agent_run_traces"`, `down_revision="0005_author_decisions"` (copy 0005's `revision` string verbatim — confirm by opening the file). `upgrade()` creates both tables + indexes; raw `JSONB` inside the migration is fine (targets real Postgres). `downgrade()` drops both. Mirror `0005`'s structure. Verify `uv run alembic heads` shows a single head `0006_agent_run_traces`.

- [ ] **Step 4: Implement the stores.** `InMemoryTraceStore` keeps a dict `run_id -> {summary, steps[]}`. `PostgresTraceStore`: `start_run` inserts an `agent_runs` row (status "running"); `append_step` inserts an `agent_run_steps` row (`payload=to_payload(step)`); `finish_run` updates the `agent_runs` row (status, proposal_id, finished_at + rewrite payload); `steps(run_id)` selects steps ordered by `seq`; `recent_runs(limit)` selects `agent_runs` ordered by `started_at DESC` and maps to `RunSummary` (step_count via a subquery or a counter kept on the header payload — simplest: keep a `step_count` on the header updated per append, OR COUNT in the query). All writes best-effort at the CALLER; the store methods themselves may raise (caller wraps) EXCEPT `steps`/`recent_runs` which the endpoints guard.

- [ ] **Step 5: Wire `make_stores`.** Add `trace_store: object` to `Stores`; postgres → `PostgresTraceStore(engine)`; file/default → `InMemoryTraceStore()`.

- [ ] **Step 6: Tests.** Shape test (no-DB): tables exist with expected columns; InMemory store round-trips a run + steps + recent_runs ordering + finish updates status/proposal_id. Postgres test (testcontainers): start→append×N→finish, `steps` ordering, `recent_runs`. Add `agent_runs, agent_run_steps` to the `clean_db` TRUNCATE list. Run `uv run pytest services/governance/tests/test_trace_store.py -q`, `uv run pytest -k stores -q`, `uv run alembic heads`.

- [ ] **Step 7: Commit.**
```bash
git add common/db.py common/stores.py alembic/versions/0006_agent_run_traces.py services/governance/adapters/trace_store.py services/governance/tests/test_trace_store.py tests/test_postgres_agent_traces.py tests/db_fixtures.py
git commit -m "feat(governance): TraceStore (in-mem + Postgres) + agent_run tables + 0006 migration"
```

---

### Task 3: Thread the trace through `RunbookAuthorAgent` + tool result summaries + tests

**Files:**
- Modify: `services/governance/adapters/runbook_author.py` (`RunbookAuthorAgent.draft`/`_run`/`_handle_tool_call` gain optional trace hooks)
- Modify: `services/governance/adapters/author_tools.py` (add a `summarize_result(name, result: dict) -> str` helper for compact digests) OR put the summarizer in trace_collector — implementer's call; keep it near the tool knowledge (author_tools) preferably
- Test: `services/governance/tests/test_runbook_author_agent.py` (add trace-capture tests; existing tests MUST still pass unchanged)

**Interfaces:**
- Consumes: `TraceCollector` (Task 1).
- Produces: `RunbookAuthorAgent.draft(situation, hint=None, trace=None)` — the `trace` (a `TraceCollector` or None) is recorded at hook points. Return type unchanged: `(playbook, rationale, cited_facts) | None`.

- [ ] **Step 1: Write failing tests** (use the existing fake chat client + fake toolbox; pass a `TraceCollector` with a list sink):
  - happy path: model does one `model_turn` (content) → one `get_past_decisions` tool_call → `submit_runbook`. Assert the trace sink saw, in order: a `model_turn` (the content), a `tool_call` (tool="get_past_decisions", arguments, a result_summary string), and a `submit` (detail has name/actions/rationale/cited_facts). (The terminal `outcome` is emitted by the CALLER in Task 4, not inside draft — so draft's trace ends at `submit` on success; document this.)
  - budget exhaustion: model never submits → trace saw the model_turns/tool_calls but NO submit; draft returns None. (Outcome emitted by caller.)
  - trace=None (default): behavior identical to today — reuse an existing happy-path assertion with trace omitted; the agent returns the same draft. (Guards "never changes drafting".)
  - a tool whose result is an `{"error": ...}` dict still produces a `tool_call` trace step with a sensible summary and the loop continues.

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement.**
  - `draft(self, situation, hint=None, trace=None)` → pass `trace` into `_run`.
  - In `_run`, after `_call_model` returns a message: if `message.get("content")` and `trace`: `trace.model_turn(message["content"])`. (Emit for both the tool-call branch's assistant content AND the plain-content branch.)
  - In the `for call in tool_calls` loop: after computing `result` from `_handle_tool_call`, if `trace`: parse the call's tool name + arguments (reuse the same parsing `_handle_tool_call` does) and `trace.tool_call(name, arguments, summarize_result(name, result_content))`. For the `submit_runbook` call specifically, when it validates and returns, if `trace`: `trace.submit({"name": playbook.name, "actions": [s.action for s in playbook.steps], "rationale": rationale, "cited_facts": cited_facts})` BEFORE returning.
  - `summarize_result(name, result)` in author_tools: a compact one-liner per tool (e.g. past_outcomes → "restart 4/5, scale 0/2"; system_context → first ~120 chars or "unconfigured"; past_decisions → "N prior decisions"; human_decisions → "N recent decisions"; incident_details → "signature=… severity=…"; list_actions → "7 actions"; error dict → "error: <class>"). Never dump the raw blob.
  - Keep every trace call guarded by `if trace is not None:` and the collector is itself best-effort — a summarizer bug can't break drafting (wrap `summarize_result` in try/except returning "").
  - Do NOT feed any trace text back into `messages`.

- [ ] **Step 4: Run tests → PASS**, and CRUCIALLY `uv run pytest services/governance/tests/test_runbook_author_agent.py services/governance/tests/test_runbook_author.py -q` — every pre-existing test green (behavior unchanged when trace is None).

- [ ] **Step 5: Commit.**
```bash
git add services/governance/adapters/runbook_author.py services/governance/adapters/author_tools.py services/governance/tests/test_runbook_author_agent.py
git commit -m "feat(governance): thread an optional best-effort trace through the agent loop"
```

---

### Task 4: `AgentRunHub` (cross-thread SSE pub-sub) + tests

**Files:**
- Create: `services/governance/agent_run_hub.py`
- Test: `services/governance/tests/test_agent_run_hub.py`

**Interfaces:**
- Produces: `AgentRunHub` modeled on `services/read/projection.py`'s pub-sub: `bind_loop(loop)`, `subscribe(run_id, maxsize=1000) -> asyncio.Queue` (call on the loop thread), `unsubscribe(run_id, q)`, `publish(run_id, step: TraceStep)` (call from worker threads; marshals via `loop.call_soon_threadsafe`; drop-oldest on `QueueFull`). Also `mark_ended(run_id)` so a stream knows to close, and a per-run subscriber set with a lock.
- Consumes: `TraceStep`.

- [ ] **Step 1: Write failing tests.**
  - `subscribe` + `publish` (same-thread, with a running loop via `asyncio.run`/an event loop fixture): a published step arrives on the queue.
  - drop-oldest: fill a maxsize=1 queue, publish another → the newest is retained (mirror read's `_deliver` behavior).
  - `unsubscribe`: after unsubscribe, a publish does not reach the old queue.
  - two subscribers on the same run_id both receive; a subscriber on a different run_id does not.
  - `publish` before `bind_loop` (loop is None) is a silent no-op (no raise).
  (Model the tests on `services/read/tests/` for the projection pub-sub if such tests exist — READ them first and match the harness.)

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** by adapting `services/read/projection.py:64-108` (`_subscribers` → a `dict[str, set[asyncio.Queue]]` keyed by run_id, `_subs_lock`, `bind_loop`, `subscribe`/`unsubscribe`/`publish`/`_deliver`). `publish` looks up the run's subscriber set; `mark_ended` can publish a sentinel or set a per-run flag the stream checks.

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Commit.**
```bash
git add services/governance/agent_run_hub.py services/governance/tests/test_agent_run_hub.py
git commit -m "feat(governance): AgentRunHub cross-thread pub-sub for live trace streaming"
```

---

### Task 5: Async draft endpoint + shared `_finalize_proposal` + `GET /agent-runs` + `GET /agent-runs/{id}` + tests

**Files:**
- Modify: `services/governance/app.py` (factor `_finalize_proposal`; add `POST /playbooks/draft-async`, `GET /agent-runs`, `GET /agent-runs/{run_id}`; wire `app.state.trace_store` + `app.state.agent_run_hub`; `bind_loop` in the lifespan)
- Test: `services/governance/tests/test_draft_async.py` + extend `test_proposed_routes.py` (confirm the refactor didn't change the sync path)

**Interfaces:**
- Consumes: `TraceCollector`, `TraceStore`, `AgentRunHub`, the agent.
- Produces: `_finalize_proposal(situation, drafted, requested_by) -> ProposedPlaybook` (the factored post-draft body, lines 256-306 today); `POST /playbooks/draft-async` returns `202 {run_id}`; the two GET endpoints.

- [ ] **Step 1: Write failing tests.** Use a fake author returning a fixed 3-tuple + an InMemoryTraceStore + a real/あるいは fake hub; drive via TestClient.
  - `_finalize_proposal` produces the same `ProposedPlaybook` shape as the sync endpoint (records AuthorDecision, server id, audit) — assert against the existing proposed-routes expectations.
  - `POST /playbooks/draft-async` returns 202 with a `run_id`; after the draft thread completes (join it in the test or poll the store), the trace store has steps for that run ending in an `outcome` step with `status="succeeded"` + a `proposal_id`, and the proposal exists in `proposed_store`.
  - a fake author returning None → the run's terminal `outcome` is `gave_up`/`failed` and NO proposal is created; endpoint still returned 202.
  - `GET /agent-runs` lists the run; `GET /agent-runs/{run_id}` returns its steps in order.
  - RBAC: a requester lacking permission → 403 (same as sync propose).
  (For deterministic tests, make the draft run synchronously-joinable: e.g. the endpoint stores the thread on app.state, or accept a test hook to run inline. Prefer storing the thread + a helper to await it in tests over sleeps.)

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement.**
  - Factor lines 256-306 of `propose_playbook` into `_finalize_proposal(situation, drafted, requested_by)`; `propose_playbook` now calls it (sync path unchanged in behavior).
  - `_init_state`: `app.state.trace_store = stores.trace_store`; `app.state.agent_run_hub = AgentRunHub()`.
  - lifespan: after obtaining the running loop, `app.state.agent_run_hub.bind_loop(asyncio.get_running_loop())` (governance's lifespan is async — add if needed). Keep the existing consumer thread.
  - `POST /playbooks/draft-async` (body = the existing `ProposeRequest`): RBAC check; mint `run_id`; `trace_store.start_run(run_id, situation.signature, now)` (best-effort); define a `sink(step)` that does `hub.publish(run_id, step)` AND `trace_store.append_step(step)` (each best-effort); start a daemon thread that:
    - `collector = TraceCollector(run_id, sink)`,
    - `drafted = app.state.runbook_author.draft(situation, hint, trace=collector)` wrapped in try/except,
    - if drafted: `proposal = _finalize_proposal(...)`; `collector.outcome("succeeded", proposal.id)`; `trace_store.finish_run(run_id, "succeeded", proposal.id, now)`,
    - else: `collector.outcome("gave_up")` (or "failed" on exception); `trace_store.finish_run(run_id, status, None, now)`,
    - finally: `hub.mark_ended(run_id)`.
    Return `202 {"run_id": run_id}`.
  - `GET /agent-runs` → `trace_store.recent_runs(limit)` (guard exceptions → []). `GET /agent-runs/{run_id}` → `trace_store.steps(run_id)` (404 if unknown/empty header).
  - Import `asyncio`, `threading` (already), `AgentRunHub`, `TraceCollector`.

- [ ] **Step 4: Run tests → PASS**; full governance suite `uv run pytest services/governance/tests/ -q`; slim-boundary check `uv run python -c "import services.governance.app, sys; print([m for m in ('torch','numpy','sentence_transformers') if m in sys.modules])"` → `[]`.

- [ ] **Step 5: Commit.**
```bash
git add services/governance/app.py services/governance/tests/test_draft_async.py services/governance/tests/test_proposed_routes.py
git commit -m "feat(governance): async draft endpoint + shared _finalize_proposal + agent-runs read endpoints"
```

---

### Task 6: SSE stream endpoint `GET /agent-runs/{run_id}/stream` + tests

**Files:**
- Modify: `services/governance/app.py` (add the SSE endpoint)
- Test: `services/governance/tests/test_agent_run_stream.py`

**Interfaces:**
- Consumes: `AgentRunHub`, `TraceStore`.
- Produces: `GET /agent-runs/{run_id}/stream` → `StreamingResponse(media_type="text/event-stream")`.

- [ ] **Step 1: Write failing tests.** Mirror how `services/read/tests` test the `/stream` endpoint (READ them first). At minimum: with `httpx.AsyncClient`/`TestClient` streaming, connect to the stream for a run that already has stored steps → the response replays those steps as `data: {json}\n\n` lines (assert the first stored step appears). Auth: when a token is configured, a missing/wrong `?token` → 401; AUTH_MODE off → open. (Full live-streaming assertions are hard in a unit test — cover the replay + auth + the media type + that it terminates after an `outcome` step; the hub's live delivery is unit-tested in Task 4.)

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement**, mirroring `services/read/app.py:125-155`:
  - authorize via the same `?token` helper read uses (reuse or replicate `_stream_authorized`); AUTH_MODE off → open.
  - `async def gen()`: `q = hub.subscribe(run_id)`; `try:` → `yield ": connected\n\n"`; FIRST replay `trace_store.steps(run_id)` (best-effort) as `data:` lines (so a late/after-completion connection sees the whole run); if the run is already ended (header status != running), yield the stored steps and return; else loop: `await asyncio.wait_for(q.get(), timeout=15.0)` → on step `yield f"data: {json.dumps(step.model_dump(mode='json'))}\n\n"`, and if `step.kind == "outcome"` break; on `TimeoutError` → `yield ": keepalive\n\n"`; `finally:` `hub.unsubscribe(run_id, q)`.
  - `StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control":"no-cache","Connection":"keep-alive","X-Accel-Buffering":"no"})`.
  - Guard: replay-then-live has a race (a step could arrive between the store read and the subscribe). Mitigate by subscribing BEFORE reading stored steps, then de-duping by `seq` (skip streamed steps whose seq ≤ the max replayed seq). Implement the subscribe-first-then-replay ordering + seq de-dup.

- [ ] **Step 4: Run → PASS**; governance suite green.

- [ ] **Step 5: Commit.**
```bash
git add services/governance/app.py services/governance/tests/test_agent_run_stream.py
git commit -m "feat(governance): SSE endpoint streaming the live agent-run trace (replay + live, seq-deduped)"
```

---

### Task 7: Console — "Agent Activity" tab (feed + expandable trace, live via EventSource) + tests

**Files:**
- Create: `frontend/src/views/AgentActivity.tsx`
- Modify: `frontend/src/components/Shell.tsx` (nav entry), `frontend/src/App.tsx` (route/tab), `frontend/src/data/api.ts` (`draftAsync`, `loadAgentRuns`, `loadAgentRun`, an `openAgentRunStream` EventSource helper), `frontend/src/data/source.ts` (live/mock wiring + a mock run), `frontend/src/data/types.ts` (TraceStep/RunSummary TS types)
- Modify: `frontend/src/views/Incidents.tsx` (rewire "Draft a runbook with AI" to `draftAsync` → focus the Agent Activity tab on the run)
- Test: a light vitest/RTL test if the frontend has a test setup (CHECK `frontend/package.json`); otherwise a build check + manual verification note

**Interfaces:**
- Consumes: `GET /api/gov/agent-runs`, `GET /api/gov/agent-runs/{id}`, `GET /api/gov/agent-runs/{id}/stream`, `POST /api/gov/playbooks/draft-async`.
- Produces: the tab + the rewired button.

- [ ] **Step 1: TS types + api.** Add `TraceStep`/`RunSummary` to `types.ts` (mirror the Pydantic fields). In `api.ts`: `draftAsync(situation, requestedBy) -> {run_id}` (POST draft-async); `loadAgentRuns() -> RunSummary[]`; `loadAgentRun(runId) -> TraceStep[]`; `openAgentRunStream(runId, onStep, onDone)` using `new EventSource(\`${GOV}/agent-runs/${runId}/stream\`)` (+ token if the app uses one — check how the read EventSource is opened in `useLiveData`/source.ts and match). In `source.ts`, gate each behind `LIVE` with a mock fallback (a seeded mock run with a few steps).

- [ ] **Step 2: `AgentActivity.tsx`.** Left: a list of recent runs (signature, status chip running/succeeded/gave_up/failed, time, step count) from `loadAgentRuns`, auto-refreshing (interval or on focus). Right: the selected run's trace as an ordered list of **expandable rows** (match the screenshot):
  - `tool_call` → collapsed: `🔧 {tool} — {result_summary}`; expanded: arguments (pretty JSON) + full result_summary.
  - `model_turn` → collapsed: `💭 Reasoning`; expanded: the text.
  - `submit` → collapsed: `📝 Drafted {name}`; expanded: actions + rationale + cited_facts.
  - `outcome` → a status row; `succeeded` links to the proposal in Governance (by proposal_id), `gave_up`/`failed` shows the reason.
  If the selected run is in flight (status running), open `openAgentRunStream` and append steps live; else `loadAgentRun` for stored steps. Clean up the EventSource on unmount / run change.
  Follow the existing console's styling/components (`primitives.tsx`, the other views) — match, don't reinvent.

- [ ] **Step 3: Nav + route.** Add "Agent Activity" to `Shell.tsx` nav and `App.tsx` routing, consistent with the existing tabs (Overview/Incidents/Governance/System). Support focusing a specific run (e.g. a `?run=` param or app state) so the Incidents button can deep-link.

- [ ] **Step 4: Rewire the Draft button.** In `Incidents.tsx`, change the "Draft a runbook with AI" handler from `proposePlaybook` to `draftAsync(situation, "oncall-alice")`, then navigate to the Agent Activity tab focused on the returned `run_id` (and show a small "drafting…" state). Keep the existing toast/error handling shape.

- [ ] **Step 5: Verify the build.** `cd frontend && npm ci && npm run build` (or the repo's build cmd — check package.json/CI) → clean, 0 TS errors. If a test setup exists, add a small render test for AgentActivity (a run with steps renders expandable rows); otherwise document manual verification (mock mode shows the seeded run; live mode streams). Run any frontend lint the CI runs.

- [ ] **Step 6: Commit.**
```bash
git add frontend/
git commit -m "feat(console): Agent Activity tab — live, expandable agent-run trace + rewired Draft button"
```

---

### Task 8: Deploy + docs — nginx SSE-safety for the stream path, docs

**Files:**
- Modify: `deploy/nginx.conf` (ensure `/api/gov/` (or a `/api/gov/agent-runs/…/stream` location) has `proxy_buffering off` + HTTP/1.1 for SSE, matching the read `/stream` handling)
- Modify: `docs/OPERATIONS.md` (a short "Agent Activity / trace" section)
- Test: `helm lint` (dockerized) + a manual nginx-config sanity note

**Interfaces:** none (config + docs).

- [ ] **Step 1:** Inspect `deploy/nginx.conf` — how the read `/api/read/` (or its `/stream`) disables buffering for SSE. Ensure the governance `/api/gov/` proxy (or a dedicated nested location for the stream) has `proxy_buffering off;`, `proxy_http_version 1.1;`, `proxy_set_header Connection "";`, and a long/absent read timeout, so the SSE trace stream isn't buffered. Match the read pattern exactly; do not weaken the other `/api/gov/` routes.
- [ ] **Step 2:** `docs/OPERATIONS.md`: a short section — what the Agent Activity tab shows, that the trace is best-effort/observer-only (never changes drafting), that it streams over SSE and is stored in Postgres, and that it only populates when the agent runs (author mode on).
- [ ] **Step 3:** `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W)":/work -w /work alpine/helm:latest lint deploy/k8s/platform` → 0 failed. (Docker daemon may be down; a local `helm lint` is a like-for-like substitute — note which was used.)
- [ ] **Step 4: Commit.**
```bash
git add deploy/nginx.conf docs/OPERATIONS.md
git commit -m "feat(deploy): SSE-safe nginx for the agent-run stream; docs"
```

---

## Notes for the executor

- **Do NOT modify** `RemediationStep`, the denylist, sandbox, RCA ranking, correlation, the execution path (`services/action/*`), or the sync `POST /playbooks/proposed` behavior.
- **The load-bearing invariant**: `draft()` with `trace=None` must be byte-identical to today — the existing agent tests are the guard; never let a trace hook change control flow.
- **Governance lifespan is async** — Task 5 adds `bind_loop(get_running_loop())`; confirm the lifespan already has access (it's an `@asynccontextmanager`). The existing consumer thread stays.
- **SSE race**: subscribe-before-replay + seq de-dup (Task 6) is the correctness crux — get it right so no step is dropped or duplicated when a client connects mid-run.
- **Slim-boundary**: after Tasks 3/5/6, `import services.governance.app` must not pull ML deps — verify in Task 5.
- Tasks 5 and 6 both edit `services/governance/app.py` — dispatch them SEQUENTIALLY (5 before 6), never parallel.
