# tests/test_remediation_contracts.py
from common.contracts import (HitlMode, Playbook, RemediationPlan, RemediationStep,
                              RemediationTarget)


def test_remediation_step_and_plan():
    step = RemediationStep(action="scale", replicas=2)
    assert step.action == "scale" and step.replicas == 2
    plan = RemediationPlan(
        target=RemediationTarget(namespace="ns", deployment="demo-app"),
        steps=[RemediationStep(action="restart")],
        rollback_steps=[RemediationStep(action="restart")],
    )
    assert plan.target.deployment == "demo-app"
    assert plan.steps[0].action == "restart"


def test_playbook_steps_are_structured():
    pb = Playbook(id="restart-pod", name="Restart", match_rule="x",
                  steps=[RemediationStep(action="restart"),
                         RemediationStep(action="wait", note="readiness")],
                  hitl_mode=HitlMode.HITL, reversible=True,
                  rollback_steps=[RemediationStep(action="restart")])
    assert pb.steps[0].action == "restart"
    # parses from dicts too (YAML load path)
    pb2 = Playbook.model_validate({
        "id": "scale-service", "name": "Scale", "match_rule": "x",
        "steps": [{"action": "scale", "replicas": 2}],
        "hitl_mode": "hitl", "reversible": True,
        "rollback_steps": [{"action": "scale", "replicas": -2}],
    })
    assert pb2.steps[0].action == "scale" and pb2.steps[0].replicas == 2
