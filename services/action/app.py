"""Action service: HITL-gated, reversible remediation."""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common.config import get_settings
from services.action.adapters.governance_gate import (
    HttpGovernanceGate,
    InProcessGovernanceGate,
)
from services.action.adapters.health import AlwaysHealthyChecker
from services.action.adapters.k8s_health import KubernetesHealthChecker
from services.action.adapters.k8s_remediator import KubernetesRemediator
from services.action.adapters.remediator import DryRunRemediator
from services.action.consumer import run_consumer
from services.base import create_app
from services.governance.adapters.audit_sink import FileAuditSink
from services.governance.adapters.playbook_store import FilePlaybookStore
from services.governance.rbac import RbacPolicy


def _make_gate(settings):
    if settings.governance_mode == "http":
        return HttpGovernanceGate(
            settings.governance_url,
            poll_interval_seconds=settings.hitl_poll_interval_seconds,
        )
    return InProcessGovernanceGate(
        RbacPolicy.from_file(settings.rbac_policy_path),
        {},
        FileAuditSink(settings.audit_store_path),
        poll_interval_seconds=settings.hitl_poll_interval_seconds,
    )


def _make_remediator(settings):
    if settings.remediator_mode == "k8s":
        return KubernetesRemediator(settings.k8s_namespace)
    return DryRunRemediator()


def _make_health_checker(settings):
    if settings.health_check_mode == "k8s":
        # metric_healthy re-queries Prometheus for the demo-app error rate; a low
        # value means recovered. Built lazily so dry-run mode never imports httpx here.
        import httpx

        def metric_healthy() -> bool:
            try:
                r = httpx.get(f"{settings.prometheus_url}/api/v1/query",
                              params={"query": "cpu_usage"}, timeout=5.0)
                results = r.json().get("data", {}).get("result", [])
                return all(float(v["value"][1]) < 50 for v in results) if results else False
            except Exception:  # noqa: BLE001
                return False

        return KubernetesHealthChecker(metric_healthy=metric_healthy)
    return AlwaysHealthyChecker()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    stop_event = threading.Event()
    store = FilePlaybookStore(settings.playbook_store_path)
    gate = _make_gate(settings)
    thread = threading.Thread(
        target=run_consumer,
        args=(app.state.bus, store, gate, _make_remediator(settings), _make_health_checker(settings),
              settings.hitl_poll_timeout_seconds, settings.hitl_poll_interval_seconds, stop_event),
        daemon=True,
    )
    thread.start()
    app.state.consumer_stop = stop_event
    app.state.consumer_thread = thread
    try:
        yield
    finally:
        stop_event.set()


app = create_app("action-service")
app.router.lifespan_context = lifespan
