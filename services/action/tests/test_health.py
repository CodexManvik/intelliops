from datetime import UTC, datetime

from common.contracts import (
    RemediationTarget,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
)
from common.interfaces import HealthChecker
from services.action.adapters.health import AlwaysHealthyChecker, FixedHealthChecker

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _situation():
    return Situation(
        id="s1",
        status=SituationStatus.DIAGNOSED,
        member_events=[
            TelemetryEvent(
                source="p",
                kind=TelemetryKind.METRIC,
                name="cpu",
                value=1.0,
                labels={},
                ts=NOW,
                fingerprint="f",
            )
        ],
        severity="high",
        first_seen=NOW,
        last_seen=NOW,
        signature="sig",
    )


def _target():
    return RemediationTarget(namespace="ns", deployment="demo-app")


def test_always_healthy():
    c = AlwaysHealthyChecker()
    assert isinstance(c, HealthChecker)
    assert c.check(_situation(), _target()) is True


def test_fixed_health_checker():
    from datetime import UTC, datetime

    from common.contracts import RemediationTarget, Situation, SituationStatus
    from services.action.adapters.health import FixedHealthChecker

    now = datetime(2026, 8, 18, tzinfo=UTC)
    sit = Situation(
        id="s",
        status=SituationStatus.ACTING,
        member_events=[],
        severity="high",
        first_seen=now,
        last_seen=now,
        signature="sig",
    )
    tgt = RemediationTarget(namespace="ns", deployment="demo-app")
    assert FixedHealthChecker(True).check(sit, tgt) is True
    assert FixedHealthChecker(False).check(sit, tgt) is False


def test_fixed_satisfies_protocol():
    assert isinstance(FixedHealthChecker(healthy=True), HealthChecker)


def test_make_health_checker_k8s_builds_per_metric():
    from services.action.adapters.k8s_health import KubernetesHealthChecker
    from services.action.app import _make_health_checker

    class S:
        health_check_mode = "k8s"
        detection_policy = "on"
        detection_ratio_threshold = 0.02
        detection_saturation_ratio_threshold = 0.80
        detection_saturation_percent_threshold = 90.0
        detection_latency_ceiling_ms = 500.0
        correlation_z_threshold = 3.0
        prometheus_url = "http://prom:9090"

    checker = _make_health_checker(S())
    assert isinstance(checker, KubernetesHealthChecker)
    assert checker._policy is not None
    assert checker._policy.enabled is True
    assert checker._query_value is not None
    assert checker._z_threshold == 3.0


def test_make_health_checker_always_is_always_healthy():
    from services.action.app import _make_health_checker

    class S:
        health_check_mode = "always"

    assert isinstance(_make_health_checker(S()), AlwaysHealthyChecker)
