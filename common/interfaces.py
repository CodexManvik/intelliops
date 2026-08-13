"""Pluggable adapter interfaces (Protocols).

Services depend on these, never on concrete tools (Redis/Kafka/K8s/Ansible),
so implementations are swappable and tests can bind fakes (see ADR-005).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from common.contracts import AuditRecord, Playbook, Situation, TelemetryEvent


@runtime_checkable
class BusClient(Protocol):
    """The event-bus spine. Redis Streams (dev) / Kafka (prod) implement this."""

    def publish(self, topic: str, message: dict) -> None: ...

    def consume(self, topic: str, group: str) -> Iterator[dict]: ...


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

    def execute(self, steps: list[str]) -> bool: ...

    def rollback(self, steps: list[str]) -> bool: ...


@runtime_checkable
class AuditSink(Protocol):
    """An append-only audit store (Postgres, file)."""

    def write(self, record: AuditRecord) -> None: ...


@runtime_checkable
class PlaybookStore(Protocol):
    """The CoE playbook registry (in-memory / file / Postgres)."""

    def register(self, playbook: Playbook) -> None: ...

    def get(self, playbook_id: str) -> Playbook | None: ...

    def list(self) -> list[Playbook]: ...


@runtime_checkable
class ContextProvider(Protocol):
    """A source of RCA enrichment context (file / Prometheus / CMDB / git)."""

    def recent_deploys(self) -> list[dict]: ...

    def topology_for(self, labels: dict[str, str]) -> dict: ...

    def config_changes(self) -> list[dict]: ...
