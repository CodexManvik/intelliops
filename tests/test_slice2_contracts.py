from datetime import UTC, datetime

from common.config import get_settings
from common.contracts import (
    DiagnosedSituation,
    EnrichmentContext,
    RootCauseHypothesis,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
)
from common.interfaces import ContextProvider, PlaybookStore

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _situation():
    return Situation(
        id="sit-1", status=SituationStatus.DETECTED,
        member_events=[TelemetryEvent(
            source="prom", kind=TelemetryKind.METRIC, name="cpu", value=99.0,
            labels={"service": "web"}, ts=NOW, fingerprint="fp",
        )],
        severity="high", first_seen=NOW, last_seen=NOW, signature="sig",
    )


def test_enrichment_context_defaults_empty():
    ctx = EnrichmentContext()
    assert ctx.recent_deploys == []
    assert ctx.topology == {}
    assert ctx.config_changes == []


def test_diagnosed_situation_roundtrips():
    d = DiagnosedSituation(
        situation=_situation(),
        hypotheses=[RootCauseHypothesis(
            situation_id="sit-1", description="recent deploy", confidence=0.8,
            evidence=["deploy web@v2"], suggested_runbook_id="rollback-deploy",
        )],
        suggested_runbook_id="rollback-deploy",
    )
    restored = DiagnosedSituation.model_validate(d.model_dump())
    assert restored == d
    assert restored.hypotheses[0].confidence == 0.8


def test_protocols_are_runtime_checkable():
    class FakeStore:
        def register(self, playbook): ...
        def get(self, playbook_id): return None
        def list(self): return []

    class FakeProvider:
        def recent_deploys(self): return []
        def topology_for(self, labels): return {}
        def config_changes(self): return []

    assert isinstance(FakeStore(), PlaybookStore)
    assert isinstance(FakeProvider(), ContextProvider)


def test_settings_have_slice2_paths():
    s = get_settings()
    assert s.audit_store_path.endswith(".jsonl")
    assert isinstance(s.playbook_store_path, str)
    assert s.rbac_policy_path.endswith(".yaml")
    assert isinstance(s.rca_context_path, str)
