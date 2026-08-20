from datetime import UTC, datetime

from common.contracts import Situation, SituationStatus, TelemetryEvent, TelemetryKind
from services.action.targets import resolve_target

NOW = datetime(2026, 8, 18, tzinfo=UTC)


def _sit(service):
    labels = {"service": service} if service else {}
    return Situation(
        id="s",
        status=SituationStatus.DIAGNOSED,
        member_events=[
            TelemetryEvent(
                source="p",
                kind=TelemetryKind.METRIC,
                name="cpu_usage",
                value=90.0,
                labels=labels,
                ts=NOW,
                fingerprint="f",
            )
        ],
        severity="high",
        first_seen=NOW,
        last_seen=NOW,
        signature="sig",
    )


def test_resolve_target_from_service_label():
    t = resolve_target(_sit("demo-app"), namespace="intelliops-demo")
    assert t.namespace == "intelliops-demo" and t.deployment == "demo-app"


def test_resolve_target_unknown_when_no_label():
    t = resolve_target(_sit(None), namespace="intelliops-demo")
    assert t.deployment == "unknown"
