"""TrainedCorrelator: an IsolationForest model composed over the robust online path.

This is the "trained" correlator kind. It does NOT replace the online detector —
it *wraps* a RobustCorrelator (the median/MAD seasonal path from Task 2) and, once
a model has been fitted, blends an IsolationForest anomaly score on top. Every
event still gets a real online score, so a cold correlator (no model yet) behaves
byte-identically to a bare RobustCorrelator.

Why compose rather than subclass RobustCorrelator? The engine's contract is
detect()/correlate()/snapshot()/load() plus the reset factory
`type(c)(z_threshold=, warmup_samples=)`. TrainedCorrelator must satisfy all of
that AND own a separate, persistable model artifact. Delegating snapshot()/load()
to the composed robust correlator keeps the engine's baseline path (list[dict] of
windows) completely separate from the model artifact path (opaque joblib bytes) —
see the deliberately distinct method names below.

Method map (the collision the review caught):
  - detect(event) -> float          : ALL scoring; online blended with model.
  - correlate(...)                  : delegate to robust.
  - snapshot() -> list[dict]        : ENGINE BASELINE path; delegate to robust.
  - load(rows: list[dict]) -> None  : ENGINE BASELINE path; delegate to robust.
  - fit() -> bool                   : train IsolationForest on the feature deque.
  - serialize() -> bytes | None     : MODEL artifact out (joblib blob or None).
  - load_model(blob) -> bool        : MODEL artifact in (refuse on feature drift).

sklearn/joblib are imported LAZILY (inside fit/serialize/load_model) so file-mode
and non-trained services never import sklearn at module load.
"""

from __future__ import annotations

import collections
import math

from common.contracts import Situation, TelemetryEvent, TelemetryKind
from services.correlation.adapters.base_correlator import BaseCorrelator
from services.correlation.adapters.robust_correlator import RobustCorrelator

# Frozen feature schema (9 columns). Persisted WITH the model blob; a loaded blob
# whose feature_names differ is refused (the correlator stays cold) so a model
# trained against a different featurization can never score live events.
FEATURE_NAMES: tuple[str, ...] = (
    "value",
    "z_online",
    "hour_sin",
    "hour_cos",
    "day_of_week",
    "kind_metric",
    "kind_log",
    "kind_trace",
    "label_count",
)

# IsolationForest.score_samples returns HIGHER=more normal (~0) and lower (~-0.5)
# for anomalies. We negate so HIGHER=more anomalous, then multiply by this factor
# to bring the model score into the same rough magnitude as the online z-score
# (threshold ~3), so max(online, model) is a meaningful blend rather than the
# model always losing. The exact factor is not load-bearing for correctness — the
# sign (negation) is; any positive constant preserves the outlier>normal ordering.
_MODEL_SCALE = 10.0


class TrainedCorrelator(BaseCorrelator):
    def __init__(
        self,
        z_threshold: float = 3.0,
        warmup_samples: int = 30,
        seasonal_buckets: int = 24,
        window_size: int = 128,
        min_fit_samples: int = 200,
        contamination: float = 0.02,
    ) -> None:
        # Store _z_threshold/_warmup_samples/_reliability on the base. Every extra
        # kwarg is defaulted so the engine reset factory — which passes ONLY
        # z_threshold + warmup_samples — reconstructs this without a TypeError.
        super().__init__(z_threshold, warmup_samples)
        self._min_fit_samples = min_fit_samples
        self._contamination = contamination
        # The online path: a composed RobustCorrelator built with the same params.
        self._robust = RobustCorrelator(
            z_threshold=z_threshold,
            warmup_samples=warmup_samples,
            seasonal_buckets=seasonal_buckets,
            window_size=window_size,
        )
        # Rolling buffer of featurized events for the next fit(). Bounded so a
        # long-lived service trains on recent behavior, not the whole history.
        self._features: collections.deque[list[float]] = collections.deque(maxlen=4096)
        self._model = None  # sklearn IsolationForest once fitted, else None

    # --- featurization --------------------------------------------------------

    @staticmethod
    def featurize(event: TelemetryEvent, z_online: float) -> list[float]:
        """Map an event + its online score to the frozen 9-column feature row."""
        hour = event.ts.hour
        return [
            float(event.value) if event.value is not None else 0.0,
            float(z_online),
            math.sin(2 * math.pi * hour / 24.0),
            math.cos(2 * math.pi * hour / 24.0),
            float(event.ts.weekday()),
            1.0 if event.kind == TelemetryKind.METRIC else 0.0,
            1.0 if event.kind == TelemetryKind.LOG else 0.0,
            1.0 if event.kind == TelemetryKind.TRACE else 0.0,
            float(len(event.labels)),
        ]

    @staticmethod
    def _rescale(neg_score: float) -> float:
        """Bring the negated IsolationForest score into the online z-score range."""
        return max(0.0, neg_score) * _MODEL_SCALE

    # --- scoring (ALL logic lives here; the engine only calls detect) ---------

    def detect(self, event: TelemetryEvent) -> float:
        # Online score first (this also folds the value into the robust window).
        online = self._robust.detect(event)
        # Featurize against the online score and buffer for the next fit().
        row = self.featurize(event, online)
        self._features.append(row)
        # Cold start (no model) -> return the online score alone. This makes a
        # freshly constructed TrainedCorrelator byte-identical to RobustCorrelator.
        if self._model is None:
            return online
        # score_samples: HIGHER = more normal, so NEGATE for anomaly magnitude.
        neg = -float(self._model.score_samples([row])[0])
        model_score = self._rescale(neg)
        return max(online, model_score)

    # --- model lifecycle (lazy sklearn/joblib) --------------------------------

    def fit(self) -> bool:
        """Train an IsolationForest on the buffered features. Returns True if a
        model was produced (enough samples), False otherwise (stays as-is)."""
        if len(self._features) < self._min_fit_samples:
            return False
        from sklearn.ensemble import IsolationForest

        rows = list(self._features)
        model = IsolationForest(
            n_estimators=100,
            contamination=self._contamination,
            random_state=0,  # reproducible fits (round-trip test relies on this)
        )
        model.fit(rows)
        self._model = model
        return True

    def serialize(self) -> bytes | None:
        """Serialize the fitted model + feature schema to a joblib blob, or None
        if there is nothing fitted to persist."""
        if self._model is None:
            return None
        import io

        import joblib

        buf = io.BytesIO()
        joblib.dump(
            {"model": self._model, "feature_names": list(FEATURE_NAMES)},
            buf,
        )
        return buf.getvalue()

    def load_model(self, blob: bytes | None) -> bool:
        """Load a persisted model blob. Refuses (returns False, stays cold) on any
        error or on feature-name drift. Distinct from load(rows) — this takes
        bytes and touches ONLY the model, never the robust baseline windows."""
        if not blob:
            return False
        import io

        import joblib

        try:
            payload = joblib.load(io.BytesIO(blob))
            if list(payload.get("feature_names", [])) != list(FEATURE_NAMES):
                return False  # schema drift -> refuse, stay cold
            self._model = payload["model"]
            return True
        except Exception:  # noqa: BLE001 — a bad/incompatible blob just means cold start
            return False

    # --- engine contract: correlate + BASELINE path (delegate to robust) ------

    def correlate(self, events: list[TelemetryEvent], severity: str = "low") -> Situation:
        return self._robust.correlate(events, severity=severity)

    def snapshot(self) -> list[dict]:
        # ENGINE BASELINE path: the robust windows, NOT the model artifact.
        return self._robust.snapshot()

    def load(self, rows: list[dict]) -> None:
        # ENGINE BASELINE path: rebuild the robust windows from list[dict]. This is
        # deliberately NOT load_model(blob) — the two never collide.
        self._robust.load(rows)
