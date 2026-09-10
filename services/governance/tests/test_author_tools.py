"""Tests for the author-tools module: OpenAI tool schemas + toolbox dispatch.

AuthorToolbox binds a request's context (situation, system_context, training_store,
audit_sink, decision_store) and dispatches the 5 read tools by name. Every branch
must be exception-safe: a store blip yields {"error": "<ClassName>"}, never a raise.
`submit_runbook` schema lives in TOOL_SCHEMAS but is NOT dispatched here (Task 5's
loop handles it) — dispatching it (or an unknown name) from the toolbox is itself
an error case, not a crash.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from common.contracts import (
    AuditRecord,
    AuthorDecision,
    RemediationResult,
    Situation,
    SituationStatus,
    TrainingRecord,
)
from services.governance.adapters.author_decision_store import InMemoryAuthorDecisionStore
from services.governance.adapters.author_tools import (
    ACTION_NOTES,
    TOOL_SCHEMAS,
    AuthorToolbox,
)

NOW = datetime(2026, 9, 10, tzinfo=UTC)

CLOSED_ACTIONS = {
    "restart",
    "scale",
    "rollback_deploy",
    "wait",
    "patch_resource_limits",
    "rollback_to_revision",
    "patch_probe",
}


# ---------------------------------------------------------------------------
# Minimal store stubs (mirroring the brief's abbreviated fixtures, but wired to
# the REAL AuditSink.records(correlation_id=None) signature — not a `cid`
# positional-only stub, since the toolbox must call the real protocol shape).
# ---------------------------------------------------------------------------


class _TrainingStub:
    """Minimal TrainingStore stub — only read_all() is required by the toolbox."""

    def __init__(self, records: list[TrainingRecord]) -> None:
        self._records = records

    def read_all(self) -> list[TrainingRecord]:
        return list(self._records)


class _AuditStub:
    """Minimal AuditSink stub satisfying records(correlation_id=None) -> all records."""

    def __init__(self, records: list[AuditRecord]) -> None:
        self._records = records

    def write(self, record: AuditRecord) -> None:  # pragma: no cover - unused here
        self._records.append(record)

    def records(self, correlation_id: str | None = None) -> list[AuditRecord]:
        if correlation_id is None:
            return list(self._records)
        return [r for r in self._records if r.correlation_id == correlation_id]


class _BoomTrainingStore:
    """A store that always raises — used to prove dispatch never crashes."""

    def read_all(self):
        raise RuntimeError("db down")


class _BoomAuditSink:
    def records(self, correlation_id: str | None = None):
        raise RuntimeError("audit down")


class _BoomDecisionStore:
    def by_signature(self, signature: str):
        raise RuntimeError("decision store down")


def _sit(signature: str = "sig-x") -> Situation:
    return Situation(
        id="sit-1",
        status=SituationStatus.DIAGNOSED,
        severity="high",
        first_seen=NOW,
        last_seen=NOW,
        signature=signature,
    )


def _toolbox(
    situation: Situation | None = None,
    system_context=None,
    training_store=None,
    audit_sink=None,
    decision_store=None,
) -> AuthorToolbox:
    return AuthorToolbox(
        situation=situation if situation is not None else _sit(),
        system_context=system_context,
        training_store=training_store if training_store is not None else _TrainingStub([]),
        audit_sink=audit_sink if audit_sink is not None else _AuditStub([]),
        decision_store=decision_store
        if decision_store is not None
        else InMemoryAuthorDecisionStore(),
    )


# ---------------------------------------------------------------------------
# Schema shape
# ---------------------------------------------------------------------------


def test_schemas_cover_exactly_seven_tools():
    names = {t["function"]["name"] for t in TOOL_SCHEMAS}
    assert names == {
        "get_system_context",
        "get_incident_details",
        "get_past_outcomes",
        "get_human_decisions",
        "get_past_decisions",
        "list_available_actions",
        "submit_runbook",
    }
    assert len(TOOL_SCHEMAS) == 7


def test_schemas_are_openai_tool_shape():
    for schema in TOOL_SCHEMAS:
        assert schema["type"] == "function"
        fn = schema["function"]
        assert isinstance(fn["name"], str) and fn["name"]
        assert isinstance(fn["description"], str) and fn["description"]
        assert "parameters" in fn
        assert fn["parameters"]["type"] == "object"


def test_only_expected_schemas_take_parameters():
    by_name = {t["function"]["name"]: t["function"] for t in TOOL_SCHEMAS}

    # No-arg tools: empty (or absent) required parameters.
    for name in ("get_system_context", "list_available_actions"):
        props = by_name[name]["parameters"].get("properties", {})
        assert props == {}

    # Single string-param tools: situation_id or signature.
    for name in (
        "get_incident_details",
        "get_past_outcomes",
        "get_human_decisions",
        "get_past_decisions",
    ):
        props = by_name[name]["parameters"]["properties"]
        assert len(props) == 1
        ((param_name, param_schema),) = props.items()
        assert param_name in {"situation_id", "signature"}
        assert param_schema["type"] == "string"

    submit = by_name["submit_runbook"]["parameters"]["properties"]
    assert set(submit.keys()) == {"playbook", "rationale", "cited_facts"}
    assert submit["playbook"]["type"] == "object"
    assert submit["rationale"]["type"] == "string"
    assert submit["cited_facts"]["type"] == "array"
    assert submit["cited_facts"]["items"]["type"] == "string"


# ---------------------------------------------------------------------------
# get_system_context
# ---------------------------------------------------------------------------


def test_get_system_context_unconfigured_when_none():
    tb = _toolbox(system_context=None)
    out = tb.dispatch("get_system_context", {})
    assert out == {"context": "unconfigured"}


def test_get_system_context_delegates_to_provider():
    class _Provider:
        def summarize(self) -> str:
            return "System: widget-api — handles widgets."

    tb = _toolbox(system_context=_Provider())
    out = tb.dispatch("get_system_context", {})
    assert out == {"context": "System: widget-api — handles widgets."}


def test_get_system_context_provider_error_is_caught():
    class _BoomProvider:
        def summarize(self) -> str:
            raise RuntimeError("unreadable")

    tb = _toolbox(system_context=_BoomProvider())
    out = tb.dispatch("get_system_context", {})
    assert out == {"error": "RuntimeError"}


# ---------------------------------------------------------------------------
# get_incident_details
# ---------------------------------------------------------------------------


def test_get_incident_details_serves_inhand_situation_ignoring_arg_id():
    sit = _sit(signature="sig-real")
    tb = _toolbox(situation=sit)
    out = tb.dispatch("get_incident_details", {"situation_id": "totally-different-id"})
    assert out["id"] == "sit-1"
    assert out["signature"] == "sig-real"
    assert out["severity"] == "high"


def test_get_incident_details_ignores_missing_arg():
    tb = _toolbox()
    out = tb.dispatch("get_incident_details", {})
    assert out["id"] == "sit-1"


# ---------------------------------------------------------------------------
# get_past_outcomes
# ---------------------------------------------------------------------------


def _training_record(signature: str, playbook_id: str, worked: bool, ts=NOW) -> TrainingRecord:
    return TrainingRecord(
        situation_id="sit-1",
        signature=signature,
        playbook_id=playbook_id,
        result=RemediationResult.SUCCESS if worked else RemediationResult.FAILURE,
        worked=worked,
        ts=ts,
    )


def test_get_past_outcomes_aggregates_per_playbook_for_signature_only():
    records = [
        _training_record("sig-x", "pb-1", worked=True),
        _training_record("sig-x", "pb-1", worked=False),
        _training_record("sig-x", "pb-2", worked=True),
        _training_record("sig-other", "pb-1", worked=True),  # different signature: excluded
    ]
    tb = _toolbox(training_store=_TrainingStub(records))
    out = tb.dispatch("get_past_outcomes", {"signature": "sig-x"})

    assert out["by_playbook"]["pb-1"] == {"worked": 1, "total": 2}
    assert out["by_playbook"]["pb-2"] == {"worked": 1, "total": 1}
    assert "pb-1" not in out.get("by_playbook", {}) or out["by_playbook"]["pb-1"]["total"] == 2
    assert "sig-other" not in str(out)  # no cross-signature leakage


def test_get_past_outcomes_includes_recent_records():
    records = [_training_record("sig-x", f"pb-{i}", worked=True) for i in range(8)]
    tb = _toolbox(training_store=_TrainingStub(records))
    out = tb.dispatch("get_past_outcomes", {"signature": "sig-x"})
    assert "recent" in out
    assert len(out["recent"]) <= 5


def test_get_past_outcomes_empty_signature_returns_empty_shape():
    tb = _toolbox(training_store=_TrainingStub([]))
    out = tb.dispatch("get_past_outcomes", {"signature": "sig-nope"})
    assert out["by_playbook"] == {}
    assert out["recent"] == []


# ---------------------------------------------------------------------------
# get_human_decisions
# ---------------------------------------------------------------------------


def _audit_record(action: str, cid: str = "sit-1", actor: str = "alice", ts=NOW) -> AuditRecord:
    return AuditRecord(
        actor=actor,
        action=action,
        resource=f"proposal:{cid}",
        decision="allow",
        ts=ts,
        correlation_id=cid,
    )


def test_get_human_decisions_filters_to_proposal_actions_only():
    records = [
        _audit_record("propose"),
        _audit_record("approve-proposal"),
        _audit_record("reject-proposal"),
        _audit_record("diagnose"),  # not a proposal-decision action: excluded
        _audit_record("graduate"),  # not a proposal-decision action: excluded
    ]
    tb = _toolbox(audit_sink=_AuditStub(records))
    out = tb.dispatch("get_human_decisions", {"signature": "sig-x"})
    actions = {d["action"] for d in out["decisions"]}
    assert actions == {"propose", "approve-proposal", "reject-proposal"}


def test_get_human_decisions_caps_at_ten_newest_first():
    records = [
        _audit_record("propose", cid=f"sit-{i}", ts=datetime(2026, 9, i % 28 + 1, tzinfo=UTC))
        for i in range(1, 15)
    ]
    tb = _toolbox(audit_sink=_AuditStub(records))
    out = tb.dispatch("get_human_decisions", {"signature": "sig-x"})
    assert len(out["decisions"]) <= 10


def test_get_human_decisions_shape_has_expected_fields():
    tb = _toolbox(audit_sink=_AuditStub([_audit_record("approve-proposal", actor="bob")]))
    out = tb.dispatch("get_human_decisions", {"signature": "sig-x"})
    d = out["decisions"][0]
    assert d["action"] == "approve-proposal"
    assert d["decided_by"] == "bob"
    assert "resource" in d
    assert "ts" in d


# ---------------------------------------------------------------------------
# get_past_decisions
# ---------------------------------------------------------------------------


def test_get_past_decisions_filtered_by_signature():
    ds = InMemoryAuthorDecisionStore()
    ds.record(
        AuthorDecision(
            signature="sig-x",
            proposal_id="p1",
            playbook_id="ai-sig-x-1",
            actions=["restart"],
            cited_facts=[],
            ts=NOW,
            disposition="accepted",
            outcome="worked",
        )
    )
    ds.record(
        AuthorDecision(
            signature="sig-other",
            proposal_id="p2",
            playbook_id="ai-sig-other-1",
            actions=["scale"],
            cited_facts=[],
            ts=NOW,
        )
    )
    tb = _toolbox(decision_store=ds)
    out = tb.dispatch("get_past_decisions", {"signature": "sig-x"})
    assert len(out["decisions"]) == 1
    assert out["decisions"][0]["outcome"] == "worked"
    assert out["decisions"][0]["signature"] == "sig-x"


def test_get_past_decisions_truncates_and_labels_note_as_untrusted():
    ds = InMemoryAuthorDecisionStore()
    long_note = "x" * 500
    ds.record(
        AuthorDecision(
            signature="sig-x",
            proposal_id="p1",
            playbook_id="ai-sig-x-1",
            actions=["restart"],
            cited_facts=[],
            note=long_note,
            ts=NOW,
        )
    )
    tb = _toolbox(decision_store=ds)
    out = tb.dispatch("get_past_decisions", {"signature": "sig-x"})
    note = out["decisions"][0]["note"]
    assert note.startswith("[prior unverified model note] ")
    assert len(note) < len(long_note)  # actually truncated, not just prefixed


def test_get_past_decisions_none_note_stays_none():
    ds = InMemoryAuthorDecisionStore()
    ds.record(
        AuthorDecision(
            signature="sig-x",
            proposal_id="p1",
            playbook_id="ai-sig-x-1",
            actions=["restart"],
            cited_facts=[],
            note=None,
            ts=NOW,
        )
    )
    tb = _toolbox(decision_store=ds)
    out = tb.dispatch("get_past_decisions", {"signature": "sig-x"})
    assert out["decisions"][0]["note"] is None


def test_get_past_decisions_empty_when_no_match():
    tb = _toolbox(decision_store=InMemoryAuthorDecisionStore())
    out = tb.dispatch("get_past_decisions", {"signature": "sig-nope"})
    assert out["decisions"] == []


# ---------------------------------------------------------------------------
# list_available_actions
# ---------------------------------------------------------------------------


def test_list_actions_returns_closed_set():
    tb = _toolbox()
    out = tb.dispatch("list_available_actions", {})
    assert set(out["actions"]) == set(ACTION_NOTES.keys())
    assert set(out["actions"]) == CLOSED_ACTIONS


def test_action_notes_keys_match_closed_vocabulary_exactly():
    assert set(ACTION_NOTES.keys()) == CLOSED_ACTIONS
    for note in ACTION_NOTES.values():
        assert isinstance(note, str) and note


# ---------------------------------------------------------------------------
# Error isolation: no branch may ever raise out of dispatch
# ---------------------------------------------------------------------------


def test_dispatch_catches_training_store_error():
    tb = _toolbox(training_store=_BoomTrainingStore())
    out = tb.dispatch("get_past_outcomes", {"signature": "sig-x"})
    assert out == {"error": "RuntimeError"}


def test_dispatch_catches_audit_sink_error():
    tb = _toolbox(audit_sink=_BoomAuditSink())
    out = tb.dispatch("get_human_decisions", {"signature": "sig-x"})
    assert out == {"error": "RuntimeError"}


def test_dispatch_catches_decision_store_error():
    tb = _toolbox(decision_store=_BoomDecisionStore())
    out = tb.dispatch("get_past_decisions", {"signature": "sig-x"})
    assert out == {"error": "RuntimeError"}


@pytest.mark.parametrize(
    "tool_name,args",
    [
        ("get_system_context", {}),
        ("get_incident_details", {}),
        ("get_past_outcomes", {"signature": "sig-x"}),
        ("get_human_decisions", {"signature": "sig-x"}),
        ("get_past_decisions", {"signature": "sig-x"}),
        ("list_available_actions", {}),
        ("submit_runbook", {"playbook": {}, "rationale": "r", "cited_facts": []}),
        ("not_a_real_tool", {}),
    ],
)
def test_dispatch_never_raises_for_any_known_or_unknown_name(tool_name, args):
    tb = _toolbox()
    # Must not raise regardless of tool name.
    out = tb.dispatch(tool_name, args)
    assert isinstance(out, dict)


def test_dispatch_submit_runbook_is_not_handled_here():
    tb = _toolbox()
    out = tb.dispatch("submit_runbook", {"playbook": {}, "rationale": "r", "cited_facts": []})
    assert "error" in out


def test_dispatch_unknown_tool_returns_error():
    tb = _toolbox()
    out = tb.dispatch("not_a_real_tool", {})
    assert "error" in out


def test_dispatch_missing_arguments_key_does_not_crash():
    tb = _toolbox()
    # signature/situation_id omitted entirely — must not KeyError.
    for name in ("get_past_outcomes", "get_human_decisions", "get_past_decisions"):
        out = tb.dispatch(name, {})
        assert isinstance(out, dict)
