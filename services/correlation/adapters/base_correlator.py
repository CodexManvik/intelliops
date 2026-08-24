"""Engine-facing contract for pluggable correlators.

CorrelationEngine depends on more than the Correlator Protocol (detect/correlate/
retrain): it reads _z_threshold/_warmup_samples, calls _severity_band, should_suppress,
snapshot, load, and reconstructs the correlator via type(correlator)(z_threshold=,
warmup_samples=) on reset(). This ABC makes that implicit contract explicit and shared.

Subclasses MUST accept z_threshold and warmup_samples (the reset factory passes exactly
those); any extra __init__ kwargs MUST have defaults or reset() raises TypeError.
A subclass that overrides retrain to also train a model MUST call super().retrain(data)
to preserve the reliability map, or closed-loop suppression silently stops working.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod

from common.contracts import Situation, TelemetryEvent


class BaseCorrelator(ABC):
    def __init__(self, z_threshold: float = 3.0, warmup_samples: int = 50) -> None:
        self._z_threshold = z_threshold
        self._warmup_samples = warmup_samples
        self._reliability: dict[str, float] = {}

    @abstractmethod
    def detect(self, event: TelemetryEvent) -> float: ...

    @abstractmethod
    def correlate(self, events: list[TelemetryEvent], severity: str = "low") -> Situation: ...

    @abstractmethod
    def snapshot(self) -> list[dict]: ...

    @abstractmethod
    def load(self, rows: list[dict]) -> None: ...

    def is_anomaly(self, event: TelemetryEvent) -> bool:
        return self.detect(event) > self._z_threshold

    def retrain(self, training_data: list[dict]) -> None:
        # REPLACE semantics (recompute from scratch each call) — pinned by test_retrain.py.
        worked: dict[str, int] = {}
        total: dict[str, int] = {}
        for record in training_data:
            sig = record["signature"]
            total[sig] = total.get(sig, 0) + 1
            if record.get("worked"):
                worked[sig] = worked.get(sig, 0) + 1
        self._reliability = {sig: worked.get(sig, 0) / n for sig, n in total.items()}

    def reliability(self, signature: str) -> float:
        return self._reliability.get(signature, 0.0)

    def should_suppress(self, signature: str, threshold: float) -> bool:
        return self.reliability(signature) >= threshold

    def _severity_band(self, score: float) -> str:
        if score >= 8:
            return "high"
        if score >= 5:
            return "medium"
        return "low"

    @staticmethod
    def _signature(events: list[TelemetryEvent]) -> str:
        joined = "|".join(sorted(e.fingerprint for e in events))
        return hashlib.sha1(joined.encode()).hexdigest()[:16]
