from datetime import UTC, datetime

from common.contracts import TraceStep, TraceStepKind
from common.db import agent_run_steps, agent_runs
from services.governance.adapters.trace_store import InMemoryTraceStore

NOW = datetime(2026, 9, 10, tzinfo=UTC)


def test_agent_runs_table_shape():
    assert agent_runs.name == "agent_runs"
    columns = {c.name for c in agent_runs.columns}
    assert columns == {
        "id",
        "run_id",
        "signature",
        "status",
        "proposal_id",
        "started_at",
        "finished_at",
        "step_count",
        "payload",
    }


def test_agent_run_steps_table_shape():
    assert agent_run_steps.name == "agent_run_steps"
    columns = {c.name for c in agent_run_steps.columns}
    assert columns == {"id", "run_id", "seq", "kind", "ts", "payload"}


def _step(run_id, seq, **kw):
    base = {"run_id": run_id, "seq": seq, "kind": TraceStepKind.MODEL_TURN, "ts": NOW}
    base.update(kw)
    return TraceStep(**base)


def test_inmemory_round_trip_orders_steps_and_reports_recent_runs():
    s = InMemoryTraceStore()
    s.start_run("run-1", "sig-x", NOW)
    s.append_step(_step("run-1", 0, text="thinking"))
    s.append_step(
        _step(
            "run-1",
            1,
            kind=TraceStepKind.TOOL_CALL,
            tool="get_past_outcomes",
            arguments={"signature": "sig-x"},
            result_summary="restart 4/5",
        )
    )
    s.append_step(_step("run-1", 2, kind=TraceStepKind.SUBMIT, detail={"name": "Fix"}))

    got = s.steps("run-1")
    assert [st.seq for st in got] == [0, 1, 2]
    assert got[1].tool == "get_past_outcomes"

    recent = s.recent_runs()
    assert len(recent) == 1
    assert recent[0].run_id == "run-1"
    assert recent[0].step_count == 3
    assert recent[0].status == "running"
    assert recent[0].signature == "sig-x"


def test_inmemory_finish_run_updates_status_and_proposal_id():
    s = InMemoryTraceStore()
    s.start_run("run-1", "sig-x", NOW)
    s.append_step(_step("run-1", 0))
    s.finish_run("run-1", "succeeded", "prop-1", NOW)

    recent = s.recent_runs()
    assert recent[0].status == "succeeded"
    assert recent[0].proposal_id == "prop-1"
    # step_count from append is preserved across finish
    assert recent[0].step_count == 1


def test_inmemory_recent_runs_orders_by_started_at_desc_and_respects_limit():
    from datetime import timedelta

    s = InMemoryTraceStore()
    s.start_run("run-1", "sig-x", NOW)
    s.start_run("run-2", "sig-y", NOW + timedelta(seconds=1))
    s.start_run("run-3", "sig-z", NOW + timedelta(seconds=2))

    recent = s.recent_runs(limit=2)
    assert [r.run_id for r in recent] == ["run-3", "run-2"]


def test_inmemory_steps_and_finish_are_noop_for_missing_run():
    s = InMemoryTraceStore()
    assert s.steps("nope") == []
    s.finish_run("nope", "failed", None, NOW)  # must not raise
    s.append_step(_step("nope", 0))  # must not raise
    assert s.recent_runs() == []
