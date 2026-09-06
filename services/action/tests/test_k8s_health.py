from datetime import UTC, datetime

from common.contracts import (
    RemediationTarget,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
)
from services.action.adapters.k8s_health import KubernetesHealthChecker
from services.correlation.detection_policy import DetectionPolicy

NOW = datetime(2026, 8, 18, tzinfo=UTC)


def _sit():
    return Situation(
        id="s",
        status=SituationStatus.ACTING,
        member_events=[],
        severity="high",
        first_seen=NOW,
        last_seen=NOW,
        signature="sig",
    )


def _sit_memory(value):
    return Situation(
        id="s",
        status=SituationStatus.ACTING,
        member_events=[
            TelemetryEvent(
                source="t",
                kind=TelemetryKind.METRIC,
                name="memory_usage_mb",
                value=value,
                ts=NOW,
                fingerprint="fp-mem",
            )
        ],
        severity="high",
        first_seen=NOW,
        last_seen=NOW,
        signature="sig",
        baseline={"memory_usage_mb": {"mean": 200.0, "std": 20.0}},
    )


def _tgt():
    return RemediationTarget(namespace="ns", deployment="demo-app")


class FakeExc(Exception):
    pass


class FakeApps:
    def __init__(self, ready, desired=1, fail=False):
        self._ready, self._desired, self._fail = ready, desired, fail

    def read_namespaced_deployment_status(self, name, namespace):
        if self._fail:
            raise FakeExc("boom")

        class _S:
            status = type("St", (), {"ready_replicas": self._ready, "replicas": self._desired})()

        return _S()


def _hc(apps, metric_ok, timeout=0.2):
    return KubernetesHealthChecker(
        apps_v1=apps,
        metric_healthy=lambda: metric_ok,
        timeout_seconds=timeout,
        poll_interval_seconds=0.0,
        exc_type=FakeExc,
    )


def test_both_signals_green_returns_true():
    assert _hc(FakeApps(ready=1, desired=1), metric_ok=True).check(_sit(), _tgt()) is True


def test_pod_ready_but_metric_bad_times_out_false():
    assert _hc(FakeApps(ready=1, desired=1), metric_ok=False).check(_sit(), _tgt()) is False


def test_pod_not_ready_times_out_false():
    assert _hc(FakeApps(ready=0, desired=1), metric_ok=True).check(_sit(), _tgt()) is False


def test_api_error_does_not_raise_times_out_false():
    assert _hc(FakeApps(ready=1, fail=True), metric_ok=True).check(_sit(), _tgt()) is False


def test_check_builds_per_metric_predicate_from_situation():
    # memory_usage_mb=300, baseline mean 200 std 20 -> z=5 > 3 -> still anomalous ->
    # NOT recovered -> check() False even though pods are ready.
    calls = []

    def query_value(name):
        calls.append(name)
        return 300.0

    checker = KubernetesHealthChecker(
        apps_v1=FakeApps(ready=1),
        policy=DetectionPolicy(enabled=True),
        query_value=query_value,
        z_threshold=3.0,
        timeout_seconds=0.1,
        poll_interval_seconds=0.01,
        exc_type=FakeExc,
    )
    assert checker.check(_sit_memory(300.0), _tgt()) is False
    assert "memory_usage_mb" in calls  # queried the firing metric, NOT cpu_usage


def test_check_healthy_when_metric_recovered():
    # query returns 205 -> z=0.25 < 3 -> recovered -> pods ready -> True.
    checker = KubernetesHealthChecker(
        apps_v1=FakeApps(ready=1),
        policy=DetectionPolicy(enabled=True),
        query_value=lambda name: 205.0,
        z_threshold=3.0,
        timeout_seconds=0.1,
        poll_interval_seconds=0.01,
        exc_type=FakeExc,
    )
    assert checker.check(_sit_memory(205.0), _tgt()) is True


def test_back_compat_injected_metric_healthy_still_used():
    # no policy/query_value -> falls back to the injected metric_healthy (today's behavior).
    checker = KubernetesHealthChecker(
        apps_v1=FakeApps(ready=1),
        metric_healthy=lambda: False,
        timeout_seconds=0.1,
        poll_interval_seconds=0.01,
        exc_type=FakeExc,
    )
    assert checker.check(_sit(), _tgt()) is False  # injected predicate honored
