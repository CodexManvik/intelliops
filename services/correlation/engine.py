"""Windowed correlation: buffer anomalous events and emit one Situation per window.

The engine scores each event via the correlator; anomalies accumulate in a
rolling time window keyed on event timestamps. When the window's span exceeds
window_seconds (or on an explicit flush), the buffer collapses into a single
Situation. Timestamps come from events, so behavior is deterministic.

The closed loop (Slice 4): a Situation whose signature has a proven
self-healing track record (reliability >= suppress_threshold) is suppressed —
not emitted — because the system has learned that when this fires, it is fixed."""

from __future__ import annotations

import threading

from common.contracts import Situation, TelemetryEvent
from services.correlation.adapters.river_correlator import RiverCorrelator


class CorrelationEngine:
    def __init__(
        self,
        correlator: RiverCorrelator,
        window_seconds: float = 30.0,
        suppress_threshold: float = 0.8,
    ) -> None:
        self._correlator = correlator
        self._correlator_factory = lambda: type(correlator)(
            z_threshold=correlator._z_threshold,
            warmup_samples=correlator._warmup_samples,
        )
        self._window = window_seconds
        self._suppress_threshold = suppress_threshold
        self._buffer: list[TelemetryEvent] = []
        self._max_score = 0.0
        self._suppressed: Situation | None = None
        # Guards _buffer/_max_score so a background time-flush (see the service
        # lifespan) can run concurrently with add() on the consumer thread.
        # Single-threaded callers (tests) are unaffected — the lock is uncontended.
        self._lock = threading.Lock()

    def add(self, event: TelemetryEvent) -> Situation | None:
        score = self._correlator.detect(event)
        if score <= self._correlator._z_threshold:
            return None
        with self._lock:
            emitted: Situation | None = None
            if self._buffer:
                span = (event.ts - self._buffer[0].ts).total_seconds()
                if span > self._window:
                    emitted = self._correlate_buffer()
            self._buffer.append(event)
            self._max_score = max(self._max_score, score)
            return emitted

    def flush(self) -> Situation | None:
        with self._lock:
            if not self._buffer:
                return None
            return self._correlate_buffer()

    def _correlate_buffer(self) -> Situation | None:
        severity = self._correlator._severity_band(self._max_score)
        sit = self._correlator.correlate(self._buffer, severity=severity)
        self._buffer = []
        self._max_score = 0.0
        # Closed loop: suppress a Situation whose signature reliably self-heals.
        if self._correlator.should_suppress(sit.signature, self._suppress_threshold):
            self._suppressed = sit
            return None
        return sit

    def pop_suppressed(self) -> Situation | None:
        with self._lock:
            s = self._suppressed
            self._suppressed = None
            return s

    def reset(self) -> None:
        with self._lock:
            self._correlator = self._correlator_factory()
            self._buffer = []
            self._max_score = 0.0
            self._suppressed = None
