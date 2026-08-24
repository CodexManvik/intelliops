"""Seeded, deterministic scenario generators for the correlator benchmark.

Each scenario function returns `(events, labels)`: `events` is a
chronologically ordered list of `TelemetryEvent`, `labels` is a same-length
list of bool ground truth (True = this specific event is an anomaly). All
randomness is drawn from a `random.Random(seed)` instance constructed inside
the function — no module-level or global RNG state — so the same seed always
produces byte-identical streams (see test_benchmark.py::test_determinism).

Scenario catalogue:
  - normal_noise       : Gaussian noise around a flat mean. All labels False.
                          Sanity baseline — nobody should flag much here.
  - seasonal_cycle      : A daily pattern (value plateaus higher during a few
                          "peak" hours-of-day, spread across many days). All
                          labels False. This is the scenario a NON-seasonal
                          online z-score baseline (RiverCorrelator) genuinely
                          false-positives on: the peak hours are common enough
                          in the stream to be "normal" for that time of day,
                          but rare enough overall to look like global outliers
                          to a single running mean/variance. A seasonal
                          detector (RobustCorrelator, bucketed by hour-of-day)
                          sees each hour's own tight local baseline instead.
  - point_anomaly       : Stable stream with sharp, brief spikes injected at
                          known indices. Labels True exactly at the spikes.
  - sustained_anomaly   : A level shift (mean jumps and stays shifted) over a
                          contiguous region. Labels True for the shifted
                          region.
  - correlation_break   : Two metrics that normally move together (metric_b
                          tracks metric_a's noise term); metric_b diverges
                          for a region while metric_a stays normal. Labels
                          True on metric_b's events during the divergence
                          (metric_a's events are always labeled False — it
                          never breaks).

Every scenario accepts a `seed` and small size knobs so callers (tests vs the
CLI) can trade speed for statistical weight without duplicating generator
logic.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from common.contracts import TelemetryEvent, TelemetryKind

_EPOCH = datetime(2026, 8, 1, 0, 0, 0, tzinfo=UTC)

Scenario = Callable[..., tuple[list[TelemetryEvent], list[bool]]]


def _event(name: str, value: float, ts: datetime, idx: int) -> TelemetryEvent:
    return TelemetryEvent(
        source="benchmark",
        kind=TelemetryKind.METRIC,
        name=name,
        value=value,
        labels={},
        ts=ts,
        fingerprint=f"{name}-{idx}",
    )


def normal_noise(
    n: int = 300,
    mean: float = 50.0,
    sd: float = 2.0,
    seed: int = 1,
    metric_name: str = "cpu",
) -> tuple[list[TelemetryEvent], list[bool]]:
    """Pure Gaussian noise around a flat mean. Nothing here is anomalous."""
    rng = random.Random(seed)
    events: list[TelemetryEvent] = []
    labels: list[bool] = []
    for i in range(n):
        ts = _EPOCH + timedelta(seconds=i)
        value = mean + rng.gauss(0, sd)
        events.append(_event(metric_name, value, ts, i))
        labels.append(False)
    return events, labels


def seasonal_cycle(
    n_hours: int = 48,
    per_hour: int = 10,
    mean: float = 100.0,
    amplitude: float = 40.0,
    noise_sd: float = 1.0,
    peak_hours: tuple[int, ...] = (9, 10, 11),
    seed: int = 2,
    metric_name: str = "cpu",
) -> tuple[list[TelemetryEvent], list[bool]]:
    """A daily pattern: value plateaus `amplitude` higher during `peak_hours`
    (hour-of-day, UTC) every day, otherwise sits at `mean`. Spread across
    `n_hours` (so the pattern repeats across multiple days) with `per_hour`
    samples per hour. All labels False — this is a normal recurring pattern,
    not an anomaly; only a detector without a seasonal baseline mistakes the
    peak hours for outliers.
    """
    rng = random.Random(seed)
    events: list[TelemetryEvent] = []
    labels: list[bool] = []
    idx = 0
    for h in range(n_hours):
        for i in range(per_hour):
            ts = _EPOCH + timedelta(hours=h, minutes=i * (60 / per_hour))
            hour_of_day = ts.hour
            level = amplitude if hour_of_day in peak_hours else 0.0
            value = mean + level + rng.gauss(0, noise_sd)
            events.append(_event(metric_name, value, ts, idx))
            labels.append(False)
            idx += 1
    return events, labels


def point_anomaly(
    n: int = 300,
    mean: float = 50.0,
    sd: float = 2.0,
    spike_magnitude: float = 40.0,
    spike_every: int = 50,
    warmup: int = 60,
    seed: int = 3,
    metric_name: str = "cpu",
) -> tuple[list[TelemetryEvent], list[bool]]:
    """A stable stream with sharp single-sample spikes injected every
    `spike_every` indices (starting only after `warmup`, so detectors have a
    real baseline before the first spike). Labels True exactly at the spikes.
    """
    rng = random.Random(seed)
    events: list[TelemetryEvent] = []
    labels: list[bool] = []
    for i in range(n):
        ts = _EPOCH + timedelta(seconds=i)
        is_spike = i >= warmup and i % spike_every == 0
        value = mean + rng.gauss(0, sd) + (spike_magnitude if is_spike else 0.0)
        events.append(_event(metric_name, value, ts, i))
        labels.append(is_spike)
    return events, labels


def sustained_anomaly(
    n: int = 300,
    mean: float = 50.0,
    sd: float = 2.0,
    shift_magnitude: float = 25.0,
    shift_start: int = 150,
    shift_len: int = 40,
    seed: int = 4,
    metric_name: str = "cpu",
) -> tuple[list[TelemetryEvent], list[bool]]:
    """A stable stream that undergoes a level shift (mean jumps by
    `shift_magnitude` and stays there) for `shift_len` samples starting at
    `shift_start`. Labels True for the entire shifted region.
    """
    rng = random.Random(seed)
    events: list[TelemetryEvent] = []
    labels: list[bool] = []
    for i in range(n):
        ts = _EPOCH + timedelta(seconds=i)
        in_shift = shift_start <= i < shift_start + shift_len
        value = mean + rng.gauss(0, sd) + (shift_magnitude if in_shift else 0.0)
        events.append(_event(metric_name, value, ts, i))
        labels.append(in_shift)
    return events, labels


def correlation_break(
    n: int = 300,
    mean: float = 50.0,
    sd: float = 1.0,
    break_start: int = 150,
    break_len: int = 40,
    break_magnitude: float = 30.0,
    seed: int = 5,
    metric_a_name: str = "metric_a",
    metric_b_name: str = "metric_b",
) -> tuple[list[TelemetryEvent], list[bool]]:
    """Two metrics that normally move together: metric_b tracks metric_a's
    noise term (b = mean + a's noise) sample-for-sample. For a `break_len`
    region starting at `break_start`, metric_b instead gets its own
    independent noise plus a level shift (`break_magnitude`) while metric_a
    stays on its normal track. Events for both metrics are interleaved per
    timestamp (metric_a then metric_b). Labels: only metric_b's events during
    the break region are True; metric_a is always False, and metric_b outside
    the break region is False.
    """
    rng = random.Random(seed)
    events: list[TelemetryEvent] = []
    labels: list[bool] = []
    for i in range(n):
        ts = _EPOCH + timedelta(seconds=i)
        a_noise = rng.gauss(0, sd)
        a_val = mean + a_noise
        in_break = break_start <= i < break_start + break_len
        if in_break:
            b_val = mean + break_magnitude + rng.gauss(0, sd)
        else:
            b_val = mean + a_noise  # tracks metric_a's noise term exactly
        events.append(_event(metric_a_name, a_val, ts, i))
        labels.append(False)
        events.append(_event(metric_b_name, b_val, ts, i))
        labels.append(in_break)
    return events, labels


# Registry so the CLI/tests can iterate "all scenarios" without hardcoding
# the function list in more than one place.
SCENARIOS: dict[str, Scenario] = {
    "normal_noise": normal_noise,
    "seasonal_cycle": seasonal_cycle,
    "point_anomaly": point_anomaly,
    "sustained_anomaly": sustained_anomaly,
    "correlation_break": correlation_break,
}
