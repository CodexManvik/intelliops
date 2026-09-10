"""Postgres agent-run-trace-store adapter tests.

The AI runbook author's step-by-step activity trace must survive a governance
restart. These exercise the Postgres adapter against a real throwaway Postgres
(the `postgres` marker + `clean_db` fixture) with the same shape as
`tests/test_postgres_author_decisions.py`.
"""

from datetime import UTC, datetime, timedelta

import pytest

from common.contracts import TraceStep, TraceStepKind
from services.governance.adapters.trace_store import PostgresTraceStore

NOW = datetime(2026, 9, 10, tzinfo=UTC)


def _step(run_id="run-1", seq=0, **kw):
    base = {"run_id": run_id, "seq": seq, "kind": TraceStepKind.MODEL_TURN, "ts": NOW}
    base.update(kw)
    return TraceStep(**base)


@pytest.mark.postgres
def test_start_run_and_recent_runs(clean_db):
    s = PostgresTraceStore(clean_db)
    s.start_run("run-1", "sig-x", NOW)
    recent = s.recent_runs()
    assert len(recent) == 1
    assert recent[0].run_id == "run-1"
    assert recent[0].signature == "sig-x"
    assert recent[0].status == "running"
    assert recent[0].step_count == 0
    assert recent[0].proposal_id is None


@pytest.mark.postgres
def test_append_step_orders_by_seq_and_bumps_step_count(clean_db):
    s = PostgresTraceStore(clean_db)
    s.start_run("run-1", "sig-x", NOW)
    s.append_step(_step(seq=0, text="thinking about disk io"))
    s.append_step(
        _step(
            seq=1,
            kind=TraceStepKind.TOOL_CALL,
            tool="get_past_outcomes",
            arguments={"signature": "sig-x"},
            result_summary="restart 4/5",
        )
    )
    s.append_step(_step(seq=2, kind=TraceStepKind.SUBMIT, detail={"name": "Fix"}))

    got = s.steps("run-1")
    assert [st.seq for st in got] == [0, 1, 2]
    assert got[1].tool == "get_past_outcomes" and got[1].arguments == {"signature": "sig-x"}

    recent = s.recent_runs()
    assert recent[0].step_count == 3


@pytest.mark.postgres
def test_finish_run_updates_status_and_proposal_id(clean_db):
    s = PostgresTraceStore(clean_db)
    s.start_run("run-1", "sig-x", NOW)
    s.append_step(_step(seq=0))
    s.finish_run("run-1", "succeeded", "prop-1", NOW)

    recent = s.recent_runs()
    assert recent[0].status == "succeeded"
    assert recent[0].proposal_id == "prop-1"
    # step_count survives the finish update (read-modify-write, not overwrite)
    assert recent[0].step_count == 1


@pytest.mark.postgres
def test_recent_runs_orders_by_started_at_desc_and_respects_limit(clean_db):
    s = PostgresTraceStore(clean_db)
    s.start_run("run-1", "sig-x", NOW)
    s.start_run("run-2", "sig-y", NOW + timedelta(seconds=1))
    s.start_run("run-3", "sig-z", NOW + timedelta(seconds=2))

    recent = s.recent_runs(limit=2)
    assert [r.run_id for r in recent] == ["run-3", "run-2"]


@pytest.mark.postgres
def test_steps_empty_for_unknown_run(clean_db):
    s = PostgresTraceStore(clean_db)
    assert s.steps("nope") == []


@pytest.mark.postgres
def test_jsonb_payload_roundtrip(clean_db):
    # The full TraceStep is stored as a JSONB payload and reconstructed via
    # from_payload — every field must survive the round-trip byte-for-byte.
    s = PostgresTraceStore(clean_db)
    s.start_run("run-json", "sig-json", NOW)
    original = _step(
        run_id="run-json",
        seq=0,
        kind=TraceStepKind.TOOL_CALL,
        tool="get_system_context",
        arguments={"k": "v"},
        result_summary="ok",
    )
    s.append_step(original)
    assert s.steps("run-json")[0] == original
