"""The submit_runbook schema must agree with the validator that enforces it.

submit_runbook used to declare its playbook parameter as a bare untyped object
with a prose field list, so the model was never shown the closed vocabularies.
Against a live model it guessed hitl_mode="manual" (HitlMode is
auto|hitl|disabled) and invented per-step keys; Playbook.model_validate rejected
every submission and the agent always ended `gave_up` with no proposal.

These tests pin the schema to common/contracts.py so the two cannot drift.
"""

from typing import get_args

from common.contracts import HitlMode, Playbook, RemediationStep
from services.governance.adapters.author_tools import TOOL_SCHEMAS


def _playbook_schema() -> dict:
    submit = next(t for t in TOOL_SCHEMAS if t["function"]["name"] == "submit_runbook")
    return submit["function"]["parameters"]["properties"]["playbook"]


def test_playbook_parameter_is_actually_specified():
    pb = _playbook_schema()
    assert pb["type"] == "object"
    # A bare {"type": "object"} is what caused the bug.
    assert "properties" in pb and pb["properties"], "playbook parameter has no schema"


def test_hitl_mode_enum_matches_the_contract():
    enum = _playbook_schema()["properties"]["hitl_mode"]["enum"]
    assert set(enum) == {m.value for m in HitlMode}
    assert "manual" not in enum  # what the model guessed when it had no enum


def test_step_action_enum_matches_the_closed_literal():
    step = _playbook_schema()["properties"]["steps"]["items"]
    enum = step["properties"]["action"]["enum"]
    assert set(enum) == set(get_args(RemediationStep.model_fields["action"].annotation))


def test_schema_does_not_ask_the_model_for_an_id():
    """The server assigns the id; a model-authored one is exactly what we distrust."""
    pb = _playbook_schema()
    assert "id" not in pb["properties"]
    assert "id" not in pb["required"]


def test_required_fields_are_the_ones_the_validator_demands():
    """Every non-defaulted Playbook field the AI authors must be required here."""
    pb = _playbook_schema()
    demanded = {
        name
        for name, f in Playbook.model_fields.items()
        if f.is_required() and name != "id"  # id is server-assigned
    }
    assert demanded <= set(pb["required"]), f"schema does not require {demanded - set(pb['required'])}"


def test_a_schema_shaped_submission_validates_as_a_playbook():
    """The end-to-end point: a draft that obeys this schema must pass the validator."""
    draft = {
        "id": "ai-draft-pending",  # injected server-side before validation
        "name": "TLS handshake failures - investigate and recycle",
        "match_rule": 'signature == "abc123"',
        "hitl_mode": _playbook_schema()["properties"]["hitl_mode"]["enum"][0],
        "reversible": True,
        "steps": [
            {"action": "restart", "note": "clear wedged TLS session state"},
            {"action": "wait", "note": "let the new pod settle"},
        ],
        "rollback_steps": [{"action": "rollback_deploy", "note": "undo"}],
    }
    pb = Playbook.model_validate(draft)
    assert [s.action for s in pb.steps] == ["restart", "wait"]
