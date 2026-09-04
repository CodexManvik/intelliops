"""Sandbox adapters: rehearse a remediation plan on an isolated copy.

NullSandbox is the config-switched, test-safe default (sandbox_mode="off"):
it passes through so the base demo and the existing suite are unchanged.

NamespaceCloneSandbox (sandbox_mode="k8s") is the real rehearsal: it clones the
target Deployment (plus, best-effort, its Service and referenced ConfigMap) into
a throwaway namespace, applies the SAME fix to the clone, watches the clone's
pod become healthy, tears the namespace down, and returns a pass/fail verdict —
all BEFORE a human approves the real remediation (ADR-007 fail-safe posture).

Fail-safe by construction: `rehearse` wraps its whole body in try/except and
ALWAYS returns a PreflightResult (never propagates); the throwaway namespace is
ALWAYS torn down in a finally (best-effort — teardown failure is logged, not
raised). The `kubernetes` client is imported lazily inside `_load_k8s()` so the
default (sandbox_mode="off") path never needs the k8s extra installed.
"""

from __future__ import annotations

import logging
from uuid import uuid4

from common.contracts import PreflightResult, RemediationPlan, RemediationTarget, Situation
from services.action.adapters.k8s_health import KubernetesHealthChecker
from services.action.adapters.k8s_remediator import KubernetesRemediator

logger = logging.getLogger("intelliops.action.sandbox")

# Bounded waits — a rehearsal must never poll forever. The clone is a single
# fresh pod, so a short rollout window is generous; the post-fix window matches
# the production health checker's default so the rehearsal mirrors the real gate.
_ROLLOUT_TIMEOUT_SECONDS = 60.0
_HEALTH_TIMEOUT_SECONDS = 60.0
_POLL_INTERVAL_SECONDS = 2.0


def _load_k8s():
    from kubernetes import client, config  # imported lazily so off-mode never needs k8s

    try:
        config.load_incluster_config()
    except Exception:  # noqa: BLE001
        config.load_kube_config()
    return client.AppsV1Api(), client.CoreV1Api()


class NullSandbox:
    """No-op sandbox. Rehearses nothing; reports an honest 'not rehearsed'."""

    def rehearse(self, situation: Situation, plan: RemediationPlan) -> PreflightResult:
        return PreflightResult(
            passed=True,
            detail="not rehearsed (sandbox off)",
            mode="off",
        )


def _strip_deployment(dep, sandbox_ns: str):
    """Prepare a Deployment read from the cluster for re-creation in sandbox_ns.

    Strips server-assigned identity/bookkeeping that a create() must not carry
    (resourceVersion/uid/creationTimestamp/status) and any ownerReferences (the
    clone is owned by nothing), retargets it at the throwaway namespace, and
    PRESERVES the pod template — labels and containers[].resources included — so
    the clone is representative and the existing Prometheus scrape/relabel can
    still discover it.
    """
    meta = dep.metadata
    meta.namespace = sandbox_ns
    meta.resource_version = None
    meta.uid = None
    meta.creation_timestamp = None
    meta.owner_references = None
    meta.managed_fields = None
    meta.self_link = None
    # A stale annotation snapshot from the source object would fight the create.
    if meta.annotations:
        meta.annotations.pop("deployment.kubernetes.io/revision", None)
        meta.annotations.pop("kubectl.kubernetes.io/last-applied-configuration", None)
    dep.status = None  # server-owned; must be absent on create
    return dep


def _strip_service(svc, sandbox_ns: str):
    """Prepare a Service for re-creation: drop the cluster-assigned IPs and any
    nodePort so the sandbox Service is allocated fresh (a copied clusterIP/
    nodePort would collide or be rejected)."""
    meta = svc.metadata
    meta.namespace = sandbox_ns
    meta.resource_version = None
    meta.uid = None
    meta.creation_timestamp = None
    meta.owner_references = None
    meta.managed_fields = None
    meta.self_link = None
    spec = svc.spec
    spec.cluster_ip = None
    spec.cluster_ips = None
    for port in spec.ports or []:
        port.node_port = None
    svc.status = None
    return svc


def _strip_config_map(cm, sandbox_ns: str):
    """Prepare a ConfigMap for re-creation (data is preserved verbatim)."""
    meta = cm.metadata
    meta.namespace = sandbox_ns
    meta.resource_version = None
    meta.uid = None
    meta.creation_timestamp = None
    meta.owner_references = None
    meta.managed_fields = None
    meta.self_link = None
    return cm


def _referenced_config_map_names(dep) -> list[str]:
    """Best-effort scan of the pod template for referenced ConfigMap names —
    volumes (configMap), envFrom (configMapRef) and env (configMapKeyRef). Any
    shape we can't read is skipped; cloning a ConfigMap is a nicety, not the core
    of the rehearsal (the Deployment + its pod health is)."""
    names: set[str] = set()
    try:
        pod_spec = dep.spec.template.spec
        for vol in pod_spec.volumes or []:
            cm = getattr(vol, "config_map", None)
            if cm is not None and getattr(cm, "name", None):
                names.add(cm.name)
        for container in pod_spec.containers or []:
            for env_from in getattr(container, "env_from", None) or []:
                ref = getattr(env_from, "config_map_ref", None)
                if ref is not None and getattr(ref, "name", None):
                    names.add(ref.name)
            for env in getattr(container, "env", None) or []:
                src = getattr(env, "value_from", None)
                ref = getattr(src, "config_map_key_ref", None) if src is not None else None
                if ref is not None and getattr(ref, "name", None):
                    names.add(ref.name)
    except Exception as exc:  # noqa: BLE001 — a scan failure just means "no CMs cloned"
        logger.debug("configmap ref scan skipped: %s", exc)
    return sorted(names)


class NamespaceCloneSandbox:
    """Rehearse a plan by cloning the target into a throwaway namespace.

    Pass signal for PR A is the CLONE'S POD READINESS. The health checker's
    metric predicate is left at its default (`lambda: True`) rather than wired to
    Prometheus: the demo's `cpu_usage` series is keyed per-metric-name, not
    per-namespace, so a clone's metric series is not reliably distinguishable
    from production's — a real per-namespace metric query is deferred to PR B.
    So `passed` is driven by the clone pod reaching ready==desired after the fix.
    """

    def __init__(self, namespace: str, prometheus_url: str | None = None):
        self._namespace = namespace
        self._prometheus_url = prometheus_url

    def rehearse(self, situation: Situation, plan: RemediationPlan) -> PreflightResult:
        sandbox_ns = f"intelliops-sandbox-{uuid4().hex[:8]}"
        apps_v1 = core_v1 = None
        dep_name = plan.target.deployment
        try:
            apps_v1, core_v1 = _load_k8s()

            # --- clone: read the target Deployment, strip server-owned fields,
            #     retarget it at the throwaway namespace (same deployment name is
            #     safe — a fresh namespace can't collide) ---
            source_dep = apps_v1.read_namespaced_deployment(dep_name, self._namespace)
            clone_dep = _strip_deployment(source_dep, sandbox_ns)

            # Create the namespace first, then the cloned Deployment into it.
            core_v1.create_namespace(_namespace_body(sandbox_ns))
            apps_v1.create_namespaced_deployment(namespace=sandbox_ns, body=clone_dep)

            # --- clone (best-effort): the Service selecting the deployment and
            #     any referenced ConfigMaps. If the target has no Service or no
            #     ConfigMap, skip gracefully — the pod's health is the core signal ---
            self._clone_service_best_effort(core_v1, dep_name, sandbox_ns)
            self._clone_config_maps_best_effort(core_v1, source_dep, sandbox_ns)

            # The clone lives in sandbox_ns under the same deployment name.
            clone_target = RemediationTarget(namespace=sandbox_ns, deployment=dep_name)

            # --- wait for the initial rollout (bounded) — reuse the health
            #     checker's pod-ready poll to confirm the clone came up before we
            #     perturb it; metric predicate left at default (see class docs) ---
            rollout_ok = KubernetesHealthChecker(
                apps_v1=apps_v1,
                timeout_seconds=_ROLLOUT_TIMEOUT_SECONDS,
                poll_interval_seconds=_POLL_INTERVAL_SECONDS,
            ).check(situation, clone_target)
            if not rollout_ok:
                return PreflightResult(
                    passed=False,
                    detail=f"sandbox: clone {dep_name} never became ready before fix",
                    mode="k8s",
                    sandbox_namespace=sandbox_ns,
                )

            # --- apply the SAME fix to the clone: reuse the real remediator,
            #     injecting the AppsV1 client we already hold, with the plan
            #     retargeted at the clone (same steps, sandbox namespace) ---
            clone_plan = plan.model_copy(update={"target": clone_target})
            applied = KubernetesRemediator(sandbox_ns, apps_v1=apps_v1).execute(clone_plan)
            if not applied:
                return PreflightResult(
                    passed=False,
                    detail=f"sandbox: clone {dep_name} remediation apply failed",
                    mode="k8s",
                    sandbox_namespace=sandbox_ns,
                )

            # --- health: post-fix, poll the clone to ready==desired (bounded).
            #     This is the PRIMARY pass signal for PR A ---
            health = KubernetesHealthChecker(
                apps_v1=apps_v1,
                timeout_seconds=_HEALTH_TIMEOUT_SECONDS,
                poll_interval_seconds=_POLL_INTERVAL_SECONDS,
            ).check(situation, clone_target)

            passed = bool(health)
            detail = (
                f"sandbox: clone {dep_name} healthy after fix"
                if passed
                else f"sandbox: clone {dep_name} did not recover after fix"
            )
            return PreflightResult(
                passed=passed, detail=detail, mode="k8s", sandbox_namespace=sandbox_ns
            )
        except Exception as exc:  # noqa: BLE001 — fail-safe, never propagate
            logger.warning("sandbox rehearsal failed: %s", exc)
            return PreflightResult(
                passed=False,
                detail=f"sandbox error: {exc.__class__.__name__}",
                mode="k8s",
                sandbox_namespace=sandbox_ns,
            )
        finally:
            if core_v1 is not None:
                try:
                    core_v1.delete_namespace(name=sandbox_ns)
                except Exception as exc:  # noqa: BLE001 — best-effort teardown
                    logger.warning("sandbox teardown failed for %s: %s", sandbox_ns, exc)

    def _clone_service_best_effort(self, core_v1, dep_name: str, sandbox_ns: str) -> None:
        """Clone the Service named like the deployment, if one exists. The demo's
        Services share their Deployment's name; a missing Service (404) or any
        other read/create error is swallowed — the Deployment is the rehearsal."""
        try:
            svc = core_v1.read_namespaced_service(dep_name, self._namespace)
        except Exception as exc:  # noqa: BLE001 — no Service to clone; that's fine
            logger.debug("no service to clone for %s: %s", dep_name, exc)
            return
        try:
            core_v1.create_namespaced_service(
                namespace=sandbox_ns, body=_strip_service(svc, sandbox_ns)
            )
        except Exception as exc:  # noqa: BLE001 — Service clone is best-effort
            logger.debug("service clone skipped for %s: %s", dep_name, exc)

    def _clone_config_maps_best_effort(self, core_v1, source_dep, sandbox_ns: str) -> None:
        """Clone every ConfigMap the pod template references, best-effort."""
        for cm_name in _referenced_config_map_names(source_dep):
            try:
                cm = core_v1.read_namespaced_config_map(cm_name, self._namespace)
                core_v1.create_namespaced_config_map(
                    namespace=sandbox_ns, body=_strip_config_map(cm, sandbox_ns)
                )
            except Exception as exc:  # noqa: BLE001 — a missing/unreadable CM is skipped
                logger.debug("configmap clone skipped for %s: %s", cm_name, exc)


def _namespace_body(sandbox_ns: str):
    """Build the throwaway namespace object, labeled so it is obviously ours (and
    easy to sweep if a teardown is ever missed)."""
    from kubernetes import client  # lazy — same rationale as _load_k8s

    return client.V1Namespace(
        metadata=client.V1ObjectMeta(
            name=sandbox_ns,
            labels={"intelliops.io/sandbox": "true", "app.kubernetes.io/managed-by": "intelliops"},
        )
    )
