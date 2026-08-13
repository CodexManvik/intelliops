from datetime import UTC, datetime

from common.contracts import Situation, SituationStatus, TelemetryEvent, TelemetryKind
from common.interfaces import HealthChecker
from services.action.adapters.health import AlwaysHealthyChecker, FixedHealthChecker

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _situation():
    return Situation(
        id="s1", status=SituationStatus.DIAGNOSED,
        member_events=[TelemetryEvent(source="p", kind=TelemetryKind.METRIC, name="cpu",
                                      value=1.0, labels={}, ts=NOW, fingerprint="f")],
        severity="high", first_seen=NOW, last_seen=NOW, signature="sig",
    )


def test_always_healthy():
    c = AlwaysHealthyChecker()
    assert isinstance(c, HealthChecker)
    assert c.check(_situation()) is True


def test_fixed_health_checker():
    assert FixedHealthChecker(healthy=True).check(_situation()) is True
    assert FixedHealthChecker(healthy=False).check(_situation()) is False


def test_fixed_satisfies_protocol():
    assert isinstance(FixedHealthChecker(healthy=True), HealthChecker)
