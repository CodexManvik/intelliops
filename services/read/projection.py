"""In-memory read-model: a projection of the event stream for the dashboard.

Rebuildable from Redis Streams on every start (the events are the source of
truth), so this holds no durable state of its own. It maps backend contracts to
the exact shapes frontend/src/data/types.ts expects, so the UI needs no
translation layer.
"""

from __future__ import annotations

import asyncio
import threading
from typing import ClassVar

from common.contracts import (
    DiagnosedSituation,
    RemediationOutcome,
    RemediationResult,
    Situation,
    SituationStatus,
)

_RESULT_STATUS = {
    RemediationResult.SUCCESS: "resolved",
    RemediationResult.FAILURE: "failed",
    RemediationResult.ROLLED_BACK: "failed",
}

_SEVERITY_MAP = {"critical": "critical", "high": "high", "medium": "medium", "low": "low"}


def _epoch_ms(dt) -> int:
    return int(dt.timestamp() * 1000)


class ReadModel:
    def __init__(
        self, max_outcomes: int = 200, ttl_seconds: float = 600.0, max_situations: int = 50
    ) -> None:
    def __init__(
        self, max_outcomes: int = 200, ttl_seconds: float = 600.0, max_situations: int = 50
    ) -> None:
        self._sits: dict[str, dict] = {}
        self._outcomes: list[dict] = []
        self._max = max_outcomes
        self._ttl_ms = ttl_seconds * 1000
        self._max_sits = max_situations
        self._suppressed_count = 0
        self._subscribers: set[asyncio.Queue] = set()
        self._subs_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

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

    def apply_detected(self, s: Situation) -> None:
        existing = self._sits.get(s.id, {})
        self._sits[s.id] = {
            **existing,
            "id": s.id,
            "signature": s.signature,
            "service": self._service_of(s),
            "title": s.signature,
            "status": s.status.value if isinstance(s.status, SituationStatus) else str(s.status),
            "severity": _SEVERITY_MAP.get(s.severity, "medium"),
            "memberCount": len(s.member_events),
            "first_seen": _epoch_ms(s.first_seen),
            "hypotheses": existing.get("hypotheses", []),
            "suggested_runbook_id": existing.get("suggested_runbook_id"),
            "hitl_mode": existing.get("hitl_mode", "hitl"),
            "reversible": existing.get("reversible", True),
            "reliability": existing.get("reliability", 0.0),
            "suppressed": False,
            "last_activity": existing.get("last_activity", _epoch_ms(s.first_seen)),
        }
        self.publish({"type": "changed"})

    def apply_suppressed(self, s: Situation) -> None:
        self._suppressed_count += 1
        self.publish({"type": "changed"})

    def apply_diagnosed(self, d: DiagnosedSituation) -> None:
        self.apply_detected(d.situation)
        self._sits[d.situation.id].update(
            {
                "status": "diagnosed",
                "hypotheses": [
                    {
                        "description": h.description,
                        "confidence": h.confidence,
                        "suggested_runbook_id": h.suggested_runbook_id,
                    }
                    for h in d.hypotheses
                ],
                "suggested_runbook_id": d.suggested_runbook_id,
            }
        )

    def apply_outcome(self, o: RemediationOutcome) -> None:
        if o.situation_id in self._sits:
            self._sits[o.situation_id]["status"] = _RESULT_STATUS.get(o.result, "failed")
            self._sits[o.situation_id]["last_activity"] = _epoch_ms(o.ts)
        result = o.result.value if isinstance(o.result, RemediationResult) else str(o.result)
        sit = self._sits.get(o.situation_id, {})
        mttr_ms = None
        if sit and o.result == RemediationResult.SUCCESS:
            mttr_ms = _epoch_ms(o.ts) - sit["first_seen"]
        self._outcomes.insert(
            0,
            {
                "situation_id": o.situation_id,
                "playbook_id": o.playbook_id,
                "result": result,
                "reason": o.health_after,
                "ts": _epoch_ms(o.ts),
                "service": sit.get("service", "unknown"),
                "hitl_mode": o.hitl_mode.value
                if hasattr(o.hitl_mode, "value")
                else str(o.hitl_mode),
                "mttr_ms": mttr_ms,
            },
        )
        del self._outcomes[self._max :]

    _TERMINAL: ClassVar[set[str]] = {"resolved", "failed"}

    def _age_out(self, now_ms: int) -> None:
        # age-out terminal situations older than ttl (needs a clock)
        for sid in list(self._sits):
            s = self._sits[sid]
            if s["status"] in self._TERMINAL and now_ms - s.get("last_activity", 0) > self._ttl_ms:
                del self._sits[sid]

    def _enforce_cap(self) -> None:
        # cap: if over max, evict oldest-terminal-first (never active). Pure
        # relative ordering by stored last_activity, so no clock is needed.
        if len(self._sits) > self._max_sits:
            terminal = sorted(
                (s for s in self._sits.values() if s["status"] in self._TERMINAL),
                key=lambda s: s.get("last_activity", 0),
            )
            n_to_drop = len(self._sits) - self._max_sits
            for s in terminal[:n_to_drop]:
                del self._sits[s["id"]]

    def _prune(self, now_ms: int) -> None:
        self._age_out(now_ms)
        self._enforce_cap()

    def situations(self, now_ms: int | None = None) -> list[dict]:
        if now_ms is not None:
            self._prune(now_ms)
        else:
            self._enforce_cap()
        return list(self._sits.values())

    def outcomes(self) -> list[dict]:
        return list(self._outcomes)

    def reset(self) -> None:
        self._sits.clear()
        self._outcomes.clear()
        self._suppressed_count = 0

    _OPEN: ClassVar[set[str]] = {"detected", "diagnosed", "acting"}

    def metrics(self) -> dict:
        sits = list(self._sits.values())
        outs = self._outcomes
        total_out = len(outs)
        successes = sum(1 for o in outs if o["result"] == "success")
        autos = sum(1 for o in outs if o.get("hitl_mode") == "auto")
        mttrs = [o["mttr_ms"] for o in outs if o.get("mttr_ms") is not None]
        alerts = sum(s["memberCount"] for s in sits)
        n_sits = len(sits)
        open_sits = [s for s in sits if s["status"] in self._OPEN]
        pending = [
            s
            for s in open_sits
            if s.get("hitl_mode") == "hitl" and s["status"] in ("diagnosed", "acting")
        ]
        pending = [
            s
            for s in open_sits
            if s.get("hitl_mode") == "hitl" and s["status"] in ("diagnosed", "acting")
        ]
        noise = ((1 - n_sits / alerts) * 100) if alerts else 0.0
        return {
            "alertsIngested": alerts,
            "situationsOpen": len(open_sits),
            "noiseReductionPct": round(max(0.0, noise), 1),
            "mttrMinutes": round((sum(mttrs) / len(mttrs) / 60000), 2) if mttrs else 0.0,
            "autoRemediatedPct": round(autos / total_out * 100, 1) if total_out else 0.0,
            "suppressedToday": self._suppressed_count,
            "approvalsPending": len(pending),
            "successRate": round(successes / total_out, 3) if total_out else 0.0,
        }

    @staticmethod
    def _service_of(s: Situation) -> str:
        for ev in s.member_events:
            for key in ("service", "job", "instance"):
                val = ev.labels.get(key)
                if val:
                    return val
        return "unknown"
