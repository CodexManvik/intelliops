"""Pick the right kube auth: in-cluster ServiceAccount when running as a pod,
a kubeconfig file otherwise (compose/local). Try in-cluster first, fall back to
kubeconfig — the robust detection sandbox.py already used, now shared + tested.
Injectable so the selection is unit-testable without a real client/cluster."""

from __future__ import annotations


def load_kube(config) -> None:
    try:
        config.load_incluster_config()
    except Exception:  # noqa: BLE001 — not in a pod → fall back to a kubeconfig file
        config.load_kube_config()
