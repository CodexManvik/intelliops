"""Record the detector benchmark: the streams, their labels, and what each
Python detector flagged.

    uv run python rust/harness/record_benchmark.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from benchmark import RIVER_WARMUP, ROBUST_WARMUP, SCENARIO_KWARGS, Z_THRESHOLD

from services.correlation.adapters.river_correlator import RiverCorrelator
from services.correlation.adapters.robust_correlator import RobustCorrelator
from services.correlation.adapters.trained_correlator import TrainedCorrelator
from services.correlation.benchmark import scenarios
from services.correlation.benchmark.runner import run_scenario

MAKE = {
    "river": lambda: RiverCorrelator(z_threshold=Z_THRESHOLD, warmup_samples=RIVER_WARMUP),
    "robust": lambda: RobustCorrelator(z_threshold=Z_THRESHOLD, warmup_samples=ROBUST_WARMUP),
    "trained": lambda: TrainedCorrelator(z_threshold=Z_THRESHOLD, warmup_samples=ROBUST_WARMUP),
}


def main() -> None:
    out = []
    for name, make_events in scenarios.SCENARIOS.items():
        events, labels = make_events(**SCENARIO_KWARGS.get(name, {}))
        results = {
            kind: run_scenario(make(), events, labels, kind, name).as_dict()
            for kind, make in MAKE.items()
        }
        out.append(
            {
                "scenario": name,
                "events": [[e.name, e.value, e.ts.isoformat()] for e in events],
                "labels": [int(x) for x in labels],
                "results": results,
            }
        )
        for kind, r in results.items():
            print(
                f"{name:18} {kind:8} tp={r['true_positives']:3} fp={r['false_positives']:3}"
                f" fpr={r['false_positive_rate']:.3f} recall={r['recall']:.3f}"
            )
    path = ROOT / "rust" / "app-cases" / "benchmark.json"
    path.write_text(json.dumps(out, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
