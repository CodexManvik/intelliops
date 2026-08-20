"""One factory selecting the store backend for ALL store-constructing services.

Shared (not per-service) so governance/action/feedback/rca never diverge — a
split backend would, e.g., have governance writing playbooks to Postgres while
rca reads them from files."""

from __future__ import annotations

from dataclasses import dataclass

from services.correlation.adapters.baseline_store import PostgresBaselineStore
from services.feedback.adapters.training_store import FileTrainingStore, PostgresTrainingStore
from services.governance.adapters.approval_store import InMemoryApprovalStore, PostgresApprovalStore
from services.governance.adapters.audit_sink import FileAuditSink, PostgresAuditSink
from services.governance.adapters.playbook_store import FilePlaybookStore, PostgresPlaybookStore


@dataclass
class Stores:
    audit_sink: object
    playbook_store: object
    training_store: object
    engine: object | None
    approval_store: object
    baseline_store: object | None


def make_stores(settings) -> Stores:
    if settings.store_backend == "postgres":
        from common.db import make_engine

        engine = make_engine(settings.database_url)
        return Stores(
            audit_sink=PostgresAuditSink(engine),
            playbook_store=PostgresPlaybookStore(engine, seed_path=settings.playbook_store_path),
            training_store=PostgresTrainingStore(engine),
            engine=engine,
            approval_store=PostgresApprovalStore(engine),
            baseline_store=PostgresBaselineStore(engine),
        )
    return Stores(
        audit_sink=FileAuditSink(settings.audit_store_path),
        playbook_store=FilePlaybookStore(settings.playbook_store_path),
        training_store=FileTrainingStore(settings.training_store_path),
        engine=None,
        approval_store=InMemoryApprovalStore(),
        baseline_store=None,
    )
