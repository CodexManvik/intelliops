"""A Remediator that acts on a real Kubernetes cluster via the API.

Maps structured RemediationSteps to typed AppsV1Api calls — no shell, no string
parsing. Defensive by construction: any API error is caught and turns into a
False result (never an escaped exception), so a remediation that can't reach the
cluster fails safely (ADR-007). Only restart/scale/rollback — never delete."""

from __future__ import annotations

import datetime as _dt
import logging

from common.contracts import RemediationPlan, RemediationStep, RemediationTarget

logger = logging.getLogger("intelliops.action.k8s")

_MAX_REPLICAS = 10
_MIN_REPLICAS = 1


def _default_apps_v1():
    from kubernetes import client, config

    config.load_kube_config()
    return client.AppsV1Api()


def _default_exc_type():
    from kubernetes.client.exceptions import ApiException

    return ApiException


class KubernetesRemediator:
    def __init__(self, namespace_default: str, apps_v1=None, exc_type=None) -> None:
        self._ns_default = namespace_default
        self._apps_v1 = apps_v1  # injected in tests; loaded lazily if None
        self._exc_type = exc_type  # the exception class to treat as a safe failure

    def _api(self):
        if self._apps_v1 is None:
            self._apps_v1 = _default_apps_v1()
        return self._apps_v1

    def _exc(self):
        if self._exc_type is None:
            self._exc_type = _default_exc_type()
        return self._exc_type

    def execute(self, plan: RemediationPlan) -> bool:
        return self._run(plan.target, plan.steps)

    def rollback(self, plan: RemediationPlan) -> bool:
        return self._run(plan.target, plan.rollback_steps)

    def _run(self, target: RemediationTarget, steps: list[RemediationStep]) -> bool:
        ns = target.namespace or self._ns_default
        try:
            exc_type = self._exc()
            api = self._api()
            for step in steps:
                self._dispatch(api, ns, target.deployment, step)
        except exc_type as exc:  # any K8s API error → safe failure
            logger.warning("k8s remediation failed on %s/%s: %s", ns, target.deployment, exc)
            return False
        except Exception as exc:  # noqa: BLE001 — fail closed on any client/connection error
            logger.warning("k8s remediation errored on %s/%s: %s", ns, target.deployment, exc)
            return False
        return True

    def _dispatch(self, api, ns: str, deployment: str, step: RemediationStep) -> None:
        if step.action == "wait":
            return  # readiness is the health checker's job
        if step.action == "restart":
            stamp = _dt.datetime.now(_dt.UTC).isoformat()
            body = {
                "spec": {
                    "template": {
                        "metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": stamp}}
                    }
                }
            }
            api.patch_namespaced_deployment(deployment, ns, body)
            return
        if step.action == "scale":
            current = api.read_namespaced_deployment(deployment, ns).spec.replicas or 1
            desired = max(_MIN_REPLICAS, min(_MAX_REPLICAS, current + (step.replicas or 0)))
            api.patch_namespaced_deployment_scale(deployment, ns, {"spec": {"replicas": desired}})
            return
        if step.action == "rollback_deploy":
            # A rollback is a restart against the prior revision annotation; for the
            # demo, a rolling restart recreates the pod (the fault is in-memory), so
            # we implement rollback_deploy as a rollout restart of the deployment.
            stamp = _dt.datetime.now(_dt.UTC).isoformat()
            body = {
                "spec": {
                    "template": {"metadata": {"annotations": {"intelliops.io/rolledBackAt": stamp}}}
                }
            }
            api.patch_namespaced_deployment(deployment, ns, body)
            return
        logger.warning("unknown remediation action: %s", step.action)
