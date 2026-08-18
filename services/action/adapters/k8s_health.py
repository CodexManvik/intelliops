"""Post-remediation health check against a real cluster.

Two signals, both required: pod readiness (readyReplicas == desiredReplicas from
the deployment status) AND metric recovery (a caller-supplied predicate that
re-queries Prometheus). Polls both up to a timeout — a rolling restart plus a
scrape cycle aren't instant. Never raises: an error mid-poll is treated as
'not yet healthy', so an unreachable cluster times out to False and the caller
rolls back (ADR-007)."""

from __future__ import annotations

import logging
import time

from common.contracts import RemediationTarget, Situation

logger = logging.getLogger("intelliops.action.k8s_health")


def _default_apps_v1():
    from kubernetes import client, config
    config.load_kube_config()
    return client.AppsV1Api()


def _default_exc_type():
    from kubernetes.client.exceptions import ApiException
    return ApiException


class KubernetesHealthChecker:
    def __init__(self, apps_v1=None, metric_healthy=None, timeout_seconds: float = 30.0,
                 poll_interval_seconds: float = 2.0, exc_type=None) -> None:
        self._apps_v1 = apps_v1
        self._metric_healthy = metric_healthy or (lambda: True)
        self._timeout = timeout_seconds
        self._poll = poll_interval_seconds
        self._exc_type = exc_type

    def _api(self):
        if self._apps_v1 is None:
            self._apps_v1 = _default_apps_v1()
        return self._apps_v1

    def _exc(self):
        if self._exc_type is None:
            self._exc_type = _default_exc_type()
        return self._exc_type

    def check(self, situation: Situation, target: RemediationTarget) -> bool:
        deadline = time.monotonic() + self._timeout
        while True:
            if self._pod_ready(target) and self._safe_metric():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(self._poll)

    def _pod_ready(self, target: RemediationTarget) -> bool:
        try:
            st = self._api().read_namespaced_deployment_status(
                target.deployment, target.namespace).status
        except self._exc():
            return False
        except Exception:  # noqa: BLE001 — treat any client error as not-yet-ready
            return False
        ready = st.ready_replicas or 0
        desired = st.replicas or 0
        return desired > 0 and ready == desired

    def _safe_metric(self) -> bool:
        try:
            return bool(self._metric_healthy())
        except Exception:  # noqa: BLE001 — a failed metric query is 'not yet healthy'
            return False
