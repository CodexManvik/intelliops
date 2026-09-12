import pytest
from pydantic import ValidationError

from common.contracts import RemediationStep


def test_new_tier2_actions_validate():
    for action in ("patch_resource_limits", "rollback_to_revision", "patch_probe"):
        step = RemediationStep(action=action)
        assert step.action == action


def test_existing_actions_unchanged():
    step = RemediationStep(action="restart")
    assert step.action == "restart"
    assert step.replicas is None and step.note is None


def test_out_of_set_action_still_rejected():
    # The closed-Literal safety property: an unlisted action must not validate.
    for bad in ("delete", "exec", "scale_to_zero", "drain_node", ""):
        with pytest.raises(ValidationError):
            RemediationStep(action=bad)


def test_tier2_params_are_optional_and_additive():
    # All new params default None; a step needs only `action`.
    step = RemediationStep(action="patch_resource_limits", cpu_limit="500m", mem_limit="512Mi")
    assert step.cpu_limit == "500m" and step.mem_limit == "512Mi"
    assert step.container is None
    probe = RemediationStep(action="patch_probe", probe="liveness", initial_delay_seconds=10)
    assert probe.probe == "liveness" and probe.initial_delay_seconds == 10
    rev = RemediationStep(action="rollback_to_revision", revision=3)
    assert rev.revision == 3
