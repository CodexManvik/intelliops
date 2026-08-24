"""Correlation adapters: concrete Correlator implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from services.correlation.adapters.base_correlator import BaseCorrelator
from services.correlation.adapters.river_correlator import RiverCorrelator
from services.correlation.adapters.robust_correlator import RobustCorrelator

if TYPE_CHECKING:
    from common.config import Settings


def make_correlator(settings: Settings) -> BaseCorrelator:
    """Build the configured Correlator implementation from settings.correlator_kind."""
    kind = settings.correlator_kind
    if kind == "river":
        return RiverCorrelator(
            z_threshold=settings.correlation_z_threshold,
            warmup_samples=settings.correlation_warmup_samples,
        )
    if kind == "robust":
        return RobustCorrelator(
            z_threshold=settings.correlation_z_threshold,
            warmup_samples=settings.correlation_robust_warmup,
            seasonal_buckets=settings.correlation_seasonal_buckets,
            window_size=settings.correlation_robust_window,
        )
    if kind == "trained":
        raise NotImplementedError("trained correlator lands in Task 3")
    raise ValueError(f"Unknown CORRELATOR_KIND: {kind!r}")
