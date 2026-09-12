"""One factory selecting the store backend for ALL store-constructing services.

Shared (not per-service) so governance/action/feedback/rca never diverge — a
split backend would, e.g., have governance writing playbooks to Postgres while
rca reads them from files."""

from __future__ import annotations

from dataclasses import dataclass

from services.correlation.adapters.baseline_store import PostgresBaselineStore
from services.correlation.adapters.model_store import InMemoryModelStore, PostgresModelStore
from services.feedback.adapters.training_store import FileTrainingStore, PostgresTrainingStore
from services.governance.adapters.approval_store import InMemoryApprovalStore, PostgresApprovalStore
from services.governance.adapters.audit_sink import FileAuditSink, PostgresAuditSink
from services.governance.adapters.author_decision_store import (
    InMemoryAuthorDecisionStore,
    PostgresAuthorDecisionStore,
)
from services.governance.adapters.playbook_store import FilePlaybookStore, PostgresPlaybookStore
from services.governance.adapters.proposed_store import (
    InMemoryProposedPlaybookStore,
    PostgresProposedPlaybookStore,
)
from services.governance.adapters.trace_store import InMemoryTraceStore, PostgresTraceStore


@dataclass
class Stores:
    audit_sink: object
    playbook_store: object
    training_store: object
    engine: object | None
    approval_store: object
    baseline_store: object | None
    model_store: object | None
    author_decision_store: object
    trace_store: object
    proposed_store: object


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
            model_store=PostgresModelStore(engine),
            author_decision_store=PostgresAuthorDecisionStore(engine),
            trace_store=PostgresTraceStore(engine),
            proposed_store=PostgresProposedPlaybookStore(engine),
        )
    return Stores(
        audit_sink=FileAuditSink(settings.audit_store_path),
        playbook_store=FilePlaybookStore(settings.playbook_store_path),
        training_store=FileTrainingStore(settings.training_store_path),
        engine=None,
        approval_store=InMemoryApprovalStore(),
        baseline_store=None,
        # In file mode the model artifact has no durable home, but an in-process
        # store still lets POST /retrain save a fit and a later in-process reload
        # pick it up (mirrors InMemoryApprovalStore's file-mode posture).
        model_store=InMemoryModelStore(),
        # No file-backed AuthorDecisionStore exists (or is needed): in-memory is
        # an acceptable dev/test posture for the non-postgres path, same as
        # InMemoryApprovalStore/InMemoryModelStore above — the author's decision
        # history just doesn't survive a restart outside of Postgres.
        author_decision_store=InMemoryAuthorDecisionStore(),
        # Same posture as author_decision_store above: no file-backed trace
        # store exists or is needed — the agent-run trace just doesn't survive
        # a restart outside of Postgres.
        trace_store=InMemoryTraceStore(),
        # Same posture again: no file-backed proposed-playbook store exists or
        # is needed — a pending AI runbook proposal just doesn't survive a
        # restart outside of Postgres (issue #56 fixes that for the postgres
        # backend above).
        proposed_store=InMemoryProposedPlaybookStore(),
    )
