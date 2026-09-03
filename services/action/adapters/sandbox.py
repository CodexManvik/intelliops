"""Sandbox adapters: rehearse a remediation plan on an isolated copy.

NullSandbox is the config-switched, test-safe default (sandbox_mode="off"):
it passes through so the base demo and the existing suite are unchanged. The
real NamespaceCloneSandbox (sandbox_mode="k8s") is added in a later task."""

from __future__ import annotations

from common.contracts import PreflightResult, RemediationPlan, Situation


class NullSandbox:
    """No-op sandbox. Rehearses nothing; reports an honest 'not rehearsed'."""

    def rehearse(self, situation: Situation, plan: RemediationPlan) -> PreflightResult:
        return PreflightResult(
            passed=True,
            detail="not rehearsed (sandbox off)",
            mode="off",
        )
