"""Remediator implementations (non-K8s).

DryRunRemediator is the safe default: logs the plan and succeeds without
touching infrastructure. RecordingRemediator is the test double capturing the
plan passed to execute/rollback. The real KubernetesRemediator lives in
k8s_remediator.py."""

from __future__ import annotations

import logging

from common.contracts import RemediationPlan

logger = logging.getLogger("intelliops.action.remediator")


class DryRunRemediator:
    def execute(self, plan: RemediationPlan) -> bool:
        for step in plan.steps:
            logger.info(
                "DRY-RUN execute on %s/%s: %s",
                plan.target.namespace,
                plan.target.deployment,
                step.action,
            )
        return True

    def rollback(self, plan: RemediationPlan) -> bool:
        for step in plan.rollback_steps:
            logger.info(
                "DRY-RUN rollback on %s/%s: %s",
                plan.target.namespace,
                plan.target.deployment,
                step.action,
            )
        return True


class RecordingRemediator:
    def __init__(self, execute_result: bool = True, rollback_result: bool = True) -> None:
        self._execute_result = execute_result
        self._rollback_result = rollback_result
        self.executed_plan: RemediationPlan | None = None
        self.rolled_back_plan: RemediationPlan | None = None

    def execute(self, plan: RemediationPlan) -> bool:
        self.executed_plan = plan
        return self._execute_result

    def rollback(self, plan: RemediationPlan) -> bool:
        self.rolled_back_plan = plan
        return self._rollback_result
