"""HealthChecker implementations (non-K8s).

AlwaysHealthyChecker pairs with the dry-run remediator. FixedHealthChecker is
the test double. The real KubernetesHealthChecker lives in k8s_health.py."""

from __future__ import annotations

from common.contracts import RemediationTarget, Situation


class AlwaysHealthyChecker:
    def check(self, situation: Situation, target: RemediationTarget) -> bool:
        return True


class FixedHealthChecker:
    def __init__(self, healthy: bool) -> None:
        self._healthy = healthy

    def check(self, situation: Situation, target: RemediationTarget) -> bool:
        return self._healthy
