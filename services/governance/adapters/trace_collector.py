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
