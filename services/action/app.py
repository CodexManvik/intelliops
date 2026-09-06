"""Action service: HITL-gated, reversible remediation."""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common.config import get_settings
from common.stores import make_stores
from services.action.adapters.governance_gate import (
    HttpGovernanceGate,
    InProcessGovernanceGate,
)
from services.action.adapters.health import AlwaysHealthyChecker
from services.action.adapters.k8s_health import KubernetesHealthChecker
from services.action.adapters.k8s_remediator import KubernetesRemediator
from services.action.adapters.remediator import DryRunRemediator
from services.action.consumer import run_consumer
from services.base import create_app, db_ready
from services.governance.rbac import RbacPolicy


def _make_gate(settings, audit_sink):
    if settings.governance_mode == "http":
        return HttpGovernanceGate(
            settings.governance_url,
            poll_interval_seconds=settings.hitl_poll_interval_seconds,
        )
    return InProcessGovernanceGate(
        RbacPolicy.from_file(settings.rbac_policy_path),
        {},
        audit_sink,
        poll_interval_seconds=settings.hitl_poll_interval_seconds,
    )


def _make_remediator(settings):
    if settings.remediator_mode == "k8s":
        return KubernetesRemediator(settings.k8s_namespace)
    return DryRunRemediator()


def _make_sandbox(settings):
    if settings.sandbox_mode == "k8s":
        from services.action.adapters.sandbox import NamespaceCloneSandbox

        policy = _make_detection_policy(settings)
        return NamespaceCloneSandbox(
            settings.k8s_namespace,
            prometheus_url=settings.prometheus_url,
            policy=policy,
            z_threshold=settings.correlation_z_threshold,
        )
    from services.action.adapters.sandbox import NullSandbox

    return NullSandbox()


def _make_detection_policy(settings):
    from services.correlation.detection_policy import DetectionPolicy

    return DetectionPolicy(
        enabled=(settings.detection_policy == "on"),
        thresholds={
            "ratio": settings.detection_ratio_threshold,
            "saturation_ratio": settings.detection_saturation_ratio_threshold,
            "saturation_percent": settings.detection_saturation_percent_threshold,
            "latency_ceiling_ms": settings.detection_latency_ceiling_ms,
        },
    )


def _make_health_checker(settings):
    if settings.health_check_mode == "k8s":
        import httpx

        policy = _make_detection_policy(settings)

        def query_value(name: str) -> float | None:
            # Instant-query the current value of the FIRING metric by name (not cpu).
            # max across series -> the worst-behaving instance must be recovered.
            try:
                r = httpx.get(
                    f"{settings.prometheus_url}/api/v1/query",
                    params={"query": name},
                    timeout=5.0,
                )
                results = r.json().get("data", {}).get("result", [])
                if not results:
                    return None
                return max(float(v["value"][1]) for v in results)
            except Exception:  # noqa: BLE001 — a failed query -> None -> metric not recovered
                return None

        return KubernetesHealthChecker(
            policy=policy,
            query_value=query_value,
            z_threshold=settings.correlation_z_threshold,
        )
    return AlwaysHealthyChecker()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    stop_event = threading.Event()
    stores = make_stores(settings)
    app.state.db_engine = stores.engine
    store = stores.playbook_store
    gate = _make_gate(settings, stores.audit_sink)
    thread = threading.Thread(
        target=run_consumer,
        args=(
            app.state.bus,
            store,
            gate,
            _make_remediator(settings),
            _make_health_checker(settings),
            _make_sandbox(settings),
            settings.hitl_poll_timeout_seconds,
            settings.hitl_poll_interval_seconds,
            stop_event,
        ),
        daemon=True,
    )
    thread.start()
    app.state.consumer_stop = stop_event
    app.state.consumer_thread = thread
    try:
        yield
    finally:
        stop_event.set()
        if stores.engine is not None:
            stores.engine.dispose()


app = create_app(
    "action-service",
    readiness=lambda: db_ready(getattr(app.state, "db_engine", None)),
)
app.router.lifespan_context = lifespan
