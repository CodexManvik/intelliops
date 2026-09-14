"""Windowed correlation: buffer anomalous events and emit one Situation per window.

The engine scores each event via the correlator; anomalies accumulate in a
rolling time window keyed on event timestamps. When the window's span exceeds
window_seconds (or on an explicit flush), the buffer collapses into a single
Situation. Timestamps come from events, so behavior is deterministic.

The closed loop (Slice 4): a Situation whose signature has a proven
self-healing track record (reliability >= suppress_threshold) is suppressed —
not emitted — because the system has learned that when this fires, it is fixed.

Grouping (`group_by`, ADR-012 switch, default "window" = unchanged): events are
bucketed by a key before windowing. In "window" mode every event shares one
bucket, so two services failing inside the same window collapse into a single
Situation — the historical behaviour, and the reason the Meridian ops panel had
to forbid concurrent fault injection. In "service" mode the key is the event's
`service` label, so concurrent faults on different services stay separate
incidents, each attributed and diagnosed on its own. The bucketing is the only
difference: one bucket behaves exactly as before."""

from __future__ import annotations

import threading

from common.contracts import Situation, TelemetryEvent
from services.correlation.adapters.river_correlator import RiverCorrelator


class CorrelationEngine:
    # Bucket key used when grouping is off. Any constant works; "" keeps the
    # single-bucket path free of per-event string work.
    _ALL = ""

    def __init__(
        self,
        correlator: RiverCorrelator,
        window_seconds: float = 30.0,
        suppress_threshold: float = 0.8,
        group_by: str = "window",
    ) -> None:
        self._correlator = correlator
        self._correlator_factory = lambda: type(correlator)(
            z_threshold=correlator._z_threshold,
            warmup_samples=correlator._warmup_samples,
            detection_policy=correlator._policy,
        )
        self._window = window_seconds
        self._suppress_threshold = suppress_threshold
        self._group_by = group_by
        # Keyed by _key(event). In "window" mode there is exactly one key, so
        # this is the old single-buffer behaviour with one dict lookup.
        self._buffers: dict[str, list[TelemetryEvent]] = {}
        self._max_scores: dict[str, float] = {}
        self._suppressed: Situation | None = None
        # Guards _buffer/_max_score so a background time-flush (see the service
        # lifespan) can run concurrently with add() on the consumer thread.
        # Single-threaded callers (tests) are unaffected — the lock is uncontended.
        self._lock = threading.Lock()

    def _key(self, event: TelemetryEvent) -> str:
        if self._group_by != "service":
            return self._ALL
        # Unlabelled events share one bucket rather than vanishing into their own.
        return event.labels.get("service") or "unknown"

    def add(self, event: TelemetryEvent) -> Situation | None:
        # detect() mutates the correlator's per-metric baseline (_mean/_var/
        # _count); snapshot()/load()/reset() touch that same state under this
        # lock from the flusher thread. Score UNDER the lock so a concurrent
        # snapshot can never read a half-updated baseline. Contention is low
        # (one consumer thread scores; the flusher only holds the lock briefly),
        # so widening the existing critical section costs nothing measurable and
        # is simpler than a second baseline-only lock.
        with self._lock:
            score = self._correlator.detect(event)
            if not self._correlator.is_anomaly_scored(event, score):
                return None
            key = self._key(event)
            buf = self._buffers.get(key)
            emitted: Situation | None = None
            # Only this key's window can overflow on this event, so at most one
            # Situation is emitted here - add()'s contract is unchanged.
            if buf:
                span = (event.ts - buf[0].ts).total_seconds()
                if span > self._window:
                    emitted = self._correlate_buffer(key)
            self._buffers.setdefault(key, []).append(event)
            self._max_scores[key] = max(self._max_scores.get(key, 0.0), score)
            return emitted

    def flush(self) -> Situation | None:
        """Flush one bucket. Kept for callers that expect a single Situation.

        With grouping on there may be more than one non-empty bucket; use
        flush_all() to drain them, or buffered incidents are stranded.
        """
        out = self.flush_all()
        return out[0] if out else None

    def flush_all(self) -> list[Situation]:
        """Collapse every non-empty bucket. Suppressed ones are omitted (they go
        to pop_suppressed), so this can return fewer Situations than buckets."""
        with self._lock:
            out = []
            for key in list(self._buffers):
                sit = self._correlate_buffer(key)
                if sit is not None:
                    out.append(sit)
            return out

    def _correlate_buffer(self, key: str = _ALL) -> Situation | None:
        buf = self._buffers.get(key)
        if not buf:
            return None
        peak = self._max_scores.get(key, 0.0)
        severity = self._correlator._severity_band(peak)
        sit = self._correlator.correlate(buf, severity=severity)
        baseline = (
            self._correlator.baseline_snapshot()
            if hasattr(self._correlator, "baseline_snapshot")
            else None
        )
        member_metrics = {e.name for e in buf}
        if baseline is not None:
            baseline = {k: v for k, v in baseline.items() if k in member_metrics}
        sit = sit.model_copy(update={"peak_score": peak, "baseline": baseline})
        self._buffers.pop(key, None)
        self._max_scores.pop(key, None)
        # Closed loop: suppress a Situation whose signature reliably self-heals.
        if self._correlator.should_suppress(sit.signature, self._suppress_threshold):
            self._suppressed = sit
            return None
        return sit

    def snapshot(self) -> list[dict]:
        with self._lock:
            return self._correlator.snapshot()

    def load(self, rows: list[dict]) -> None:
        with self._lock:
            self._correlator.load(rows)

    def pop_suppressed(self) -> Situation | None:
        with self._lock:
            s = self._suppressed
            self._suppressed = None
            return s

    def reset(self) -> None:
        with self._lock:
            self._correlator = self._correlator_factory()
            self._buffers = {}
            self._max_scores = {}
            self._suppressed = None
