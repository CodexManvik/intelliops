"""Benchmark harness: labeled scenarios + a runner to score correlators against
ground truth (precision/recall/false-positive-rate/detection-latency).

This package is intentionally decoupled from the engine/consumer runtime — it
only depends on BaseCorrelator's public detect()/is_anomaly() contract and
common.contracts.TelemetryEvent, so it can score any current or future
correlator kind without touching production wiring.
"""

from __future__ import annotations
