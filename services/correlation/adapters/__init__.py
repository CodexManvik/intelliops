"""Correlation adapters: concrete Correlator implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from services.correlation.adapters.base_correlator import BaseCorrelator

if TYPE_CHECKING:
    from common.config import Settings


def make_correlator(settings: Settings) -> BaseCorrelator:
    """Build the configured Correlator implementation from settings.correlator_kind.

    Correlator classes are imported lazily so importing this package (which
    common/stores.py does transitively via baseline_store/model_store) never
    pulls in numpy/river/sklearn for services that only need the SQLAlchemy
    stores. Only the kind actually selected loads its heavy dependency.
    """
    kind = settings.correlator_kind
    if kind == "river":
        from services.correlation.adapters.river_correlator import RiverCorrelator

        return RiverCorrelator(
            z_threshold=settings.correlation_z_threshold,
            warmup_samples=settings.correlation_warmup_samples,
        )
    if kind == "robust":
        from services.correlation.adapters.robust_correlator import RobustCorrelator

        return RobustCorrelator(
            z_threshold=settings.correlation_z_threshold,
            warmup_samples=settings.correlation_robust_warmup,
            seasonal_buckets=settings.correlation_seasonal_buckets,
            window_size=settings.correlation_robust_window,
        )
    if kind == "trained":
        from services.correlation.adapters.trained_correlator import TrainedCorrelator

        return TrainedCorrelator(
            z_threshold=settings.correlation_z_threshold,
            warmup_samples=settings.correlation_robust_warmup,
            seasonal_buckets=settings.correlation_seasonal_buckets,
            window_size=settings.correlation_robust_window,
        )
    raise ValueError(f"Unknown CORRELATOR_KIND: {kind!r}")
