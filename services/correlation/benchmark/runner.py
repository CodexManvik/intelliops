"""Score a correlator against a labeled scenario's ground truth.

`run_scenario` feeds a scenario's events through a correlator's `is_anomaly()`
one at a time (the same call pattern the live consumer uses) and compares the
per-event predictions to the ground-truth labels, producing precision,
recall, false-positive-rate, and mean detection latency.

TrainedCorrelator needs a warm-up + fit() pass before it does anything beyond
what its composed RobustCorrelator would do on its own (see
adapters/trained_correlator.py's cold-start note): `run_scenario` detects a
correlator with a callable `.fit()` and, when `warm_and_fit=True` (the
default), feeds it a warm-up slice of the *same* events, calls `fit()`, then
re-scores the full stream from a matching fresh correlator's baseline state.
Concretely this means: build one correlator instance, run it once over the
whole stream to populate `_features`/robust windows, call `fit()`, then score
a SECOND pass over the same events with fit() already applied — so every
scored prediction benefits from the fitted model, not just the tail. This
mirrors "train on history, then serve" rather than "learn while serving,"
which is the realistic deployment shape for the trained kind.
"""

from __future__ import annotations

from dataclasses import dataclass

from common.contracts import TelemetryEvent
from services.correlation.adapters.base_correlator import BaseCorrelator


@dataclass(frozen=True)
class ScenarioResult:
    correlator_kind: str
    scenario_name: str
    n_events: int
    n_anomalies: int
    true_positives: int
    false_positives: int
    true_negatives: int
    false_negatives: int
    precision: float
    recall: float
    false_positive_rate: float
    detection_latency: (
        float | None
    )  # mean events-from-onset-to-first-flag; None if no anomaly regions

    def as_dict(self) -> dict:
        return {
            "correlator_kind": self.correlator_kind,
            "scenario_name": self.scenario_name,
            "n_events": self.n_events,
            "n_anomalies": self.n_anomalies,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "true_negatives": self.true_negatives,
            "false_negatives": self.false_negatives,
            "precision": self.precision,
            "recall": self.recall,
            "false_positive_rate": self.false_positive_rate,
            "detection_latency": self.detection_latency,
        }


def _anomaly_regions(labels: list[bool]) -> list[tuple[int, int]]:
    """Contiguous True runs in `labels` as [start, end) index pairs."""
    regions: list[tuple[int, int]] = []
    start: int | None = None
    for i, lab in enumerate(labels):
        if lab and start is None:
            start = i
        elif not lab and start is not None:
            regions.append((start, i))
            start = None
    if start is not None:
        regions.append((start, len(labels)))
    return regions


def _detection_latency(labels: list[bool], predictions: list[bool]) -> float | None:
    """Mean, over each contiguous anomaly region, of (first flagged index
    within the region) - (region start index). A region never flagged counts
    as latency == len(region) (the detector never caught it within the
    anomaly's own duration) so a silent miss still penalizes the average
    rather than being dropped from it. Returns None if there are no anomaly
    regions in this scenario (nothing to measure latency against)."""
    regions = _anomaly_regions(labels)
    if not regions:
        return None
    latencies: list[int] = []
    for start, end in regions:
        first_flag = next((i for i in range(start, end) if predictions[i]), None)
        latencies.append((first_flag - start) if first_flag is not None else (end - start))
    return sum(latencies) / len(latencies)


def score_predictions(
    labels: list[bool],
    predictions: list[bool],
    correlator_kind: str,
    scenario_name: str,
) -> ScenarioResult:
    """Compare `predictions` (per-event is_anomaly() calls) to `labels`
    (ground truth) and compute the metrics. Split out from run_scenario so
    tests can exercise the metrics math directly without a real correlator.
    """
    assert len(labels) == len(predictions), "labels/predictions length mismatch"
    tp = fp = tn = fn = 0
    for lab, pred in zip(labels, predictions, strict=True):
        if pred and lab:
            tp += 1
        elif pred and not lab:
            fp += 1
        elif not pred and lab:
            fn += 1
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    fpr = fp / (fp + tn) if (fp + tn) else 0.0
    latency = _detection_latency(labels, predictions)

    return ScenarioResult(
        correlator_kind=correlator_kind,
        scenario_name=scenario_name,
        n_events=len(labels),
        n_anomalies=sum(labels),
        true_positives=tp,
        false_positives=fp,
        true_negatives=tn,
        false_negatives=fn,
        precision=precision,
        recall=recall,
        false_positive_rate=fpr,
        detection_latency=latency,
    )


def run_scenario(
    correlator: BaseCorrelator,
    events: list[TelemetryEvent],
    labels: list[bool],
    correlator_kind: str,
    scenario_name: str,
    warm_and_fit: bool = True,
) -> ScenarioResult:
    """Run `correlator` over `events` in order, calling is_anomaly() per
    event, and score the predictions against `labels`.

    If `correlator` exposes a callable `.fit()` (TrainedCorrelator) and
    `warm_and_fit` is True, it is first fed the full event stream once (to
    populate its feature buffer / robust windows), fit() is called, and THEN
    a fresh scoring pass over the same events is what gets measured — so the
    fitted model is in effect for every scored prediction, not just events
    after some in-stream fit point.
    """
    fit = getattr(correlator, "fit", None)
    if warm_and_fit and callable(fit):
        for event in events:
            correlator.detect(event)
        fit()

    predictions = [correlator.is_anomaly(event) for event in events]
    return score_predictions(labels, predictions, correlator_kind, scenario_name)
