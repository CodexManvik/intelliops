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

# The projection's own status vocabulary: a deliberate superset of the backend
# SituationStatus enum, because the console needs a card colour for outcomes the
# backend state machine has no state for. Any RemediationResult missing here
# silently falls back to "failed" in apply_outcome, so a new member MUST be added.
_RESULT_STATUS = {
    RemediationResult.SUCCESS: "resolved",
    RemediationResult.FAILURE: "failed",
    RemediationResult.ROLLED_BACK: "failed",
    RemediationResult.ESCALATED: "needs_attention",
}

_SEVERITY_MAP = {"critical": "critical", "high": "high", "medium": "medium", "low": "low"}


def _epoch_ms(dt) -> int:
    return int(dt.timestamp() * 1000)


_MAX_MEMBER_EVENTS = 20


def _project_events(s: Situation) -> list[dict]:
    out: list[dict] = []
    for ev in s.member_events[:_MAX_MEMBER_EVENTS]:
        out.append(
            {
                "name": ev.name,
                "value": ev.value,
                "labels": dict(ev.labels),
                "kind": ev.kind.value if hasattr(ev.kind, "value") else str(ev.kind),
                "ts": _epoch_ms(ev.ts),
            }
        )
    return out


class ReadModel:
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
        # Guards _sits/_outcomes/_suppressed_count. Four consumer threads (one per
        # topic) write them while request threads read and prune them; two
        # concurrent /situations calls could both try to delete the same aged-out
        # entry, and iterating a dict another thread is resizing raises.
        self._lock = threading.RLock()

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
        with self._lock:
            applied = self._apply_detected(s)
        if applied:
            self.publish({"type": "changed"})

    def _apply_detected(self, s: Situation) -> bool:
        """Fold a detection in. False when the event is stale and was ignored.

        A Situation's id is its signature, so the same incident recurring later
        arrives under the same id. first_seen tells the occurrences apart:

        - older than what we hold: a late event from an earlier occurrence (the
          topics are consumed on separate threads, and a rebuild replays them
          topic by topic). Applying it would roll the card back in time.
        - newer, and the held one is finished: a new occurrence. Start clean,
          or the new card inherits the previous run's outcome and timeline.
        - equal: the same occurrence seen again (e.g. via its diagnosis). Never
          move its status backwards - a detected event that lost the race to its
          own outcome must not reopen a resolved card.
        """
        first_ms = _epoch_ms(s.first_seen)
        existing = self._sits.get(s.id, {})
        if existing:
            held_first = existing.get("first_seen", first_ms)
            if first_ms < held_first:
                return False
            if first_ms > held_first and existing.get("status") in self._EVICTABLE:
                existing = {}
        status = s.status.value if isinstance(s.status, SituationStatus) else str(s.status)
        if existing.get("first_seen") == first_ms and existing.get("status") not in (
            None,
            "detected",
        ):
            status = existing["status"]
        self._sits[s.id] = {
            **existing,
            "id": s.id,
            "signature": s.signature,
            "service": self._service_of(s),
            "title": s.signature,
            "status": status,
            "severity": _SEVERITY_MAP.get(s.severity, "medium"),
            "memberCount": len(s.member_events),
            "member_events": _project_events(s),
            "peak_score": s.peak_score,
            "baseline": s.baseline,
            "first_seen": _epoch_ms(s.first_seen),
            # Required by the Situation contract. The console posts a situation
            # back to governance for AI drafting, and without this that request
            # is rejected 422 - the projection was not round-trippable.
            "last_seen": _epoch_ms(s.last_seen),
            "hypotheses": existing.get("hypotheses", []),
            "suggested_runbook_id": existing.get("suggested_runbook_id"),
            "hitl_mode": existing.get("hitl_mode", "hitl"),
            "reversible": existing.get("reversible", True),
            "reliability": existing.get("reliability", 0.0),
            "suppressed": False,
            "last_activity": existing.get("last_activity", _epoch_ms(s.first_seen)),
            "stages": existing.get("stages", {}),
        }
        stages = self._sits[s.id].get("stages", {})
        stages.setdefault("detected", _epoch_ms(s.first_seen))
        self._sits[s.id]["stages"] = stages
        return True

    def apply_suppressed(self, s: Situation) -> None:
        with self._lock:
            self._suppressed_count += 1
        self.publish({"type": "changed"})

    def apply_diagnosed(self, d: DiagnosedSituation) -> None:
        with self._lock:
            applied = self._apply_diagnosed(d)
        if applied:
            self.publish({"type": "changed"})

    def _apply_diagnosed(self, d: DiagnosedSituation) -> bool:
        if not self._apply_detected(d.situation):
            return False
        hyps = [
            {
                "description": h.description,
                "confidence": h.confidence,
                "suggested_runbook_id": h.suggested_runbook_id,
                "evidence": list(h.evidence),
                "explanation": h.explanation,
                "explanation_source": h.explanation_source,
                "confidence_source": h.confidence_source,
            }
            for h in d.hypotheses
        ]
        service = self._sits[d.situation.id].get("service", "unknown")
        title = f"{hyps[0]['description']} · {service}" if hyps else d.situation.signature
        sit = self._sits[d.situation.id]
        stages = sit.get("stages", {})
        stages["diagnosed"] = _epoch_ms(d.situation.last_seen)
        sit.update(
            {
                "hypotheses": hyps,
                "suggested_runbook_id": d.suggested_runbook_id,
                "title": title,
                "stages": stages,
            }
        )
        # Same no-going-backwards rule as _apply_detected: a diagnosis that
        # arrives after its outcome must not reopen the card.
        if sit.get("status") in (None, "detected"):
            sit["status"] = "diagnosed"
        return True

    def apply_outcome(self, o: RemediationOutcome) -> None:
        with self._lock:
            self._apply_outcome(o)
        self.publish({"type": "changed"})

    def _apply_outcome(self, o: RemediationOutcome) -> None:
        if o.situation_id in self._sits:
            self._sits[o.situation_id]["status"] = _RESULT_STATUS.get(o.result, "failed")
            self._sits[o.situation_id]["last_activity"] = _epoch_ms(o.ts)
            terminal = _RESULT_STATUS.get(o.result, "failed")
            stages = self._sits[o.situation_id].get("stages", {})
            stages[terminal] = _epoch_ms(o.ts)
            self._sits[o.situation_id]["stages"] = stages
            self._sits[o.situation_id]["outcome"] = {
                "result": o.result.value
                if isinstance(o.result, RemediationResult)
                else str(o.result),
                "health_after": o.health_after,
                "mode": getattr(o, "mode", "dry_run"),
                "steps": list(getattr(o, "steps", [])),
                "preflight": (
                    p.model_dump() if (p := getattr(o, "preflight", None)) is not None else None
                ),
            }
        result = o.result.value if isinstance(o.result, RemediationResult) else str(o.result)
        sit = self._sits.get(o.situation_id, {})
        mttr_ms = None
        if sit and o.result == RemediationResult.SUCCESS:
            elapsed = _epoch_ms(o.ts) - sit["first_seen"]
            # A Situation's id IS its signature, so a recurrence of the same
            # incident overwrites the earlier record's `first_seen`. Replaying
            # the stream on a read-model rebuild then pairs an OLD outcome with
            # a NEW first_seen and the subtraction goes negative -- the console's
            # front page read "MEAN TIME TO RESOLVE -9.54 min". A negative
            # elapsed time is not a slow fix, it is the absence of a matching
            # detection, so report no measurement rather than a nonsense one.
            mttr_ms = elapsed if elapsed >= 0 else None
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
                # Whether a real cluster was touched. The nested per-situation
                # outcome already carried this, but the flat /outcomes feed - the
                # one the console's history renders - dropped it, so "we really
                # restarted a pod" and "we simulated it" were indistinguishable
                # downstream. It is the whole point of the k8s posture.
                "mode": getattr(o, "mode", "dry_run"),
                "mttr_ms": mttr_ms,
            },
        )
        del self._outcomes[self._max :]

    _TERMINAL: ClassVar[set[str]] = {"resolved", "failed"}

    # "needs_attention" is deliberately NOT terminal — the one incident that most
    # needs a human must never vanish from the console on a TTL. It is still
    # evictable, otherwise a fault storm matching no playbook would grow _sits
    # past max_situations without bound.
    _EVICTABLE: ClassVar[set[str]] = {"resolved", "failed", "needs_attention"}

    def _age_out(self, now_ms: int) -> None:
        # age-out terminal situations older than ttl (needs a clock)
        for sid in list(self._sits):
            s = self._sits[sid]
            if s["status"] in self._TERMINAL and now_ms - s.get("last_activity", 0) > self._ttl_ms:
                del self._sits[sid]

    def _enforce_cap(self) -> None:
        # cap: if over max, evict oldest-terminal-first (never active). Pure
        # relative ordering by stored last_activity, so no clock is needed.
        # needs_attention sorts last so it is only sacrificed once no genuinely
        # finished situation is left to drop.
        if len(self._sits) > self._max_sits:
            evictable = sorted(
                (s for s in self._sits.values() if s["status"] in self._EVICTABLE),
                key=lambda s: (s["status"] == "needs_attention", s.get("last_activity", 0)),
            )
            n_to_drop = len(self._sits) - self._max_sits
            for s in evictable[:n_to_drop]:
                del self._sits[s["id"]]

    def _prune(self, now_ms: int) -> None:
        self._age_out(now_ms)
        self._enforce_cap()

    def situations(self, now_ms: int | None = None) -> list[dict]:
        with self._lock:
            if now_ms is not None:
                self._prune(now_ms)
            else:
                self._enforce_cap()
            return list(self._sits.values())

    def outcomes(self) -> list[dict]:
        with self._lock:
            return list(self._outcomes)

    def situation(self, sid: str) -> dict | None:
        with self._lock:
            s = self._sits.get(sid)
            return dict(s) if s is not None else None

    def reset(self) -> None:
        with self._lock:
            self._sits.clear()
            self._outcomes.clear()
            self._suppressed_count = 0

    # needs_attention is outstanding work, so it counts as open even though it is
    # not in-flight. approvalsPending narrows to diagnosed/acting on its own.
    _OPEN: ClassVar[set[str]] = {"detected", "diagnosed", "acting", "needs_attention"}

    def metrics(self) -> dict:
        with self._lock:
            return self._metrics()

    def _metrics(self) -> dict:
        # Enforce the same cap situations() does. Without this the two endpoints
        # count different sets, and the console renders them side by side: the
        # noise-reduction tile said "N alerts -> 3 open" while the incident list
        # directly beneath it showed 2. Same projection, two answers.
        self._enforce_cap()
        sits = list(self._sits.values())
        outs = self._outcomes
        # Escalations are "we never tried", so they belong in neither the numerator
        # nor the denominator of a remediation rate — counting them as attempts
        # would penalise the system for correctly refusing to guess.
        attempted = [o for o in outs if o["result"] != "escalated"]
        n_att = len(attempted)
        successes = sum(1 for o in attempted if o["result"] == "success")
        autos = sum(1 for o in attempted if o.get("hitl_mode") == "auto")
        mttrs = [o["mttr_ms"] for o in outs if o.get("mttr_ms") is not None]
        alerts = sum(s["memberCount"] for s in sits)
        n_sits = len(sits)
        open_sits = [s for s in sits if s["status"] in self._OPEN]
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
            "autoRemediatedPct": round(autos / n_att * 100, 1) if n_att else 0.0,
            "suppressedToday": self._suppressed_count,
            "approvalsPending": len(pending),
            "successRate": round(successes / n_att, 3) if n_att else 0.0,
            "needsAttention": sum(1 for s in sits if s["status"] == "needs_attention"),
        }

    @staticmethod
    def _service_of(s: Situation) -> str:
        for ev in s.member_events:
            for key in ("service", "job", "instance"):
                val = ev.labels.get(key)
                if val:
                    return val
        return "unknown"
