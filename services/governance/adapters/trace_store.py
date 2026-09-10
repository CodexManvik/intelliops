"""TraceStore: the step-by-step record of the AI runbook author's activity.

One header row per run (`agent_runs`) plus an ordered log of steps
(`agent_run_steps`) — model turns, tool calls, the submit, and the final
outcome. InMemory (tests / non-postgres deploys) here; Postgres in the same
module (mirrors AuthorDecisionStore's layout).

Store methods MAY raise on write — the caller (TraceCollector's sink, wired in
a later task) wraps every write best-effort so a trace-store outage never
breaks drafting. `steps`/`recent_runs` are read paths guarded by the endpoints
that call them, not by this adapter."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from common.contracts import RunSummary, TraceStep
from common.db import agent_run_steps, agent_runs, from_payload, to_payload


class InMemoryTraceStore:
    def __init__(self) -> None:
        # run_id -> {"summary": RunSummary, "steps": list[TraceStep]}
        self._runs: dict[str, dict] = {}

    def start_run(self, run_id: str, signature: str, ts: datetime) -> None:
        self._runs[run_id] = {
            "summary": RunSummary(
                run_id=run_id,
                started_at=ts,
                status="running",
                signature=signature,
                step_count=0,
                proposal_id=None,
            ),
            "steps": [],
        }

    def append_step(self, step: TraceStep) -> None:
        entry = self._runs.get(step.run_id)
        if entry is None:
            return
        entry["steps"].append(step)
        entry["summary"] = entry["summary"].model_copy(
            update={"step_count": entry["summary"].step_count + 1}
        )

    def finish_run(
        self, run_id: str, status: str, proposal_id: str | None, ts: datetime
    ) -> None:
        entry = self._runs.get(run_id)
        if entry is None:
            return
        entry["summary"] = entry["summary"].model_copy(
            update={"status": status, "proposal_id": proposal_id}
        )

    def steps(self, run_id: str) -> list[TraceStep]:
        entry = self._runs.get(run_id)
        if entry is None:
            return []
        return sorted(entry["steps"], key=lambda s: s.seq)

    def recent_runs(self, limit: int = 50) -> list[RunSummary]:
        summaries = [entry["summary"] for entry in self._runs.values()]
        summaries.sort(key=lambda s: s.started_at, reverse=True)
        return summaries[:limit]


class PostgresTraceStore:
    """TraceStore backed by Postgres. Write methods propagate errors to the
    caller (the caller wraps every write best-effort — see TraceCollector).

    step_count is a promoted counter on the `agent_runs` header, bumped by
    `append_step` in the same transaction as the step insert (never a separate
    statement that could disagree with the actual step rows)."""

    def __init__(self, engine) -> None:
        self._engine = engine

    def start_run(self, run_id: str, signature: str, ts: datetime) -> None:
        summary = RunSummary(
            run_id=run_id,
            started_at=ts,
            status="running",
            signature=signature,
            step_count=0,
            proposal_id=None,
        )
        with self._engine.begin() as conn:
            conn.execute(
                agent_runs.insert().values(
                    run_id=run_id,
                    signature=signature,
                    status="running",
                    proposal_id=None,
                    started_at=ts,
                    finished_at=None,
                    step_count=0,
                    payload=to_payload(summary),
                )
            )

    def append_step(self, step: TraceStep) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                agent_run_steps.insert().values(
                    run_id=step.run_id,
                    seq=step.seq,
                    kind=step.kind.value,
                    ts=step.ts,
                    payload=to_payload(step),
                )
            )
            row = conn.execute(
                select(agent_runs.c.id, agent_runs.c.step_count, agent_runs.c.payload).where(
                    agent_runs.c.run_id == step.run_id
                )
            ).one_or_none()
            if row is None:
                return
            new_count = row.step_count + 1
            summary = from_payload(row.payload, RunSummary).model_copy(
                update={"step_count": new_count}
            )
            conn.execute(
                agent_runs.update()
                .where(agent_runs.c.id == row.id)
                .values(step_count=new_count, payload=to_payload(summary))
            )

    def finish_run(
        self, run_id: str, status: str, proposal_id: str | None, ts: datetime
    ) -> None:
        with self._engine.begin() as conn:
            row = conn.execute(
                select(agent_runs.c.id, agent_runs.c.payload).where(
                    agent_runs.c.run_id == run_id
                )
            ).one_or_none()
            if row is None:
                return
            summary = from_payload(row.payload, RunSummary).model_copy(
                update={"status": status, "proposal_id": proposal_id}
            )
            conn.execute(
                agent_runs.update()
                .where(agent_runs.c.id == row.id)
                .values(
                    status=status,
                    proposal_id=proposal_id,
                    finished_at=ts,
                    payload=to_payload(summary),
                )
            )

    def steps(self, run_id: str) -> list[TraceStep]:
        stmt = (
            select(agent_run_steps.c.payload)
            .where(agent_run_steps.c.run_id == run_id)
            .order_by(agent_run_steps.c.seq)
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [from_payload(row.payload, TraceStep) for row in rows]

    def recent_runs(self, limit: int = 50) -> list[RunSummary]:
        stmt = (
            select(agent_runs.c.payload)
            .order_by(agent_runs.c.started_at.desc())
            .limit(limit)
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [from_payload(row.payload, RunSummary) for row in rows]
