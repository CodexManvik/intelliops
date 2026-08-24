"""Robust, seasonal anomaly detection (numpy batch median/MAD over per-bucket windows).

Unlike RiverCorrelator's online mean/variance, this uses the median absolute
deviation (MAD) of a bounded per-(metric, hour-bucket) window. MAD is robust
to outliers already present in the window: a single earlier spike shifts a
mean/variance baseline (desensitizing later detection of a similar spike),
but the median and MAD barely move because at most one of the window's
values is extreme. This is the key advantage over RiverCorrelator's z-score
for metrics that see occasional real spikes.

Seasonality: samples are bucketed by event.ts.hour (mod seasonal_buckets), so
a metric's day/night baseline doesn't get diluted by mixing hours together.

Persistence: snapshot()/load() round-trip the raw windows in-process (used by
CorrelationEngine.reset()). Durable cross-restart persistence is deferred —
see Task 2 brief Step 5 — so a `robust` correlator cold-starts on restart and
re-warms per bucket. This is intentional and not a Task 2 gap.
"""

from __future__ import annotations

import collections

import numpy as np

from common.contracts import Situation, SituationStatus, TelemetryEvent
from services.correlation.adapters.base_correlator import BaseCorrelator

_MAD_C = 1.4826  # MAD -> sigma consistency constant for normal data


class RobustCorrelator(BaseCorrelator):
    def __init__(
        self,
        z_threshold: float = 3.0,
        warmup_samples: int = 30,
        seasonal_buckets: int = 24,
        window_size: int = 128,
    ) -> None:
        # sets _z_threshold/_warmup_samples/_reliability
        super().__init__(z_threshold, warmup_samples)
        self._n_buckets = seasonal_buckets
        self._window_size = window_size
        self._windows: dict[tuple[str, int], collections.deque] = {}

    def _bucket(self, event: TelemetryEvent) -> int:
        return event.ts.hour % self._n_buckets

    def detect(self, event: TelemetryEvent) -> float:
        if event.value is None:
            return 0.0
        key = (event.name, self._bucket(event))
        win = self._windows.setdefault(key, collections.deque(maxlen=self._window_size))
        if len(win) < self._warmup_samples:
            score = 0.0
        else:
            arr = np.fromiter(win, dtype=float, count=len(win))
            med = np.median(arr)
            mad = np.median(np.abs(arr - med))
            score = 0.0 if mad == 0.0 else abs(event.value - med) / (_MAD_C * mad)
        win.append(event.value)  # score-before-fold (matches RiverCorrelator ordering)
        return float(score)

    def correlate(self, events: list[TelemetryEvent], severity: str = "low") -> Situation:
        if not events:
            raise ValueError("cannot correlate an empty event list")
        signature = self._signature(events)
        return Situation(
            id="sit-" + signature,
            status=SituationStatus.DETECTED,
            member_events=list(events),
            severity=severity,
            first_seen=min(e.ts for e in events),
            last_seen=max(e.ts for e in events),
            signature=signature,
        )

    def snapshot(self) -> list[dict]:
        out: list[dict] = []
        for (name, bucket), win in list(self._windows.items()):  # list() = live-resize guard
            out.append({"metric_name": name, "bucket": bucket, "n": len(win), "window": list(win)})
        return out

    def load(self, rows: list[dict]) -> None:
        for r in rows:
            key = (r["metric_name"], int(r["bucket"]))
            self._windows[key] = collections.deque(
                (float(x) for x in r["window"]), maxlen=self._window_size
            )
