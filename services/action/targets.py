"""Resolve a Situation to the K8s deployment it targets.

The situation's member events carry a `service` label (from the telemetry
relabel); the deployment name is that label, in the configured namespace. This
mirrors how real AIOps derives the remediation target from the incident's own
telemetry rather than hard-coding it per playbook."""

from __future__ import annotations

from common.contracts import RemediationTarget, Situation


def resolve_target(situation: Situation, namespace: str) -> RemediationTarget:
    deployment = "unknown"
    for ev in situation.member_events:
        svc = ev.labels.get("service") or ev.labels.get("job")
        if svc:
            deployment = svc
            break
    return RemediationTarget(namespace=namespace, deployment=deployment)
