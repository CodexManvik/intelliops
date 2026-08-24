"""Isolation Forest based anomaly detection + correlation.

This implements the Correlator interface using scikit-learn's IsolationForest.
It maintains a buffer of recent values per metric and retrains on labeled data.
"""

from __future__ import annotations

import hashlib
import logging
from collections import deque
from typing import Any

import numpy as np
from sklearn.ensemble import IsolationForest

from common.contracts import Situation, SituationStatus, TelemetryEvent

logger = logging.getLogger(__name__)


class IsolationForestCorrelator:
    """Anomaly detection using Isolation Forest with online retraining."""

    def __init__(
        self,
        contamination: float = 0.1,
        max_samples: int = 100,
        buffer_size: int = 200,
    ) -> None:
        self._contamination = contamination
        self._max_samples = max_samples
        self._buffer_size = buffer_size
        self._buffers: dict[str, deque[float]] = {}
        self._models: dict[str, IsolationForest] = {}
        self._retrain_count: int = 0
        self._reliability: dict[str, float] = {}
        self._signature_cache: dict[str, float] = {}

    def detect(self, event: TelemetryEvent) -> float:
        if event.value is None:
            return 0.0
        name = event.name
        if name not in self._buffers:
            self._buffers[name] = deque(maxlen=self._buffer_size)
        self._buffers[name].append(event.value)
        if len(self._buffers[name]) < self._max_samples:
            return 0.0
        if name not in self._models:
            self._models[name] = IsolationForest(
                contamination=self._contamination,
                max_samples=self._max_samples,
                random_state=42,
            )
            X = self._get_features(name)
            if len(X) >= self._max_samples:
                self._models[name].fit(X)
        try:
            current_X = np.array([[event.value]])
            decision = self._models[name].decision_function(current_X)[0]
            score = max(0.0, -decision * 2)
            return min(score, 1.0)
        except Exception as e:
            logger.warning("IsolationForest scoring failed for %s: %s", name, e)
            return 0.0

    def _get_features(self, metric_name: str) -> np.ndarray:
        buffer = self._buffers.get(metric_name, deque())
        if len(buffer) < self._max_samples:
            return np.array([])
        values = list(buffer)[-self._max_samples:]
        return np.array(values).reshape(-1, 1)

    def correlate(self, events: list[TelemetryEvent]) -> Situation:
        if not events:
            raise ValueError("cannot correlate an empty event list")
        signature = self._signature(events)
        self._signature_cache[signature] = events[-1].ts.timestamp()
        scores = []
        for e in events:
            scores.append(self.detect(e))
        max_score = max(scores) if scores else 0.0
        if max_score > 0.8:
            severity = "high"
        elif max_score > 0.6:
            severity = "medium"
        else:
            severity = "low"
        return Situation(
            id="sit-" + signature,
            status=SituationStatus.DETECTED,
            member_events=list(events),
            severity=severity,
            first_seen=min(e.ts for e in events),
            last_seen=max(e.ts for e in events),
            signature=signature,
        )

    def retrain(self, training_data: list[dict]) -> None:
        worked: dict[str, int] = {}
        total: dict[str, int] = {}
        for record in training_data:
            sig = record.get("signature", "")
            if not sig:
                continue
            total[sig] = total.get(sig, 0) + 1
            if record.get("worked", False):
                worked[sig] = worked.get(sig, 0) + 1
        self._reliability = {
            sig: worked.get(sig, 0) / max(1, n)
            for sig, n in total.items()
        }
        self._retrain_count += 1
        logger.info(
            "Retrain called (count=%d), reliability updated for %d signatures",
            self._retrain_count,
            len(self._reliability),
        )

    def snapshot(self) -> list[dict]:
        rows = []
        for name in self._models.keys():
            buffer_values = list(self._buffers.get(name, deque()))
            rows.append({
                "metric_name": name,
                "model_type": "isolation_forest",
                "contamination": self._contamination,
                "max_samples": self._max_samples,
                "buffer_values": buffer_values[-self._max_samples:],
                "n_samples": len(self._buffers.get(name, deque())),
                "reliability": self._reliability.get(name, 0.0),
            })
        return rows

    def load(self, rows: list[dict]) -> None:
        for row in rows:
            name = row.get("metric_name")
            if not name or row.get("model_type") != "isolation_forest":
                continue
            values = row.get("buffer_values", [])
            self._buffers[name] = deque(values, maxlen=self._buffer_size)
            if len(self._buffers[name]) >= self._max_samples:
                X = self._get_features(name)
                if len(X) > 0:
                    model = IsolationForest(
                        contamination=row.get("contamination", self._contamination),
                        max_samples=row.get("max_samples", self._max_samples),
                        random_state=42,
                    )
                    model.fit(X)
                    self._models[name] = model
            reliability = row.get("reliability", 0.0)
            if reliability > 0:
                self._reliability[name] = reliability

    def reliability(self, signature: str) -> float:
        return self._reliability.get(signature, 0.0)

    def should_suppress(self, signature: str, threshold: float) -> bool:
        return self.reliability(signature) >= threshold

    @staticmethod
    def _signature(events: list[TelemetryEvent]) -> str:
        joined = "|".join(sorted(e.fingerprint for e in events))
        return hashlib.sha1(joined.encode()).hexdigest()[:16]

    def reset(self) -> None:
        self._buffers.clear()
        self._models.clear()
        self._reliability.clear()
        self._signature_cache.clear()
        self._retrain_count = 0
        logger.info("IsolationForestCorrelator reset")
