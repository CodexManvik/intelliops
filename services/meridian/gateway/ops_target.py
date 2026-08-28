"""Ops-proxy target-URL resolution for Meridian fault injection.

In `compose` mode the gateway reaches the sibling Meridian services by their
compose service DNS name. In `k8s` mode the Meridian services run in the kind
cluster and are reached via their NodePorts through host.docker.internal (the
same path the k8s overlay uses to reach the in-cluster Prometheus at :30090).
Kept as a pure function so it's unit-testable without importing the gateway app
(which re-registers prometheus_client gauges — see test_gateway.py)."""

from __future__ import annotations

# service short-name -> NodePort (must match deploy/k8s/meridian/*-service.yaml
# and deploy/k8s/kind-config.yaml extraPortMappings).
_NODEPORTS = {
    "gateway": 30808,
    "validation": 30811,
    "aggregation": 30812,
    "reporting": 30813,
}


def ops_target_url(svc: str, path: str, mode: str) -> str:
    """Build the fault/clear URL for a Meridian service. `path` is e.g.
    'admin/fault'. `mode` is 'compose' (default) or 'k8s'."""
    if mode == "k8s":
        port = _NODEPORTS[svc]
        return f"http://host.docker.internal:{port}/{path}"
    return f"http://meridian-{svc}:8000/{path}"
