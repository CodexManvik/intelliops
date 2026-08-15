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


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    stop_event = threading.Event()
    store = FilePlaybookStore(settings.playbook_store_path)
    gate = _make_gate(settings)
    thread = threading.Thread(
        target=run_consumer,
        args=(app.state.bus, store, gate, DryRunRemediator(), AlwaysHealthyChecker(),
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
