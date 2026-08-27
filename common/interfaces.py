"""Pluggable adapter interfaces (Protocols).

Services depend on these, never on concrete tools (Redis/Kafka/K8s/Ansible),
so implementations are swappable and tests can bind fakes (see ADR-005).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from common.contracts import (
    ApprovalRequest,
    AuditRecord,
    EnrichmentContext,
    Playbook,
    RemediationPlan,
    RemediationTarget,
    RootCauseHypothesis,
    Situation,
    TelemetryEvent,
    TrainingRecord,
)


@runtime_checkable
class BusClient(Protocol):
    """The event-bus spine. Redis Streams (dev) / Kafka (prod) implement this."""

    def publish(self, topic: str, message: dict) -> None: ...

    def consume(self, topic: str, group: str) -> Iterator[dict]: ...

    def ping(self) -> None: ...


@runtime_checkable
class TelemetrySource(Protocol):
    """A source of raw telemetry (Prometheus, Loki, OpenTelemetry)."""

    def poll(self) -> list[TelemetryEvent]: ...

    def subscribe(self) -> Iterator[TelemetryEvent]: ...


@runtime_checkable
class Correlator(Protocol):
    """Anomaly detection + event clustering (River, scikit-learn)."""

    def detect(self, event: TelemetryEvent) -> float: ...

    def correlate(self, events: list[TelemetryEvent]) -> Situation: ...

    def retrain(self, training_data: list[dict]) -> None: ...


@runtime_checkable
class Remediator(Protocol):
    """Executes and reverses remediation (Kubernetes API, Ansible)."""

    def execute(self, plan: RemediationPlan) -> bool: ...

    def rollback(self, plan: RemediationPlan) -> bool: ...


@runtime_checkable
class AuditSink(Protocol):
    """An append-only audit store (Postgres, file)."""

    def write(self, record: AuditRecord) -> None: ...

    def records(self, correlation_id: str | None = None) -> list[AuditRecord]: ...


@runtime_checkable
class PlaybookStore(Protocol):
    """The CoE playbook registry (in-memory / file / Postgres)."""

    def register(self, playbook: Playbook) -> None: ...

    def get(self, playbook_id: str) -> Playbook | None: ...

    def list(self) -> list[Playbook]: ...


@runtime_checkable
class ApprovalStore(Protocol):
    """Pending HITL approvals (in-memory / Postgres)."""

    def create(self, request: ApprovalRequest) -> ApprovalRequest: ...

    def get(self, approval_id: str) -> ApprovalRequest | None: ...

    def decide(self, approval_id: str, status: str, decided_by: str) -> ApprovalRequest | None: ...

    def list_pending(self) -> list[ApprovalRequest]: ...


@runtime_checkable
class ContextProvider(Protocol):
    """A source of RCA enrichment context (file / Prometheus / CMDB / git)."""

    def recent_deploys(self) -> list[dict]: ...

    def topology_for(self, labels: dict[str, str]) -> dict: ...

    def config_changes(self) -> list[dict]: ...


@runtime_checkable
class GovernanceGate(Protocol):
    """The synchronous action→governance seam: RBAC, approvals, audit (ADR-003)."""

    def check_rbac(self, actor: str, action: str, resource: str) -> bool: ...

    def request_approval(self, request: ApprovalRequest) -> ApprovalRequest: ...

    def await_decision(self, approval_id: str, timeout_seconds: float) -> ApprovalRequest: ...

    def write_audit(self, record: AuditRecord) -> None: ...


@runtime_checkable
class HealthChecker(Protocol):
    """Post-remediation health signal (ADR-007 verify step)."""

    def check(self, situation: Situation, target: RemediationTarget) -> bool: ...


@runtime_checkable
class TrainingStore(Protocol):
    """The closed-loop training store: feedback appends, correlation reads (see ADR-001)."""

    def append(self, record: TrainingRecord) -> None: ...

    def read_all(self) -> list[TrainingRecord]: ...


@runtime_checkable
class ExplanationProvider(Protocol):
    """Produces a human-readable advisory explanation for the top RCA hypothesis.

    Template (offline, deterministic) is the CI-safe default; an LLM-backed
    implementation may be selected via config, but MUST NEVER raise out of the
    consumer — every failure path falls back to the template output."""

    def explain(
        self,
        hypothesis: RootCauseHypothesis,
        context: EnrichmentContext,
        situation: Situation,
    ) -> str: ...

    def explain_with_source(
        self,
        hypothesis: RootCauseHypothesis,
        context: EnrichmentContext,
        situation: Situation,
    ) -> tuple[str, str]: ...
