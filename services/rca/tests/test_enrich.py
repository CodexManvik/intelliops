from datetime import UTC, datetime

from common.contracts import (
    EnrichmentContext,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
)
from services.rca.enrich import enrich

NOW = datetime(2026, 8, 13, tzinfo=UTC)


class FakeProvider:
    def recent_deploys(self):
        return [{"service": "web", "version": "v2", "ts": NOW.isoformat()}]

    def topology_for(self, labels):
        return {"web": ["db"]}

    def config_changes(self):
        return [{"key": "web.replicas", "ts": NOW.isoformat()}]


def _situation():
    return Situation(
        id="sit-1", status=SituationStatus.DETECTED,
        member_events=[TelemetryEvent(
            source="prom", kind=TelemetryKind.METRIC, name="cpu", value=99.0,
            labels={"service": "web"}, ts=NOW, fingerprint="fp",
        )],
        severity="high", first_seen=NOW, last_seen=NOW, signature="sig",
    )


def test_enrich_gathers_all_context():
    ctx = enrich(_situation(), FakeProvider())
    assert isinstance(ctx, EnrichmentContext)
    assert ctx.recent_deploys[0]["service"] == "web"
    assert ctx.topology == {"web": ["db"]}
    assert ctx.config_changes[0]["key"] == "web.replicas"
