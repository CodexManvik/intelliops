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
