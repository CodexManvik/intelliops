"""Bus consumer for rca-service.

Consumes situations.detected, diagnoses each (enrich → rank → surface runbook),
marks it diagnosed, publishes a DiagnosedSituation to situations.diagnosed, and
writes an audit record threaded by the situation id. Runs in a daemon thread
started by the FastAPI lifespan; a stop_event allows clean shutdown.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from common.contracts import (
    AuditRecord,
    DiagnosedSituation,
    Situation,
    SituationStatus,
)
from common.envelope import iter_models, publish_model
from common.interfaces import AuditSink, ContextProvider, PlaybookStore
from services.rca.enrich import enrich
from services.rca.rank import rank_hypotheses, surface_runbook


def diagnose(
    situation: Situation, provider: ContextProvider, store: PlaybookStore
) -> DiagnosedSituation:
    context = enrich(situation, provider)
    hypotheses = rank_hypotheses(situation, context)
    runbook = surface_runbook(hypotheses, store)
    diagnosed_situation = situation.model_copy(update={"status": SituationStatus.DIAGNOSED})
    return DiagnosedSituation(
        situation=diagnosed_situation,
        hypotheses=hypotheses,
        suggested_runbook_id=runbook.id
        if runbook is not None
        else hypotheses[0].suggested_runbook_id,
    )


def run_consumer(
    bus,
    provider: ContextProvider,
    store: PlaybookStore,
    audit_sink: AuditSink,
    stop_event: threading.Event,
) -> None:
    for situation in iter_models(bus, "situations.detected", "rca", Situation):
        if stop_event.is_set():
            break
        diagnosed = diagnose(situation, provider, store)
        publish_model(bus, "situations.diagnosed", diagnosed)
        audit_sink.write(
            AuditRecord(
                actor="rca-service",
                action="diagnose",
                resource=f"situation:{situation.id}",
                decision="allow",
                ts=datetime.now(UTC),
                correlation_id=situation.id,
            )
        )
