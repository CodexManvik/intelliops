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
        min_events: int = 1,
        suppress_min_samples: int = 1,
        suppress_mode: str = "drop",
    ) -> None:
        self._correlator = correlator
        self._correlator_factory = (
            correlator.clone_empty
            if hasattr(correlator, "clone_empty")
            else lambda: type(correlator)(
                z_threshold=correlator._z_threshold,
                warmup_samples=correlator._warmup_samples,
                detection_policy=correlator._policy,
            )
        )
        self._window = window_seconds
        self._suppress_threshold = suppress_threshold
        self._suppress_min_samples = max(1, int(suppress_min_samples))
        # What suppression DOES to a reliably-fixed signature:
        #   "drop"  (historical default here): the Situation is never emitted, so
        #           it never reaches RCA or action - the fault is left unfixed.
        #   "quiet": the Situation IS emitted, marked handling="quiet", so it is
        #           still diagnosed and remediated, just without paging a human
        #           when action can confirm the playbook's track record.
        # Either way it is also queued for situations.suppressed (the counter
        # and the log of what was suppressed).
        self._suppress_mode = suppress_mode
        self._group_by = group_by
        # How many anomalous events a window must hold before it is an incident.
        #
        # 1 reproduces the pre-change behaviour EXACTLY, and is the default for
        # the same reason DetectionPolicy defaults to disabled. But one sample
        # crossing the threshold is not an incident, it is a sample: real
        # telemetry wanders, and every detector worth the name requires the
        # breach to persist (Prometheus spells this `for:`). With a genuine
        # fault the target emits an anomalous sample on every poll, so a 30s
        # window holds dozens; an isolated excursion holds one or two. The live
        # overlay raises this, and the difference is four bogus "connection-pool
        # exhaustion" incidents versus none.
        self._min_events = max(1, int(min_events))
        # Keyed by _key(event). In "window" mode there is exactly one key, so
        # this is the old single-buffer behaviour with one dict lookup.
        self._buffers: dict[str, list[TelemetryEvent]] = {}
        self._max_scores: dict[str, float] = {}
        # A queue, not a slot: one flush_all() can suppress several buckets, and a
        # single slot kept only the last, silently dropping the rest.
        self._suppressed: list[Situation] = []
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
        if len(buf) < self._min_events:
            # Not (yet) sustained. A buffer younger than the window has not had
            # its full chance: the background flusher runs on its own timer and
            # can land a few seconds after a fault starts, and discarding there
            # would cost a whole window of detection latency. Leave it to keep
            # filling, and only discard once it has aged out.
            if (buf[-1].ts - buf[0].ts).total_seconds() < self._window:
                return None
            # Aged out and still thin. Drop it rather than routing it to
            # _suppressed: suppression means "this signature reliably
            # self-heals", which is a claim about a real incident, and this was
            # never one.
            self._buffers.pop(key, None)
            self._max_scores.pop(key, None)
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
        # Closed loop: a signature the system has reliably fixed before.
        if self._correlator.should_suppress(
            sit.signature, self._suppress_threshold, self._suppress_min_samples
        ):
            if self._suppress_mode == "quiet":
                sit = sit.model_copy(update={"handling": "quiet"})
                self._suppressed.append(sit)
                return sit
            self._suppressed.append(sit)
            return None
        return sit

    def retrain(self, training_data: list[dict]) -> None:
        """Replace the reliability map (what suppression reads) under the lock."""
        with self._lock:
            self._correlator.retrain(training_data)

    def snapshot(self) -> list[dict]:
        with self._lock:
            return self._correlator.snapshot()

    def load(self, rows: list[dict]) -> None:
        with self._lock:
            self._correlator.load(rows)

    def pop_suppressed(self) -> Situation | None:
        """Oldest suppressed Situation not yet published, or None."""
        with self._lock:
            return self._suppressed.pop(0) if self._suppressed else None

    def reset(self) -> None:
        with self._lock:
            # A baseline reset forgets what "normal" looks like, not which fixes
            # have worked: the reliability map comes from labelled outcomes and is
            # re-derived from them anyway, so carry it across.
            old = self._correlator
            self._correlator = self._correlator_factory()
            for attr in ("_reliability", "_samples"):
                if hasattr(old, attr):
                    setattr(self._correlator, attr, getattr(old, attr))
            self._buffers = {}
            self._max_scores = {}
            self._suppressed = []
