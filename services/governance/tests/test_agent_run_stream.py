"""Tests for GET /agent-runs/{run_id}/stream — the SSE endpoint that streams
an agent run's trace steps live to the console.

Full live-streaming over a real HTTP connection is hard to unit test (a
blocking TestClient would hang waiting on a queue that never closes for a
still-running, unknown, or empty run). What's covered here:

1. `_stream_authorized` — off-mode open, token-mode exact match required.
   Mirrors services/read/tests/test_stream.py's equivalent tests.
2. The replay path over a real TestClient, for a run that has ALREADY ENDED:
   deterministic because gen() replays the stored steps and returns without
   ever touching the live queue (the `hub.is_ended(run_id)` early-return).
   This exercises the "client connects after the run finished" case end to
   end, including the media type and the `data:` line framing.
3. The live path (subscribe-before-replay ordering + seq de-dup + the
   terminal `outcome` step closing the stream) driven directly against the
   async generator with a controlled event loop, instead of over a blocking
   HTTP client — deterministic, and it is the one test that actually proves
   the "no dropped or duplicated step" crux: a step is published on the hub
   between `subscribe()` and reading the store, and the test asserts it is
   still delivered exactly once.

The hub's own pub/sub mechanics (bind_loop, subscribe, publish, unsubscribe,
mark_ended/is_ended, per-run isolation, cross-thread delivery) are unit-tested
in test_agent_run_hub.py (Task 4); this file assumes that layer works and
focuses on how the endpoint composes it with the TraceStore.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import ClassVar

from fastapi.testclient import TestClient

from common.contracts import TraceStep, TraceStepKind
from services.governance.adapters.trace_store import InMemoryTraceStore
from services.governance.agent_run_hub import AgentRunHub

NOW = datetime(2026, 9, 10, tzinfo=UTC)


def _step(run_id: str, seq: int, kind: TraceStepKind = TraceStepKind.MODEL_TURN, **kw) -> TraceStep:
    kw.setdefault("text", "step")
    return TraceStep(run_id=run_id, seq=seq, kind=kind, ts=NOW, **kw)


# ---------------------------------------------------------------------------
# 1. _stream_authorized — mirrors services/read/tests/test_stream.py
# ---------------------------------------------------------------------------


def test_stream_authorized_off_mode(monkeypatch):
    from common.config import Settings
    from services.governance import app as governance_app

    monkeypatch.setattr(governance_app, "get_settings", lambda: Settings(auth_mode="off"))

    class Req:  # minimal stub
        query_params: ClassVar[dict] = {}

    assert governance_app._stream_authorized(Req()) is True


def test_stream_authorized_token_mode_requires_match(monkeypatch):
    from common.config import Settings
    from services.governance import app as governance_app

    monkeypatch.setattr(
        governance_app,
        "get_settings",
        lambda: Settings(auth_mode="token", auth_token="secret"),
    )

    class Req:
        def __init__(self, tok):
            self.query_params = {"token": tok}

    assert governance_app._stream_authorized(Req("secret")) is True
    assert governance_app._stream_authorized(Req("wrong")) is False
    assert governance_app._stream_authorized(Req("")) is False


# ---------------------------------------------------------------------------
# 2. Replay path over a real TestClient (deterministic: run already ended)
# ---------------------------------------------------------------------------


def _client(trace_store=None, hub=None):
    from services.governance.app import app

    app.state.trace_store = trace_store or InMemoryTraceStore()
    app.state.agent_run_hub = hub or AgentRunHub()
    return TestClient(app)


def _parse_data_lines(body: str) -> list[dict]:
    """Extract the JSON payload of every `data: {...}` SSE line."""
    events = []
    for line in body.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: ") :]))
    return events


def test_stream_replays_stored_steps_for_ended_run():
    store = InMemoryTraceStore()
    hub = AgentRunHub()
    run_id = "run-ended-1"

    store.start_run(run_id, "sig-1", NOW)
    store.append_step(_step(run_id, 0, TraceStepKind.MODEL_TURN, text="investigating"))
    store.append_step(
        _step(run_id, 1, TraceStepKind.TOOL_CALL, tool="get_situation", arguments={"id": "s1"}, result_summary="ok", text=None)
    )
    store.append_step(
        _step(run_id, 2, TraceStepKind.OUTCOME, text=None, detail={"status": "succeeded", "proposal_id": "prop-1"})
    )
    store.finish_run(run_id, "succeeded", "prop-1", NOW)
    # The run is over — mark_ended is what tells gen() to stop after replay
    # instead of hanging on a queue that will never receive anything more.
    hub.mark_ended(run_id)

    c = _client(trace_store=store, hub=hub)
    with TestClient(c.app) as client:
        resp = client.get(f"/agent-runs/{run_id}/stream")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    body = resp.text
    assert ": connected" in body

    events = _parse_data_lines(body)
    stored_steps = store.steps(run_id)
    assert len(events) == len(stored_steps) == 3

    # first stored step's content appears verbatim
    assert events[0]["seq"] == 0
    assert events[0]["kind"] == "model_turn"
    assert events[0]["text"] == "investigating"

    # seqs arrive in order, matching the stored steps exactly (no drops/dupes)
    assert [e["seq"] for e in events] == [s.seq for s in stored_steps]
    assert events[-1]["kind"] == "outcome"
    assert events[-1]["detail"]["proposal_id"] == "prop-1"


def test_stream_unknown_run_replays_nothing_but_still_connects():
    # An unknown run_id that IS marked ended (edge case, but exercises the
    # "no stored steps" branch of the best-effort replay without hanging):
    # store.steps() returns [] for an unrecognized run_id (see
    # InMemoryTraceStore.steps), so replay yields zero data: lines, and the
    # ended check closes the stream immediately.
    store = InMemoryTraceStore()
    hub = AgentRunHub()
    hub.mark_ended("ghost-run")

    c = _client(trace_store=store, hub=hub)
    with TestClient(c.app) as client:
        resp = client.get("/agent-runs/ghost-run/stream")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert _parse_data_lines(resp.text) == []


def test_stream_degrades_to_no_stored_steps_on_store_exception():
    # Best-effort guard: a trace_store.steps() failure must never 500 the
    # stream — it degrades to "no stored steps" (mirrors the try/except
    # guards on GET /agent-runs and GET /agent-runs/{run_id}).
    class _RaisingTraceStore:
        def steps(self, run_id):
            raise RuntimeError("store unavailable")

    hub = AgentRunHub()
    hub.mark_ended("run-x")

    c = _client(trace_store=_RaisingTraceStore(), hub=hub)
    with TestClient(c.app) as client:
        resp = client.get("/agent-runs/run-x/stream")

    assert resp.status_code == 200
    assert _parse_data_lines(resp.text) == []


def test_stream_requires_token_when_auth_mode_is_token(monkeypatch):
    from common.config import Settings
    from services.governance import app as governance_app

    monkeypatch.setattr(
        governance_app, "get_settings", lambda: Settings(auth_mode="token", auth_token="secret")
    )
    store = InMemoryTraceStore()
    hub = AgentRunHub()
    hub.mark_ended("run-y")
    governance_app.app.state.trace_store = store
    governance_app.app.state.agent_run_hub = hub

    with TestClient(governance_app.app) as client:
        resp = client.get("/agent-runs/run-y/stream")
        assert resp.status_code == 401

        resp = client.get("/agent-runs/run-y/stream", params={"token": "wrong"})
        assert resp.status_code == 401

        resp = client.get("/agent-runs/run-y/stream", params={"token": "secret"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 3. Live path — drive the async generator directly (no blocking HTTP client)
# ---------------------------------------------------------------------------
#
# This is the test that actually proves the subscribe-before-replay + seq
# de-dup crux: a step is published on the hub in the gap between subscribing
# and reading the store (simulating the race the ordering is designed to
# close), and we assert it is delivered exactly once — never dropped, never
# duplicated — and that the terminal `outcome` step closes the stream.


class _NullRequest:
    query_params: ClassVar[dict] = {}


def test_live_path_delivers_step_published_between_subscribe_and_replay():
    from services.governance.app import app as governance_app_instance
    from services.governance.app import stream_agent_run

    run_id = "run-live-race"
    store = InMemoryTraceStore()
    hub = AgentRunHub()
    governance_app_instance.state.trace_store = store
    governance_app_instance.state.agent_run_hub = hub
    store.start_run(run_id, "sig-1", NOW)

    async def scenario():
        hub.bind_loop(asyncio.get_running_loop())

        # stream_agent_run is `async def stream_agent_run(run_id, request)`;
        # awaiting it runs the auth check and returns the StreamingResponse
        # without executing gen() yet — gen() only runs as its
        # body_iterator is pulled. Driving that iterator directly lets the
        # live path be tested deterministically instead of over a blocking
        # HTTP client.
        response = await stream_agent_run(run_id, _NullRequest())
        agen = response.body_iterator

        connected = await agen.__anext__()
        assert connected == ": connected\n\n"

        # By the time gen() has yielded ": connected\n\n", it has already
        # called hub.subscribe(run_id) (subscribe-before-replay ordering)
        # but has not yet called store.steps(). Simulate the race that
        # ordering exists to close: publish a step on the hub AND append it
        # to the store now (mirroring _run_draft_async's sink(), which
        # writes to both), so the still-pending store.steps() call sees it
        # too. The step must reach the client exactly once.
        step0 = _step(run_id, 0, TraceStepKind.MODEL_TURN, text="racing")
        hub.publish(run_id, step0)
        store.append_step(step0)
        await asyncio.sleep(0)  # let call_soon_threadsafe's callback run

        first_data = await agen.__anext__()
        assert first_data == f"data: {json.dumps(step0.model_dump(mode='json'))}\n\n"

        # A genuinely live step published after the replay is delivered too.
        step1 = _step(run_id, 1, TraceStepKind.TOOL_CALL, tool="get_situation", text=None)
        hub.publish(run_id, step1)
        await asyncio.sleep(0)
        second_data = await agen.__anext__()
        assert second_data == f"data: {json.dumps(step1.model_dump(mode='json'))}\n\n"

        # The terminal outcome step is delivered, then closes the stream.
        outcome = _step(
            run_id,
            2,
            TraceStepKind.OUTCOME,
            text=None,
            detail={"status": "succeeded", "proposal_id": "p1"},
        )
        hub.publish(run_id, outcome)
        await asyncio.sleep(0)
        third_data = await agen.__anext__()
        assert third_data == f"data: {json.dumps(outcome.model_dump(mode='json'))}\n\n"

        # gen() returns right after yielding the outcome step: the generator
        # is exhausted, proving the stream actually closes rather than
        # hanging on the queue forever.
        raised_stop = False
        try:
            await agen.__anext__()
        except StopAsyncIteration:
            raised_stop = True
        assert raised_stop

        # Exactly three data events were seen in total across this whole
        # scenario (step0, step1, outcome), each exactly once — step0 in
        # particular was visible to both the live queue (published) and the
        # once-store.steps() runs (appended), yet the seq de-dup ensured the
        # client only ever saw it via the replay path, never a second time
        # from the live loop. That is the crux this endpoint exists to get
        # right: no dropped step, no duplicated step.

    asyncio.run(scenario())
