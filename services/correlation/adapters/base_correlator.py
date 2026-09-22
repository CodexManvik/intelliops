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
from services.correlation.detection_policy import DetectionPolicy


class BaseCorrelator(ABC):
    def __init__(
        self,
        z_threshold: float = 3.0,
        warmup_samples: int = 50,
        detection_policy: DetectionPolicy | None = None,
    ) -> None:
        self._z_threshold = z_threshold
        self._warmup_samples = warmup_samples
        self._reliability: dict[str, float] = {}
        # How many labelled outcomes each reliability figure rests on.
        self._samples: dict[str, int] = {}
        self._policy = (
            detection_policy if detection_policy is not None else DetectionPolicy(enabled=False)
        )

    @abstractmethod
    def detect(self, event: TelemetryEvent) -> float: ...

    @abstractmethod
    def correlate(self, events: list[TelemetryEvent], severity: str = "low") -> Situation: ...

    @abstractmethod
    def snapshot(self) -> list[dict]: ...

    @abstractmethod
    def load(self, rows: list[dict]) -> None: ...

    def _clone_kwargs(self) -> dict:
        """Constructor kwargs that reproduce this correlator's configuration.
        Subclasses with extra settings extend this."""
        return {
            "z_threshold": self._z_threshold,
            "warmup_samples": self._warmup_samples,
            "detection_policy": self._policy,
        }

    def clone_empty(self) -> BaseCorrelator:
        """A fresh correlator with the same configuration and no learned state.

        CorrelationEngine.reset() used to rebuild via type(c)(z_threshold=,
        warmup_samples=, detection_policy=), which quietly reset every other
        setting - window size, seasonal buckets - to its default."""
        return type(self)(**self._clone_kwargs())

    def is_anomaly(self, event: TelemetryEvent) -> bool:
        return self.is_anomaly_scored(event, self.detect(event))

    def is_anomaly_scored(self, event: TelemetryEvent, score: float) -> bool:
        return self._policy.is_anomaly(event, score, self._z_threshold)

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
        self._samples = dict(total)

    def reliability(self, signature: str) -> float:
        return self._reliability.get(signature, 0.0)

    def should_suppress(self, signature: str, threshold: float, min_samples: int = 1) -> bool:
        # min_samples: a single success is reliability 1/1 = 1.0, which cleared any
        # threshold. One data point is not a track record.
        if self._samples.get(signature, 0) < min_samples:
            return False
        return self.reliability(signature) >= threshold

    def _severity_band(self, score: float) -> str:
        if score >= 8:
            return "high"
        if score >= 5:
            return "medium"
        return "low"

    @staticmethod
    def _signature(events: list[TelemetryEvent]) -> str:
        # The SET of series involved, not the multiset of samples. A fingerprint
        # identifies a series (source + name + labels), so every sample of one
        # series shares it; hashing the raw list made the signature depend on how
        # many anomalous samples happened to land in the window - six vs seven
        # samples of the same fault on the same service gave two unrelated
        # signatures, and everything keyed on the signature (suppression,
        # graduation evidence, RCA reliability, the author's past decisions)
        # only matched by luck.
        joined = "|".join(sorted({e.fingerprint for e in events}))
        return hashlib.sha1(joined.encode()).hexdigest()[:16]
