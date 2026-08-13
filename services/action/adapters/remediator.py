"""Remediator implementations.

DryRunRemediator is the safe running-service default: it logs the steps and
succeeds without touching real infrastructure. RecordingRemediator is the test
double that captures execute/rollback calls — the safety assertions check
whether execute was (or was NOT) called. Real K8s/Ansible remediators are
deferred (see ADR-007)."""

from __future__ import annotations

import logging

logger = logging.getLogger("intelliops.action.remediator")


class DryRunRemediator:
    def execute(self, steps: list[str]) -> bool:
        for step in steps:
            logger.info("DRY-RUN execute: %s", step)
        return True

    def rollback(self, steps: list[str]) -> bool:
        for step in steps:
            logger.info("DRY-RUN rollback: %s", step)
        return True


class RecordingRemediator:
    def __init__(self, execute_result: bool = True, rollback_result: bool = True) -> None:
        self._execute_result = execute_result
        self._rollback_result = rollback_result
        self.executed_steps: list[str] = []
        self.rolled_back_steps: list[str] = []

    def execute(self, steps: list[str]) -> bool:
        self.executed_steps = list(steps)
        return self._execute_result

    def rollback(self, steps: list[str]) -> bool:
        self.rolled_back_steps = list(steps)
        return self._rollback_result
