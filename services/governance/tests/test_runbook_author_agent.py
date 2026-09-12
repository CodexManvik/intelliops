"""Tests for RunbookAuthorAgent: the bounded tool-calling drafting loop.

Uses a fake OpenAI-chat-shaped client (_FakeChatClient) that returns queued
responses — each either a tool-calls message or a plain-content message — and
a FakeToolbox (constructed by a toolbox_factory, mirroring AuthorToolbox's
shape) that records dispatch() calls and returns canned dicts. The real
Playbook/RemediationStep types are used for validation (never faked) so the
closed-action safety gate is exercised for real.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from common.contracts import HitlMode, Playbook, Situation, SituationStatus
from services.governance.adapters.runbook_author import RunbookAuthorAgent
from services.governance.adapters.trace_collector import TraceCollector


def _situation():
    now = datetime.now(UTC)
    return Situation(
        id="sit-1",
        status=SituationStatus.DIAGNOSED,
        severity="high",
        first_seen=now,
        last_seen=now,
        signature="sig-1",
    )


_VALID_PLAYBOOK = {
    "name": "Drafted restart",
    "match_rule": "*",
    "steps": [{"action": "restart"}],
    "hitl_mode": "hitl",
    "reversible": True,
}


def _submit_call(call_id, playbook=None, rationale="because", cited_facts=None):
    """Build one OpenAI-shaped tool_call dict for submit_runbook."""
    arguments = {
        "playbook": playbook if playbook is not None else _VALID_PLAYBOOK,
        "rationale": rationale,
        "cited_facts": cited_facts if cited_facts is not None else ["fact-1"],
    }
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "submit_runbook", "arguments": json.dumps(arguments)},
    }


def _read_tool_call(call_id, name, arguments=None):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments or {})},
    }


def _tool_calls_response(calls):
    """An OpenAI-shaped chat/completions 200 body whose message has tool_calls."""
    return _FakeResp(200, {"choices": [{"message": {"content": None, "tool_calls": calls}}]})


def _content_response(text):
    """An OpenAI-shaped chat/completions 200 body with plain content, no tool calls."""
    return _FakeResp(200, {"choices": [{"message": {"content": text}}]})


class _FakeResp:
    def __init__(self, status_code=200, body=None, headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    def json(self):
        return self._body


class _FakeChatClient:
    """Returns queued responses, one per `.post()` call (clamped once exhausted)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def post(self, *a, **k):
        self.calls += 1
        idx = min(self.calls - 1, len(self._responses) - 1)
        return self._responses[idx]


class FakeToolbox:
    """Mirrors AuthorToolbox's dispatch(name, arguments) -> dict shape.

    Records every call (name, arguments) and returns a canned dict per tool
    name (default {"ok": True} for unconfigured names), so tests can assert
    which read tools the model actually invoked.
    """

    def __init__(self, situation, canned=None):
        self.situation = situation
        self._canned = dict(canned or {})
        self.calls: list[tuple[str, dict]] = []

    def dispatch(self, name, arguments):
        self.calls.append((name, dict(arguments or {})))
        return self._canned.get(name, {"ok": True})


def _agent(client, toolbox_factory=None, **kwargs):
    factory = toolbox_factory or (lambda situation: FakeToolbox(situation))
    return RunbookAuthorAgent(
        "http://x", "m", http_client=client, toolbox_factory=factory, **kwargs
    )


# ---------------------------------------------------------------------------
# 1. Happy path: a read tool, then submit_runbook.
# ---------------------------------------------------------------------------


def test_happy_path_reads_then_submits():
    boxes: list[FakeToolbox] = []

    def factory(situation):
        box = FakeToolbox(situation)
        boxes.append(box)
        return box

    client = _FakeChatClient(
        [
            _tool_calls_response(
                [_read_tool_call("call-1", "get_past_decisions", {"signature": "sig-1"})]
            ),
            _tool_calls_response([_submit_call("call-2")]),
        ]
    )
    agent = _agent(client, toolbox_factory=factory)
    result = agent.draft(_situation(), hint="check past decisions")

    assert result is not None
    playbook, rationale, cited_facts = result
    assert isinstance(playbook, Playbook)
    assert playbook.steps[0].action == "restart"
    assert playbook.hitl_mode == HitlMode.HITL
    assert rationale == "because"
    assert cited_facts == ["fact-1"]

    # the toolbox recorded the read call the model made before submitting
    assert len(boxes) == 1
    assert boxes[0].calls == [("get_past_decisions", {"signature": "sig-1"})]


# ---------------------------------------------------------------------------
# 2. Budget: the model never submits -> None after max_rounds.
# ---------------------------------------------------------------------------


def test_never_submits_exhausts_round_budget_returns_none():
    # every round the model just calls a read tool again, never submit_runbook
    responses = [
        _tool_calls_response([_read_tool_call(f"call-{i}", "get_system_context")])
        for i in range(10)
    ]
    client = _FakeChatClient(responses)
    agent = _agent(client, max_rounds=4)
    result = agent.draft(_situation())

    assert result is None
    # exactly max_rounds model calls were made — the budget is enforced, not
    # merely "eventually stops"
    assert client.calls == 4


# ---------------------------------------------------------------------------
# 3. Invalid submit (out-of-catalog action) -> validation gate rejects -> None,
#    but a corrective round is attempted first.
# ---------------------------------------------------------------------------


def test_invalid_submit_triggers_corrective_round_then_none():
    # The model keeps submitting the same out-of-catalog action every round
    # (the fake client clamps to the last queued response once exhausted, so
    # every remaining round replays this same invalid submit) — it should
    # stay rejected forever, never slip through, and the loop runs the full
    # round budget looking for a valid resubmit before giving up.
    bad_playbook = {**_VALID_PLAYBOOK, "steps": [{"action": "delete"}]}
    client = _FakeChatClient(
        [_tool_calls_response([_submit_call("call-1", playbook=bad_playbook)])]
    )
    agent = _agent(client, max_rounds=4)
    result = agent.draft(_situation())

    assert result is None
    # a corrective round happened each time: the model was called across the
    # full round budget, not stopped dead after the first invalid submit
    assert client.calls == 4


def test_invalid_submit_then_valid_resubmit_succeeds():
    bad_playbook = {**_VALID_PLAYBOOK, "steps": [{"action": "delete"}]}
    client = _FakeChatClient(
        [
            _tool_calls_response([_submit_call("call-1", playbook=bad_playbook)]),
            _tool_calls_response([_submit_call("call-2", playbook=_VALID_PLAYBOOK)]),
        ]
    )
    agent = _agent(client, max_rounds=6)
    result = agent.draft(_situation())

    assert result is not None
    playbook, _rationale, _facts = result
    assert playbook.steps[0].action == "restart"


# ---------------------------------------------------------------------------
# 4. submit with no id -> still validates (placeholder injection carried from #47).
# ---------------------------------------------------------------------------


def test_submit_without_id_still_validates():
    assert "id" not in _VALID_PLAYBOOK
    client = _FakeChatClient([_tool_calls_response([_submit_call("call-1")])])
    agent = _agent(client)
    result = agent.draft(_situation())

    assert result is not None
    playbook, _rationale, _facts = result
    assert playbook.id  # a placeholder was injected; some id is present
    assert playbook.steps[0].action == "restart"


def test_submit_with_model_supplied_id_still_validates():
    draft = {**_VALID_PLAYBOOK, "id": "model-supplied"}
    client = _FakeChatClient([_tool_calls_response([_submit_call("call-1", playbook=draft)])])
    agent = _agent(client)
    result = agent.draft(_situation())

    assert result is not None


# ---------------------------------------------------------------------------
# 5. 429 on the first round then success -> honored backoff, still returns a draft.
# ---------------------------------------------------------------------------


def test_rate_limit_backs_off_then_continues(monkeypatch):
    slept = []
    monkeypatch.setattr(
        "services.governance.adapters.runbook_author.time.sleep", lambda s: slept.append(s)
    )
    rate_limited = _FakeResp(
        429, {"error": {"message": "Rate limit reached ... Please try again in 3.2s."}}
    )
    client = _FakeChatClient([rate_limited, _tool_calls_response([_submit_call("call-1")])])
    agent = _agent(client, max_attempts=3, max_rounds=6)
    result = agent.draft(_situation())

    assert result is not None
    assert slept == [3.2]
    assert client.calls == 2


def test_rate_limit_exhausting_attempts_within_a_round_returns_none(monkeypatch):
    # If every attempt within a single round's HTTP budget is 429'd, the round
    # (and thus the whole draft) is terminal — no infinite hammering.
    slept = []
    monkeypatch.setattr(
        "services.governance.adapters.runbook_author.time.sleep", lambda s: slept.append(s)
    )
    rate_limited = _FakeResp(429, {"error": {"message": "try again in 1s"}})
    client = _FakeChatClient([rate_limited, rate_limited, rate_limited])
    agent = _agent(client, max_attempts=3, max_rounds=6)
    result = agent.draft(_situation())

    assert result is None
    assert client.calls == 3
    # backed off between attempts 1->2 and 2->3, but not after the last
    assert slept == [1.0, 1.0]


# ---------------------------------------------------------------------------
# 6. A read-tool {"error": ...} result does not crash the loop; the model can
#    still submit afterward.
# ---------------------------------------------------------------------------


def test_read_tool_error_result_does_not_crash_loop():
    def factory(situation):
        return FakeToolbox(situation, canned={"get_past_outcomes": {"error": "TimeoutError"}})

    client = _FakeChatClient(
        [
            _tool_calls_response(
                [_read_tool_call("call-1", "get_past_outcomes", {"signature": "sig-1"})]
            ),
            _tool_calls_response([_submit_call("call-2")]),
        ]
    )
    agent = _agent(client, toolbox_factory=factory)
    result = agent.draft(_situation())

    assert result is not None
    playbook, _rationale, _facts = result
    assert playbook.steps[0].action == "restart"


def test_toolbox_dispatch_raising_does_not_crash_loop():
    # Belt-and-suspenders: even if a (mis-behaving) toolbox raises instead of
    # degrading to {"error": ...} itself, the agent loop must not propagate it.
    class RaisingToolbox:
        def dispatch(self, name, arguments):
            raise RuntimeError("boom")

    client = _FakeChatClient(
        [
            _tool_calls_response([_read_tool_call("call-1", "get_system_context")]),
            _tool_calls_response([_submit_call("call-2")]),
        ]
    )
    agent = _agent(client, toolbox_factory=lambda situation: RaisingToolbox())
    result = agent.draft(_situation())

    assert result is not None


# ---------------------------------------------------------------------------
# Additional coverage: plain-content nudge behavior, no-toolbox_factory, and
# "never raises" on a hard transport failure / malformed response.
# ---------------------------------------------------------------------------


def test_plain_content_is_nudged_then_can_submit():
    client = _FakeChatClient(
        [
            _content_response("Let me think about this incident..."),
            _tool_calls_response([_submit_call("call-1")]),
        ]
    )
    agent = _agent(client, max_rounds=6)
    result = agent.draft(_situation())

    assert result is not None
    # one round for the plain content, one round for the tool-call submit
    assert client.calls == 2


def test_plain_content_twice_exhausts_budget_returns_none():
    client = _FakeChatClient(
        [
            _content_response("thinking..."),
            _content_response("still thinking..."),
            _content_response("more thinking..."),
        ]
    )
    agent = _agent(client, max_rounds=3)
    result = agent.draft(_situation())

    assert result is None
    assert client.calls == 3


def test_transport_error_returns_none_never_raises():
    class _RaisingClient:
        def post(self, *a, **k):
            raise httpx.ConnectError("unreachable")

    agent = _agent(_RaisingClient())
    assert agent.draft(_situation()) is None


def test_missing_toolbox_factory_returns_none_never_raises():
    client = _FakeChatClient([_tool_calls_response([_submit_call("call-1")])])
    agent = RunbookAuthorAgent("http://x", "m", http_client=client, toolbox_factory=None)
    assert agent.draft(_situation()) is None
    assert client.calls == 0  # never even attempted a model call without a toolbox


def test_malformed_submit_arguments_json_triggers_corrective_round():
    bad_call = {
        "id": "call-1",
        "type": "function",
        "function": {"name": "submit_runbook", "arguments": "not json at all"},
    }
    client = _FakeChatClient(
        [
            _tool_calls_response([bad_call]),
            _tool_calls_response([_submit_call("call-2")]),
        ]
    )
    agent = _agent(client, max_rounds=6)
    result = agent.draft(_situation())

    assert result is not None


@pytest.mark.parametrize("bad_field", ["playbook", "cited_facts"])
def test_submit_with_wrong_types_triggers_corrective_round(bad_field):
    arguments = {
        "playbook": _VALID_PLAYBOOK,
        "rationale": "because",
        "cited_facts": ["fact-1"],
    }
    arguments[bad_field] = "not the right type"
    bad_call = {
        "id": "call-1",
        "type": "function",
        "function": {"name": "submit_runbook", "arguments": json.dumps(arguments)},
    }
    client = _FakeChatClient(
        [
            _tool_calls_response([bad_call]),
            _tool_calls_response([_submit_call("call-2")]),
        ]
    )
    agent = _agent(client, max_rounds=6)
    result = agent.draft(_situation())

    assert result is not None


# ---------------------------------------------------------------------------
# 7. Non-submit tool with non-string arguments (TypeError in json.loads) should
#    degrade gracefully to an error result, not crash the loop.
# ---------------------------------------------------------------------------


def test_read_tool_with_non_string_arguments_degrades_gracefully():
    # A client that passes already-parsed arguments (dict or int) instead of
    # a JSON string should not crash the loop. The arguments are treated as
    # empty {} and dispatch() is called (which returns {"ok": True} for
    # unconfigured tools). The model can then submit after.
    bad_call = {
        "id": "call-1",
        "type": "function",
        "function": {"name": "get_system_context", "arguments": {"already": "parsed"}},
    }
    client = _FakeChatClient(
        [
            _tool_calls_response([bad_call]),
            _tool_calls_response([_submit_call("call-2")]),
        ]
    )
    agent = _agent(client, max_rounds=6)
    result = agent.draft(_situation())

    assert result is not None
    playbook, _rationale, _facts = result
    assert playbook.steps[0].action == "restart"


def test_read_tool_with_int_arguments_degrades_gracefully():
    # Even an int (completely wrong type) should degrade to empty args without
    # crashing the loop.
    bad_call = {
        "id": "call-1",
        "type": "function",
        "function": {"name": "get_past_decisions", "arguments": 12345},
    }
    client = _FakeChatClient(
        [
            _tool_calls_response([bad_call]),
            _tool_calls_response([_submit_call("call-2")]),
        ]
    )
    agent = _agent(client, max_rounds=6)
    result = agent.draft(_situation())

    assert result is not None
    playbook, _rationale, _facts = result
    assert playbook.steps[0].action == "restart"


# ---------------------------------------------------------------------------
# 8. Trace threading (Task 3): an optional TraceCollector is recorded at hook
# points inside the loop, but NEVER fed back into `messages` and NEVER changes
# what draft() returns. The terminal `outcome` step is emitted by the CALLER
# of draft() (Task 4) — draft's own trace always ends at `submit` on success,
# or simply stops (no submit step) on budget exhaustion/failure.
# ---------------------------------------------------------------------------


def _trace():
    """A TraceCollector wired to a plain list sink, for assertions."""
    sink: list = []
    return TraceCollector("run-1", sink=sink.append), sink


def test_trace_records_model_turn_tool_call_then_submit_in_order():
    client = _FakeChatClient(
        [
            _tool_calls_response(
                [_read_tool_call("call-1", "get_past_decisions", {"signature": "sig-1"})]
            ),
            _tool_calls_response([_submit_call("call-2")]),
        ]
    )
    # give the first response some assistant content alongside the tool call,
    # so we can assert model_turn captured it
    client._responses[0]._body["choices"][0]["message"]["content"] = "checking history first"

    agent = _agent(client)
    trace, sink = _trace()
    result = agent.draft(_situation(), trace=trace)

    assert result is not None
    playbook, rationale, cited_facts = result

    kinds = [step.kind.value for step in sink]
    assert kinds == ["model_turn", "tool_call", "submit"]

    model_turn_step = sink[0]
    assert model_turn_step.text == "checking history first"

    tool_call_step = sink[1]
    assert tool_call_step.tool == "get_past_decisions"
    assert tool_call_step.arguments == {"signature": "sig-1"}
    assert isinstance(tool_call_step.result_summary, str)
    assert tool_call_step.result_summary  # non-empty: FakeToolbox returns {"ok": True}

    submit_step = sink[2]
    assert submit_step.detail["name"] == playbook.name
    assert submit_step.detail["actions"] == [s.action for s in playbook.steps]
    assert submit_step.detail["rationale"] == rationale
    assert submit_step.detail["cited_facts"] == cited_facts

    # draft() never emits the terminal outcome step itself — that's the
    # caller's job (Task 4)
    assert "outcome" not in kinds


def test_trace_budget_exhaustion_records_turns_but_no_submit():
    responses = [
        _tool_calls_response([_read_tool_call(f"call-{i}", "get_system_context")])
        for i in range(10)
    ]
    client = _FakeChatClient(responses)
    agent = _agent(client, max_rounds=4)
    trace, sink = _trace()
    result = agent.draft(_situation(), trace=trace)

    assert result is None
    kinds = [step.kind.value for step in sink]
    assert "submit" not in kinds
    assert "outcome" not in kinds
    # one tool_call per round (no assistant content in these fixtures, so no
    # model_turn steps) — 4 rounds, 4 tool calls
    assert kinds.count("tool_call") == 4


def test_trace_none_default_is_byte_identical_to_no_trace():
    # Reuses the happy-path shape from test_happy_path_reads_then_submits:
    # trace omitted entirely (the default) must yield the exact same draft as
    # today, with no behavior change from adding the parameter.
    client = _FakeChatClient(
        [
            _tool_calls_response(
                [_read_tool_call("call-1", "get_past_decisions", {"signature": "sig-1"})]
            ),
            _tool_calls_response([_submit_call("call-2")]),
        ]
    )
    agent = _agent(client)
    result = agent.draft(_situation(), hint="check past decisions")

    assert result is not None
    playbook, rationale, cited_facts = result
    assert isinstance(playbook, Playbook)
    assert playbook.steps[0].action == "restart"
    assert playbook.hitl_mode == HitlMode.HITL
    assert rationale == "because"
    assert cited_facts == ["fact-1"]


def test_trace_error_result_still_traced_and_loop_continues_to_submit():
    def factory(situation):
        return FakeToolbox(situation, canned={"get_past_outcomes": {"error": "TimeoutError"}})

    client = _FakeChatClient(
        [
            _tool_calls_response(
                [_read_tool_call("call-1", "get_past_outcomes", {"signature": "sig-1"})]
            ),
            _tool_calls_response([_submit_call("call-2")]),
        ]
    )
    agent = _agent(client, toolbox_factory=factory)
    trace, sink = _trace()
    result = agent.draft(_situation(), trace=trace)

    assert result is not None
    tool_call_steps = [s for s in sink if s.kind.value == "tool_call"]
    assert len(tool_call_steps) == 1
    assert tool_call_steps[0].tool == "get_past_outcomes"
    assert tool_call_steps[0].result_summary == "error: TimeoutError"
    assert any(s.kind.value == "submit" for s in sink)
