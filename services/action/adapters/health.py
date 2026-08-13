"""HealthChecker implementations.

AlwaysHealthyChecker pairs with the dry-run remediator (nothing really changed,
so health is unchanged). FixedHealthChecker is the test double that lets a test
drive the rollback path by returning unhealthy. A real checker (re-query
Prometheus / pod status) is deferred (see ADR-007)."""

from __future__ import annotations

from common.contracts import Situation


class AlwaysHealthyChecker:
    def check(self, situation: Situation) -> bool:
        return True


class FixedHealthChecker:
    def __init__(self, healthy: bool) -> None:
        self._healthy = healthy

    def check(self, situation: Situation) -> bool:
        return self._healthy
