"""Post-remediation health check against a real cluster.

Two signals, both required: pod readiness (readyReplicas == desiredReplicas from
the deployment status) AND metric recovery. When a `policy` + `query_value` are
supplied, the metric signal is the per-metric recovery predicate built fresh from
the Situation's firing metrics (see `services.action.verify.build_metric_healthy`);
otherwise it falls back to the injected `metric_healthy` predicate (default
`lambda: True`) exactly as before, for back-compat. Polls both up to a timeout —
a rolling restart plus a scrape cycle aren't instant. Never raises: an error
mid-poll is treated as 'not yet healthy', so an unreachable cluster times out to
False and the caller rolls back (ADR-007)."""

from __future__ import annotations

import logging
import time

from common.contracts import RemediationTarget, Situation
from services.action.adapters.kube_config import load_kube
from services.action.verify import build_metric_healthy

logger = logging.getLogger("intelliops.action.k8s_health")


def _default_apps_v1():
    from kubernetes import client, config

    load_kube(config)
    return client.AppsV1Api()


def _default_exc_type():
    from kubernetes.client.exceptions import ApiException

    return ApiException


class KubernetesHealthChecker:
    def __init__(
        self,
        apps_v1=None,
        metric_healthy=None,
        policy=None,
        query_value=None,
        z_threshold: float = 3.0,
        timeout_seconds: float = 30.0,
        poll_interval_seconds: float = 2.0,
        exc_type=None,
    ) -> None:
        self._apps_v1 = apps_v1
        self._metric_healthy = metric_healthy or (lambda: True)
        self._policy = policy
        self._query_value = query_value
        self._z_threshold = z_threshold
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
        # Built ONCE per check() call, not per poll iteration: build_metric_healthy
        # reads the Situation's member_events (static for this check) and returns a
        # fresh closure whose query_value() call re-queries the current metric value
        # every time it's invoked — so polling below still sees live metric data on
        # each iteration even though the predicate itself is built up front.
        if self._policy is not None and self._query_value is not None:
            metric_healthy = build_metric_healthy(
                situation, self._query_value, self._policy, self._z_threshold
            )
        else:
            metric_healthy = self._metric_healthy  # back-compat (default lambda: True)

        deadline = time.monotonic() + self._timeout
        while True:
            if self._pod_ready(target) and self._safe_metric(metric_healthy):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(self._poll)

    def _pod_ready(self, target: RemediationTarget) -> bool:
        # A single bare `except Exception` is the robust guard here: it catches
        # the K8s ApiException (which subclasses Exception), a ConfigException /
        # connection error from lazily acquiring the client via self._api(), AND
        # any failure resolving the client itself — with no escape. A specific
        # `except self._exc()` clause would be both redundant (ApiException is an
        # Exception) and fragile: if evaluating its type expression raised, that
        # error would escape the try (Python does not consult sibling excepts for
        # an error raised while matching an except type). So: one catch, no gaps.
        try:
            st = (
                self._api()
                .read_namespaced_deployment_status(target.deployment, target.namespace)
                .status
            )
        except Exception:  # noqa: BLE001 — any client/config/connection error → not-yet-ready
            return False
        ready = st.ready_replicas or 0
        desired = st.replicas or 0
        return desired > 0 and ready == desired

    def _safe_metric(self, predicate) -> bool:
        try:
            return bool(predicate())
        except Exception:  # noqa: BLE001 — a failed metric query is 'not yet healthy'
            return False
