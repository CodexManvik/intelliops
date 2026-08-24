"""Benchmark harness tests: scenario determinism + the CI-enforced detector win.

CI-ENFORCED METRIC: on `seasonal_cycle`, RobustCorrelator's false_positive_rate
is strictly less than RiverCorrelator's. This is a REAL measured win, not a
tautology: `seasonal_cycle` (services/correlation/benchmark/scenarios.py) is a
daily pattern where a metric plateaus higher during a few peak hours-of-day,
repeated over many days — a normal recurring pattern, so every label is
False. RiverCorrelator has no notion of hour-of-day; it keeps one running
mean/variance per metric, so the (common enough to be "normal", rare enough
in the full stream to look extreme) peak-hour plateau crosses its z-threshold
and it genuinely false-positives. RobustCorrelator buckets its median/MAD
baseline by hour-of-day, so each hour's own tight local distribution is
compared against itself and it does not false-positive on the same stream.
Both correlators see the exact same events in the exact same order — only the
detector differs — so a passing test here is proof of a genuine algorithmic
improvement, not a scenario rigged to only one side's advantage.
"""

from __future__ import annotations

from services.correlation.adapters.river_correlator import RiverCorrelator
from services.correlation.adapters.robust_correlator import RobustCorrelator
from services.correlation.benchmark import scenarios
from services.correlation.benchmark.runner import (
    ScenarioResult,
    _anomaly_regions,
    _detection_latency,
    run_scenario,
    score_predictions,
)

# --- scenario determinism + label sanity ------------------------------------


def test_same_seed_produces_identical_event_streams():
    """Same seed -> identical events (values, timestamps, fingerprints) and
    identical labels, across every scenario generator."""
    for name, fn in scenarios.SCENARIOS.items():
        events_a, labels_a = fn()
        events_b, labels_b = fn()
        assert labels_a == labels_b, f"{name}: labels differ across identical calls"
        assert len(events_a) == len(events_b), f"{name}: event count differs"
        for ea, eb in zip(events_a, events_b, strict=True):
            assert ea.value == eb.value
            assert ea.ts == eb.ts
            assert ea.name == eb.name
            assert ea.fingerprint == eb.fingerprint


def test_different_seeds_produce_different_streams():
    """Sanity check that the generators are not accidentally seed-invariant
    (e.g. ignoring the seed argument entirely)."""
    for name, fn in scenarios.SCENARIOS.items():
        events_a, _ = fn(seed=100)
        events_b, _ = fn(seed=101)
        values_a = [e.value for e in events_a]
        values_b = [e.value for e in events_b]
        assert values_a != values_b, f"{name}: seed 100 and 101 produced identical values"


def test_labels_line_up_with_events():
    for name, fn in scenarios.SCENARIOS.items():
        events, labels = fn()
        assert len(events) == len(labels), f"{name}: events/labels length mismatch"


def test_normal_noise_and_seasonal_cycle_are_all_normal():
    """These two scenarios represent normal behavior end-to-end: no event is
    ground-truth anomalous."""
    for name in ("normal_noise", "seasonal_cycle"):
        _, labels = scenarios.SCENARIOS[name]()
        assert not any(labels), f"{name}: expected all-False labels"


def test_point_anomaly_and_sustained_anomaly_have_some_true_labels():
    for name in ("point_anomaly", "sustained_anomaly"):
        _, labels = scenarios.SCENARIOS[name]()
        assert any(labels), f"{name}: expected at least one True label"
        assert not all(labels), f"{name}: expected at least one False label"


def test_correlation_break_labels_only_metric_b_during_break():
    events, labels = scenarios.correlation_break()
    for event, label in zip(events, labels, strict=True):
        if event.name == "metric_a":
            assert label is False
    assert any(
        label for event, label in zip(events, labels, strict=True) if event.name == "metric_b"
    )


# --- runner metrics math -----------------------------------------------------


def test_score_predictions_perfect_detector():
    labels = [False, False, True, True, False]
    predictions = [False, False, True, True, False]
    result = score_predictions(labels, predictions, "perfect", "toy")
    assert result.precision == 1.0
    assert result.recall == 1.0
    assert result.false_positive_rate == 0.0


def test_score_predictions_all_false_positive():
    labels = [False, False, False]
    predictions = [True, True, True]
    result = score_predictions(labels, predictions, "trigger-happy", "toy")
    assert result.precision == 0.0
    assert result.false_positive_rate == 1.0
    # no ground-truth anomalies -> recall is defined as 0.0 (no TP possible)
    assert result.recall == 0.0


def test_anomaly_regions_finds_contiguous_runs():
    labels = [False, True, True, False, False, True, False]
    assert _anomaly_regions(labels) == [(1, 3), (5, 6)]


def test_anomaly_regions_region_touching_end():
    labels = [False, False, True, True]
    assert _anomaly_regions(labels) == [(2, 4)]


def test_detection_latency_measures_onset_to_first_flag():
    labels = [False, False, True, True, True, False]
    predictions = [False, False, False, True, False, False]
    # region is [2,5); first flag inside region is index 3 -> latency 1
    assert _detection_latency(labels, predictions) == 1.0


def test_detection_latency_none_when_no_anomalies():
    labels = [False, False, False]
    predictions = [False, False, False]
    assert _detection_latency(labels, predictions) is None


def test_detection_latency_missed_region_counts_as_full_region_length():
    labels = [False, True, True, True, False]
    predictions = [False, False, False, False, False]  # never flagged
    # region [1,4), never flagged -> latency == region length == 3
    assert _detection_latency(labels, predictions) == 3.0


# --- the CI-enforced win ------------------------------------------------------


def test_robust_beats_river_false_positive_rate_on_seasonal_cycle():
    """THE CI-ENFORCED WIN. See module docstring for why this is a genuine,
    non-tautological improvement: RiverCorrelator's global (non-seasonal)
    baseline mistakes the recurring peak-hour plateau for an outlier;
    RobustCorrelator's per-hour-of-day baseline does not. Kept small so this
    stays fast (~500 events, well under a second)."""
    events, labels = scenarios.seasonal_cycle(n_hours=48, per_hour=10)
    assert not any(labels)  # sanity: this scenario is all-normal by construction

    river = RiverCorrelator(z_threshold=3.0, warmup_samples=50)
    robust = RobustCorrelator(z_threshold=3.0, warmup_samples=30)

    river_result = run_scenario(river, events, labels, "river", "seasonal_cycle")
    robust_result = run_scenario(robust, events, labels, "robust", "seasonal_cycle")

    # The baseline must actually false-positive here (otherwise this would be
    # a tautological "0 < 0" pass) — pin that river genuinely misfires first.
    assert river_result.false_positive_rate > 0.0, (
        "expected RiverCorrelator to false-positive on the seasonal plateau; "
        "if this fails the scenario params no longer exercise the baseline's "
        "known weakness and the comparison below would be tautological"
    )

    assert robust_result.false_positive_rate < river_result.false_positive_rate
    # And robust should be nearly clean on a purely seasonal, non-anomalous stream.
    assert robust_result.false_positive_rate <= 0.01


def test_scenario_result_as_dict_round_trips_fields():
    result = ScenarioResult(
        correlator_kind="river",
        scenario_name="toy",
        n_events=1,
        n_anomalies=0,
        true_positives=0,
        false_positives=0,
        true_negatives=1,
        false_negatives=0,
        precision=0.0,
        recall=0.0,
        false_positive_rate=0.0,
        detection_latency=None,
    )
    d = result.as_dict()
    assert d["correlator_kind"] == "river"
    assert d["detection_latency"] is None
