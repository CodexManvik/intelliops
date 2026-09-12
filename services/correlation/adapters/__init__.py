"""Correlation adapters: concrete Correlator implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from services.correlation.adapters.base_correlator import BaseCorrelator
from services.correlation.detection_policy import DetectionPolicy

if TYPE_CHECKING:
    from common.config import Settings


def _make_detection_policy(settings: Settings) -> DetectionPolicy:
    """Build the metric-kind-aware DetectionPolicy from settings (default off).

    `detection_policy.py` imports only `common.contracts`, so importing
    DetectionPolicy here stays slim: it does not pull numpy/river/sklearn into
    services (e.g. services/action) that only need common/stores.py.
    """
    return DetectionPolicy(
        enabled=(settings.detection_policy == "on"),
        thresholds={
            "ratio": settings.detection_ratio_threshold,
            "saturation_ratio": settings.detection_saturation_ratio_threshold,
            "saturation_percent": settings.detection_saturation_percent_threshold,
            "latency_ceiling_ms": settings.detection_latency_ceiling_ms,
        },
    )


def make_correlator(settings: Settings) -> BaseCorrelator:
    """Build the configured Correlator implementation from settings.correlator_kind.

    Correlator classes are imported lazily so importing this package (which
    common/stores.py does transitively via baseline_store/model_store) never
    pulls in numpy/river/sklearn for services that only need the SQLAlchemy
    stores. Only the kind actually selected loads its heavy dependency.
    """
    policy = _make_detection_policy(settings)
    kind = settings.correlator_kind
    if kind == "river":
        from services.correlation.adapters.river_correlator import RiverCorrelator

        return RiverCorrelator(
            z_threshold=settings.correlation_z_threshold,
            warmup_samples=settings.correlation_warmup_samples,
            detection_policy=policy,
        )
    if kind == "robust":
        from services.correlation.adapters.robust_correlator import RobustCorrelator

        return RobustCorrelator(
            z_threshold=settings.correlation_z_threshold,
            warmup_samples=settings.correlation_robust_warmup,
            seasonal_buckets=settings.correlation_seasonal_buckets,
            window_size=settings.correlation_robust_window,
            detection_policy=policy,
        )
    if kind == "trained":
        from services.correlation.adapters.trained_correlator import TrainedCorrelator

        return TrainedCorrelator(
            z_threshold=settings.correlation_z_threshold,
            warmup_samples=settings.correlation_robust_warmup,
            seasonal_buckets=settings.correlation_seasonal_buckets,
            window_size=settings.correlation_robust_window,
            detection_policy=policy,
        )
    raise ValueError(f"Unknown CORRELATOR_KIND: {kind!r}")
