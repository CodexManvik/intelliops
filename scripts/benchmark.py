#!/usr/bin/env python3
"""Run every correlator kind against every benchmark scenario and print a
markdown table of precision/recall/false-positive-rate/detection-latency.

Usage:
    uv run python scripts/benchmark.py

This is the harness Task 6 (BENCHMARKS.md) runs to get real numbers — it is
deliberately NOT part of the pytest suite (test_benchmark.py uses small,
fast, CI-enforced scenario sizes instead) so this script is free to run
larger, more statistically representative scenario sizes for reporting.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as `uv run python scripts/benchmark.py` from the repo root
# without requiring the package to be installed in editable mode.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.correlation.adapters.river_correlator import RiverCorrelator
from services.correlation.adapters.robust_correlator import RobustCorrelator
from services.correlation.adapters.trained_correlator import TrainedCorrelator
from services.correlation.benchmark import scenarios
from services.correlation.benchmark.runner import ScenarioResult, run_scenario

Z_THRESHOLD = 3.0
RIVER_WARMUP = 50
ROBUST_WARMUP = 30

# Reporting-sized scenarios: bigger than the CI test's fast/small ones so the
# printed numbers are more statistically meaningful, while still finishing in
# well under a minute.
SCENARIO_KWARGS: dict[str, dict] = {
    "normal_noise": {"n": 1000},
    "seasonal_cycle": {"n_hours": 96, "per_hour": 12},
    "point_anomaly": {"n": 1000, "spike_every": 80, "warmup": 60},
    "sustained_anomaly": {"n": 1000, "shift_start": 400, "shift_len": 100},
    "correlation_break": {"n": 800, "break_start": 350, "break_len": 100},
}


def make_correlators() -> dict[str, object]:
    return {
        "river": RiverCorrelator(z_threshold=Z_THRESHOLD, warmup_samples=RIVER_WARMUP),
        "robust": RobustCorrelator(z_threshold=Z_THRESHOLD, warmup_samples=ROBUST_WARMUP),
        "trained": TrainedCorrelator(
            z_threshold=Z_THRESHOLD, warmup_samples=ROBUST_WARMUP, min_fit_samples=200
        ),
    }


def run_all() -> list[ScenarioResult]:
    results: list[ScenarioResult] = []
    for scenario_name, fn in scenarios.SCENARIOS.items():
        kwargs = SCENARIO_KWARGS.get(scenario_name, {})
        events, labels = fn(**kwargs)
        for kind, correlator in make_correlators().items():
            result = run_scenario(correlator, events, labels, kind, scenario_name)
            results.append(result)
    return results


def format_latency(latency: float | None) -> str:
    return "n/a" if latency is None else f"{latency:.1f}"


def to_markdown_table(results: list[ScenarioResult]) -> str:
    header = (
        "| Scenario | Correlator | N | Anomalies | Precision | Recall | FPR | Detection Latency |"
    )
    divider = "|---|---|---|---|---|---|---|---|"
    lines = [header, divider]
    for r in results:
        lines.append(
            f"| {r.scenario_name} | {r.correlator_kind} | {r.n_events} | {r.n_anomalies} "
            f"| {r.precision:.3f} | {r.recall:.3f} | {r.false_positive_rate:.3f} "
            f"| {format_latency(r.detection_latency)} |"
        )
    return "\n".join(lines)


def main() -> None:
    results = run_all()
    print(to_markdown_table(results))


if __name__ == "__main__":
    main()
