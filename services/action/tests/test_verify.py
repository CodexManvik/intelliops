from datetime import UTC, datetime

from common.contracts import Situation, SituationStatus, TelemetryEvent, TelemetryKind
from services.action.verify import build_metric_healthy
from services.correlation.detection_policy import DetectionPolicy

NOW = datetime(2026, 9, 6, tzinfo=UTC)


def _sit(metrics, baseline=None):
    """metrics: list of (name, value) that fired. baseline: {name: {mean, std}}."""
    events = [
        TelemetryEvent(
            source="test",
            kind=TelemetryKind.METRIC,
            name=n,
            value=v,
            ts=NOW,
            fingerprint=f"fp-{n}",
        )
        for n, v in metrics
    ]
    return Situation(
        id="sit-x",
        status=SituationStatus.ACTING,
        member_events=events,
        severity="high",
        first_seen=NOW,
        last_seen=NOW,
        signature="sig-x",
        baseline=baseline,
    )


ON = DetectionPolicy(
    enabled=True
)  # policy thresholds: ratio 0.02, sat% 90, sat_ratio 0.80, latency 500


def test_ratio_recovered_is_healthy():
    # error_rate now 0.005 < 0.02 threshold -> recovered -> healthy. No baseline needed.
    sit = _sit([("error_rate", 0.005)])
    healthy = build_metric_healthy(sit, lambda name: 0.005, ON)
    assert healthy() is True


def test_ratio_still_high_is_not_healthy():
    sit = _sit([("error_rate", 0.09)])
    healthy = build_metric_healthy(sit, lambda name: 0.09, ON)
    assert healthy() is False


def test_saturation_cpu_recovered_by_policy_cutoff():
    # cpu_usage 40 < 90 (percent-scale saturation cutoff) -> recovered. Note: the OLD
    # hardcoded check was <50; the policy cutoff is 90 -> intended shift.
    sit = _sit([("cpu_usage", 40.0)])
    healthy = build_metric_healthy(sit, lambda name: 40.0, ON)
    assert healthy() is True


def test_latency_recovered_via_ceiling_without_baseline():
    # latency 300ms < 500 ceiling, no baseline -> ceiling-only judges it recovered
    # (NOT fail-safed, because latency has an absolute rule).
    sit = _sit([("latency_p99_ms", 300.0)], baseline=None)
    healthy = build_metric_healthy(sit, lambda name: 300.0, ON)
    assert healthy() is True


def test_latency_breaching_ceiling_is_not_healthy():
    sit = _sit([("latency_p99_ms", 700.0)], baseline=None)
    healthy = build_metric_healthy(sit, lambda name: 700.0, ON)
    assert healthy() is False


def test_default_metric_recovered_via_zscore_with_baseline():
    # memory_usage_mb is 'default' kind (pure z-score). current 210, baseline mean 200 std 20
    # -> z = 0.5 < 3.0 -> not anomalous -> recovered.
    sit = _sit(
        [("memory_usage_mb", 210.0)], baseline={"memory_usage_mb": {"mean": 200.0, "std": 20.0}}
    )
    healthy = build_metric_healthy(sit, lambda name: 210.0, ON, z_threshold=3.0)
    assert healthy() is True


def test_default_metric_still_high_via_zscore():
    # current 300, mean 200 std 20 -> z = 5.0 > 3.0 -> still anomalous -> not recovered.
    sit = _sit(
        [("memory_usage_mb", 300.0)], baseline={"memory_usage_mb": {"mean": 200.0, "std": 20.0}}
    )
    healthy = build_metric_healthy(sit, lambda name: 300.0, ON, z_threshold=3.0)
    assert healthy() is False


def test_default_metric_missing_baseline_fails_safe():
    # memory_usage_mb (default kind) with NO baseline -> cannot prove recovery -> not healthy.
    sit = _sit([("memory_usage_mb", 210.0)], baseline=None)
    healthy = build_metric_healthy(sit, lambda name: 210.0, ON)
    assert healthy() is False


def test_default_metric_zero_std_baseline_fails_safe():
    # Still 420 against a flat baseline of 200: the metric has NOT come back, so
    # this stays False - but now because it genuinely has not recovered, not
    # because a zero-variance baseline was treated as unusable.
    #
    # This used to assert the same thing at 210. A flat baseline has no spread
    # to normalise by, so verify now requires a departure of at least half the
    # baseline before calling a metric unrecovered, matching the rule
    # RobustCorrelator applies when deciding whether to open the incident at
    # all. 210 against 200 is a 5% tick, which the detector would never have
    # flagged, so verification must not fail the fix over it either.
    sit = _sit(
        [("memory_usage_mb", 420.0)], baseline={"memory_usage_mb": {"mean": 200.0, "std": 0.0}}
    )
    healthy = build_metric_healthy(sit, lambda name: 420.0, ON)
    assert healthy() is False


def test_zero_std_baseline_small_variation_counts_as_recovered():
    """The mirror of RobustCorrelator's flat-baseline floor.

    A metric the simulator had pinned to a constant reports 17.34 once it starts
    emitting real idle variation. The detector does not call that anomalous, so
    verification must not report a working fix as a rollback because of it.
    """
    sit = _sit([("cpu_usage", 92.0)], baseline={"cpu_usage": {"mean": 18.0, "std": 0.0}})
    healthy = build_metric_healthy(sit, lambda name: 17.34, ON)
    assert healthy() is True


def test_zero_std_baseline_back_at_the_mean_IS_recovered():
    """A perfectly flat baseline must still be verifiable.

    `service_up` is pinned at exactly 1.0, so its baseline std is 0. Treating
    std <= 0 as "no usable baseline" made the metric unverifiable forever: every
    restart-pod remediation reported `rolled_back` on a real cluster even though
    the pod had restarted and service_up had returned to 1.
    """
    sit = _sit([("service_up", 0.0)], baseline={"service_up": {"mean": 1.0, "std": 0.0}})
    assert build_metric_healthy(sit, lambda name: 1.0, ON)() is True


def test_zero_std_baseline_still_down_is_not_recovered():
    sit = _sit([("service_up", 0.0)], baseline={"service_up": {"mean": 1.0, "std": 0.0}})
    assert build_metric_healthy(sit, lambda name: 0.0, ON)() is False


def test_metric_still_far_BELOW_baseline_is_not_recovered():
    """The false-success case: a downward anomaly must not read as recovered.

    _baseline_score returns a SIGNED z and DetectionPolicy.is_anomaly's default
    branch asks `score > z_threshold`, which only catches upward movement. The
    correlators score on magnitude, so detection saw service_up fall 1 -> 0
    while verification computed -20.0, concluded -20 > 3 was False, and called a
    service that was STILL DOWN successfully remediated.
    """
    sit = _sit([("service_up", 0.0)], baseline={"service_up": {"mean": 1.0, "std": 0.05}})
    assert build_metric_healthy(sit, lambda name: 0.0, ON)() is False
    # and the genuine recovery is still recognised
    assert build_metric_healthy(sit, lambda name: 1.0, ON)() is True


def test_all_firing_metrics_must_recover():
    # dependency_outage: error_rate recovered (0.005) BUT latency still high (700) -> NOT healthy.
    sit = _sit([("error_rate", 0.005), ("latency_p99_ms", 700.0)])
    values = {"error_rate": 0.005, "latency_p99_ms": 700.0}
    healthy = build_metric_healthy(sit, lambda name: values.get(name), ON)
    assert healthy() is False


def test_all_recovered_is_healthy():
    sit = _sit([("error_rate", 0.005), ("latency_p99_ms", 300.0)])
    values = {"error_rate": 0.005, "latency_p99_ms": 300.0}
    healthy = build_metric_healthy(sit, lambda name: values.get(name), ON)
    assert healthy() is True


def test_query_returns_none_fails_safe():
    sit = _sit([("cpu_usage", 40.0)])
    healthy = build_metric_healthy(sit, lambda name: None, ON)
    assert healthy() is False


def test_query_raises_fails_safe():
    def boom(name):
        raise RuntimeError("prometheus down")

    sit = _sit([("cpu_usage", 40.0)])
    healthy = build_metric_healthy(sit, boom, ON)
    assert healthy() is False  # never raises


def test_no_metric_events_is_vacuously_healthy():
    # a situation whose only events are value-None (log/trace) -> nothing to verify -> True.
    ev = TelemetryEvent(
        source="t", kind=TelemetryKind.LOG, name="app.error", value=None, ts=NOW, fingerprint="fp-l"
    )
    sit = Situation(
        id="s",
        status=SituationStatus.ACTING,
        member_events=[ev],
        severity="high",
        first_seen=NOW,
        last_seen=NOW,
        signature="sg",
        baseline=None,
    )
    healthy = build_metric_healthy(sit, lambda name: None, ON)
    assert healthy() is True


def test_policy_off_needs_baseline_for_every_metric():
    # detection_policy off -> pure z-score for all. cpu with no baseline -> fail-safe not-recovered.
    off = DetectionPolicy(enabled=False)
    sit = _sit([("cpu_usage", 40.0)], baseline=None)
    healthy = build_metric_healthy(sit, lambda name: 40.0, off, z_threshold=3.0)
    assert healthy() is False  # off-policy treats cpu as score-rule; no baseline -> not provable


def test_malformed_baseline_value_does_not_raise():
    # baseline value is a string, not a {mean,std} dict -> treated as no baseline,
    # metric_healthy() must NOT raise. For a default-kind metric that means not-recovered.
    sit = _sit([("memory_usage_mb", 210.0)], baseline={"memory_usage_mb": "oops-not-a-dict"})
    healthy = build_metric_healthy(sit, lambda name: 210.0, ON)
    assert healthy() is False  # no usable baseline -> fail-safe, and crucially: no exception


def test_metric_query_is_scoped_to_the_incidents_service():
    """The metric must be verified for the service the incident is about.

    query_value used to run the bare metric name and take max() across every
    series, so service_up's max over four Meridian services was 1.0 whenever any
    one of them was up - a still-down service verified as recovered.
    """
    seen: list[tuple] = []

    def q(name, service=None):
        seen.append((name, service))
        return 1.0

    ev = TelemetryEvent(
        source="test",
        kind=TelemetryKind.METRIC,
        name="service_up",
        value=0.0,
        ts=NOW,
        fingerprint="fp-service_up",
        labels={"service": "meridian-validation"},
    )
    sit = Situation(
        id="sit-scope",
        status=SituationStatus.ACTING,
        member_events=[ev],
        severity="high",
        first_seen=NOW,
        last_seen=NOW,
        signature="sig-scope",
        baseline={"service_up": {"mean": 1.0, "std": 0.0}},
    )
    build_metric_healthy(sit, q, ON)()
    assert seen == [("service_up", "meridian-validation")]


def test_one_argument_query_value_still_works():
    """The documented contract is Callable[[str], float | None]; honour it."""
    sit = _sit([("service_up", 0.0)], baseline={"service_up": {"mean": 1.0, "std": 0.0}})
    # _sit builds events with no labels, and this lambda takes only a name.
    assert build_metric_healthy(sit, lambda name: 1.0, ON)() is True
