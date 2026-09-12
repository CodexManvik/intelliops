"""AgentRunHub: cross-thread publish/subscribe hub for live trace streaming.

The AI runbook author's draft() runs in a worker THREAD (see
adapters/runbook_author.py), while the SSE endpoint that streams its trace
steps to the UI runs as an async coroutine on the event loop. This hub is the
bridge: worker threads call `publish(run_id, step)`, which marshals delivery
onto the loop via `loop.call_soon_threadsafe` so subscriber queues are only
ever touched from the loop thread. Modeled on services/read/projection.py's
pub-sub (see ReadModel.bind_loop/subscribe/unsubscribe/publish/_deliver), but
keyed by run_id so each run's subscribers are isolated from every other run's.

This hub holds no durable state — it is a live side-channel only. The durable
record of a run's steps lives in TraceStore (adapters/trace_store.py); a
subscriber that connects after a run has already ended relies on is_ended()
plus the stored steps (wired by the SSE endpoint in a later task), not on this
hub replaying history.
"""

from __future__ import annotations

import asyncio
import threading

from common.contracts import TraceStep


class AgentRunHub:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = {}
        self._ended: set[str] = set()
        self._subs_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called once from the async lifespan so worker threads can hand off."""
        self._loop = loop

    def subscribe(self, run_id: str, maxsize: int = 1000) -> asyncio.Queue:
        """MUST be called on the event-loop thread (from the SSE coroutine)."""
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        with self._subs_lock:
            self._subs.setdefault(run_id, set()).add(q)
        return q

    def unsubscribe(self, run_id: str, q: asyncio.Queue) -> None:
        with self._subs_lock:
            subs = self._subs.get(run_id)
            if subs is None:
                return
            subs.discard(q)
            if not subs:
                del self._subs[run_id]

    def publish(self, run_id: str, step: TraceStep) -> None:
        """Called from worker THREADS. Marshals delivery onto the loop."""
        loop = self._loop
        if loop is None:
            return
        with self._subs_lock:
            subs = list(self._subs.get(run_id, ()))
        for q in subs:
            try:
                loop.call_soon_threadsafe(self._deliver, q, step)
            except RuntimeError:
                pass  # loop closed during shutdown

    def _deliver(self, q: asyncio.Queue, step: TraceStep) -> None:
        # runs ON the loop thread
        try:
            q.put_nowait(step)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
                q.put_nowait(step)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    def mark_ended(self, run_id: str) -> None:
        """Backstop for a subscriber that connects after the run already ended.

        The primary close signal is the `outcome` TraceStep the caller
        publishes before calling this; is_ended() lets a late subscriber (who
        will never see that outcome step arrive on their queue) know to stop
        waiting and fall back to the stored steps instead.
        """
        with self._subs_lock:
            self._ended.add(run_id)

    def is_ended(self, run_id: str) -> bool:
        with self._subs_lock:
            return run_id in self._ended
