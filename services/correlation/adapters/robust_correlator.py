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
from services.correlation.detection_policy import DetectionPolicy

_MAD_C = 1.4826  # MAD -> sigma consistency constant for normal data
# Floor for a step off a perfectly flat baseline. Comfortably above the
# default z_threshold (3.0) so such a step is always caught; the actual score
# rises with the relative size of the jump.
_FLAT_STEP_SCORE = 6.0
# How far a value must move off a flat baseline before it counts as a step.
#
# A zero-MAD window gives us no scale to judge by, so the previous code called
# ANY movement beyond float noise an anomaly. That is right for the cases this
# path exists for -- service_up going 1 -> 0, tls_handshake_failures going
# 0.4 -> 46.4 -- and wrong for a metric that was pinned at a constant by a
# simulator and then started reporting real, small variation: 18.0 -> 17.34 was
# scored 6.0 and opened an incident. With no spread to normalise by, the only
# scale available is the baseline's own magnitude, so require the move to be
# at least half of it. Every genuine case above clears that comfortably.
_FLAT_MIN_RELATIVE = 0.5


class RobustCorrelator(BaseCorrelator):
    def __init__(
        self,
        z_threshold: float = 3.0,
        warmup_samples: int = 30,
        seasonal_buckets: int = 24,
        window_size: int = 128,
        detection_policy: DetectionPolicy | None = None,
        key_by: str = "metric",
    ) -> None:
        # sets _z_threshold/_warmup_samples/_reliability/_policy
        super().__init__(z_threshold, warmup_samples, detection_policy=detection_policy)
        self._n_buckets = seasonal_buckets
        self._window_size = window_size
        # "metric" (default, historical): one window per metric NAME, pooling every
        # service's samples. A fault on a quiet service is then judged against a
        # busier one's normal - a 10 -> 45 CPU jump scores ~0 beside a service
        # idling at 80. "series": one window per (metric, service label).
        # Per-series windows fill more slowly (one sample per poll instead of one
        # per service per poll), so pair it with a smaller warm-up.
        self._key_by = key_by
        self._windows: dict[tuple, collections.deque] = {}

    def _clone_kwargs(self) -> dict:
        return {
            **super()._clone_kwargs(),
            "seasonal_buckets": self._n_buckets,
            "window_size": self._window_size,
            "key_by": self._key_by,
        }

    def _bucket(self, event: TelemetryEvent) -> int:
        return event.ts.hour % self._n_buckets

    def _series(self, event: TelemetryEvent) -> str:
        if self._key_by != "series":
            return ""
        return event.labels.get("service") or event.labels.get("job") or ""

    def _key(self, name: str, series: str, bucket: int) -> tuple:
        # The default mode keeps the historical 2-tuple so snapshots and any
        # external reader of _windows see exactly the old shape.
        return (name, series, bucket) if self._key_by == "series" else (name, bucket)

    def _scoring_window(self, name: str, series: str, win) -> collections.deque:
        """The window to score against: this hour's, or - while it is still
        warming - the fullest warm window of the same series from another hour.

        Without the fallback every top-of-hour opened a fresh, empty bucket and the
        detector scored 0 for everything until it re-warmed (~2 minutes under the
        live posture) - a blackout on the hour, every hour."""
        if len(win) >= self._warmup_samples:
            return win
        best = win
        for key, other in list(self._windows.items()):
            if other is win or len(other) < self._warmup_samples:
                continue
            if key[0] != name or (self._key_by == "series" and key[1] != series):
                continue
            if len(other) > len(best):
                best = other
        return best

    def detect(self, event: TelemetryEvent) -> float:
        if event.value is None:
            return 0.0
        series = self._series(event)
        key = self._key(event.name, series, self._bucket(event))
        win = self._windows.setdefault(key, collections.deque(maxlen=self._window_size))
        ref = self._scoring_window(event.name, series, win)
        if len(ref) < self._warmup_samples:
            score = 0.0
        else:
            arr = np.fromiter(ref, dtype=float, count=len(ref))
            med = np.median(arr)
            mad = np.median(np.abs(arr - med))
            deviation = abs(event.value - med)
            if mad > 0.0:
                score = deviation / (_MAD_C * mad)
            elif deviation <= _FLAT_MIN_RELATIVE * max(abs(med), 1.0):
                # Perfectly flat baseline and the value has not meaningfully
                # moved: normal. Covers both a re-published identical value
                # (deviation ~ _FLAT_TOLERANCE) and ordinary small variation.
                score = 0.0
            else:
                # MAD == 0 means every sample in the window is identical, so ANY
                # real movement is unprecedented by definition. Returning 0.0 here
                # (the previous behaviour) made this correlator permanently blind
                # to a step on a flat series: a metric pinned at 0.4 that jumps to
                # 46.4 scored 0.00 forever, while RiverCorrelator scored 10.91.
                # Verified against the real tls_handshake_failures series, whose
                # baseline is exactly constant - so the escalation path could
                # never fire under `robust`.
                #
                # There is no sample spread to scale by, so the magnitude is
                # expressed relative to the baseline itself and floored at the
                # threshold: this is an anomaly, and how large it is is a matter
                # of degree, not of whether.
                score = max(_FLAT_STEP_SCORE, deviation / max(abs(med), 1.0))
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

    def baseline_snapshot(self) -> dict:
        """Per-metric {name: {mean, std}} for attaching to an emitted Situation.

        Post-remediation verification (services/action/verify.py) asks whether a
        firing metric has returned to its baseline, and looks it up BY METRIC
        NAME. Without this method the engine attached no baseline at all under
        CORRELATOR_KIND=robust and every score-only metric was unverifiable, so
        a successful fix was always reported as a rollback.

        Samples are pooled across the seasonal hour buckets: the verifier has no
        bucket context, and a metric-level baseline is the stable thing to
        compare a just-recovered value against. The estimators match detect()'s
        (median / MAD -> sigma), so verification agrees with the detection that
        produced the situation.
        """
        pooled: dict[str, list[float]] = {}
        for key, win in list(self._windows.items()):  # list() = live-resize guard
            if win:
                pooled.setdefault(key[0], []).extend(win)

        out: dict = {}
        for name, samples in pooled.items():
            arr = np.fromiter(samples, dtype=float, count=len(samples))
            med = float(np.median(arr))
            mad = float(np.median(np.abs(arr - med)))
            # std 0.0 is a real, meaningful answer here: the metric never moved.
            # service_up is exactly that, and verify.py treats it as the
            # strongest possible baseline rather than a missing one.
            out[name] = {"mean": med, "std": mad * _MAD_C}
        return out

    def snapshot(self) -> list[dict]:
        out: list[dict] = []
        for key, win in list(self._windows.items()):  # list() = live-resize guard
            row = {"metric_name": key[0], "bucket": key[-1], "n": len(win), "window": list(win)}
            if self._key_by == "series":
                row["series"] = key[1]
            out.append(row)
        return out

    def load(self, rows: list[dict]) -> None:
        for r in rows:
            key = self._key(r["metric_name"], r.get("series", ""), int(r["bucket"]))
            self._windows[key] = collections.deque(
                (float(x) for x in r["window"]), maxlen=self._window_size
            )
