# Slice 0 — Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the IntelliOps monorepo skeleton — a `uv` project, a tested `common/` shared library (contracts, interfaces, config, Redis bus client), and all six FastAPI services as health-check stubs, all runnable together via `docker compose up`.

**Architecture:** A single `uv`-managed Python package. `common/` holds the load-bearing Pydantic data contracts, the pluggable adapter interfaces (as `Protocol`s), a `BusClient` interface with a Redis Streams implementation, and config. Each of the six services is a thin FastAPI app exposing `/health` and holding a wired-but-inert bus client. Docker Compose runs Redis plus the six services. Nothing "smart" happens yet — this slice proves the spine runs and the contracts are correct.

**Tech Stack:** Python 3.11 · uv · FastAPI · Pydantic v2 · Redis (redis-py, Streams) · pytest · Docker Compose · Ruff

**Spec:** [docs/superpowers/specs/2026-08-13-intelliops-coe-design.md](../specs/2026-08-13-intelliops-coe-design.md)

## Global Constraints

- **Python floor:** 3.11 (`requires-python = ">=3.11"`).
- **Package manager:** `uv` only. Never invoke bare `pip`; use `uv add` / `uv run`.
- **Pydantic:** v2 API (`model_config = ConfigDict(...)`, `model_dump()`, `field_validator`). No v1 idioms.
- **Contracts are load-bearing:** every model in `common/contracts.py` must round-trip
  (`Model.model_validate(m.model_dump()) == m`) and is tested before any service uses it.
- **Interfaces before implementations:** services depend on the `Protocol`s in `common/interfaces.py`, never directly on Redis/Kafka/K8s classes.
- **Bus is swappable:** all bus access goes through the `BusClient` protocol. The Redis impl is one binding; a `FakeBus` is used in tests.
- **Enums, not free strings:** `Situation.status`, `Playbook.hitl_mode`, `RemediationOutcome.result` are `str, Enum` types with exactly the values named in the spec.
- **Service ports:** ingestion 8001, correlation 8002, rca 8003, action 8004, governance 8005, feedback 8006.
- **Every service exposes** `GET /health` → `{"service": "<name>", "status": "ok"}` (HTTP 200).
- **Line endings:** repo is developed on Windows; `.gitignore` and `.gitattributes` already handle CRLF. Don't fight Git's LF→CRLF warnings.
- **Test command:** `uv run pytest` from repo root. **Lint:** `uv run ruff check .`.

---

### Task 1: Bootstrap the uv project

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Create: `.gitattributes`
- Create: `common/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/test_smoke.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: a working `uv` environment where `uv run pytest` executes. Later tasks assume `uv add <pkg>` works and that `common/` and `tests/` are importable packages.

- [ ] **Step 1: Initialize the uv project**

Run from repo root (`C:\Project\intelliops`):

```bash
uv init --bare --python 3.11
```

This creates `pyproject.toml` and `.python-version` without a sample `hello.py`.

- [ ] **Step 2: Set project metadata and dependencies in `pyproject.toml`**

Replace the generated `pyproject.toml` with:

```toml
[project]
name = "intelliops"
version = "0.0.0"
description = "Agentic AIOps for automated incident detection, diagnosis & remediation"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "pydantic>=2.9",
    "pydantic-settings>=2.5",
    "redis>=5.1",
]

[dependency-groups]
dev = [
    "pytest>=8.3",
    "httpx>=0.27",
    "ruff>=0.7",
]

[tool.pytest.ini_options]
testpaths = ["tests", "common", "services"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.setuptools.packages.find]
include = ["common*", "services*"]
```

- [ ] **Step 3: Create `.gitattributes` to normalize line endings**

```gitattributes
* text=auto eol=lf
*.png binary
*.jpg binary
```

- [ ] **Step 4: Create the package marker files**

`common/__init__.py`:

```python
"""IntelliOps shared library: contracts, interfaces, bus client, config."""
```

`tests/__init__.py`:

```python
```

(empty file)

- [ ] **Step 5: Write a smoke test**

`tests/test_smoke.py`:

```python
def test_environment_is_wired():
    """The test runner and package imports work."""
    import common

    assert common.__doc__ is not None
```

- [ ] **Step 6: Sync the environment and run the smoke test**

```bash
uv sync
uv run pytest tests/test_smoke.py -v
```

Expected: PASS (1 passed).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .python-version .gitattributes uv.lock common/__init__.py tests/__init__.py tests/test_smoke.py
git commit -m "chore: bootstrap uv project skeleton"
```

---

### Task 2: Data contracts (`common/contracts.py`)

**Files:**
- Create: `common/contracts.py`
- Test: `tests/test_contracts.py`

**Interfaces:**
- Consumes: Pydantic v2 (from Task 1).
- Produces: the following importable from `common.contracts` — enums `SituationStatus`, `HitlMode`, `RemediationResult`, `TelemetryKind`; models `TelemetryEvent`, `Situation`, `RootCauseHypothesis`, `Playbook`, `ApprovalRequest`, `RemediationOutcome`, `AuditRecord`. Field names and types exactly as below; every model is a `pydantic.BaseModel`. These are the currency every later task passes over the bus.

- [ ] **Step 1: Write the failing test**

`tests/test_contracts.py`:

```python
from datetime import datetime, timezone

import pytest

from common.contracts import (
    ApprovalRequest,
    AuditRecord,
    HitlMode,
    Playbook,
    RemediationOutcome,
    RemediationResult,
    RootCauseHypothesis,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
)

NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)


def _telemetry_event() -> TelemetryEvent:
    return TelemetryEvent(
        source="prometheus",
        kind=TelemetryKind.METRIC,
        name="cpu_usage",
        value=0.97,
        labels={"pod": "web-1"},
        ts=NOW,
        fingerprint="abc123",
    )


@pytest.mark.parametrize(
    "model",
    [
        _telemetry_event(),
        Situation(
            id="s1",
            status=SituationStatus.DETECTED,
            member_events=[_telemetry_event()],
            severity="high",
            first_seen=NOW,
            last_seen=NOW,
            signature="sig1",
        ),
        RootCauseHypothesis(
            situation_id="s1",
            description="pod crash loop",
            confidence=0.8,
            evidence=["restart count spiked"],
            suggested_runbook_id="restart-pod",
        ),
        Playbook(
            id="restart-pod",
            name="Restart Pod",
            match_rule="signature == 'sig1'",
            steps=["kubectl rollout restart deploy/web"],
            hitl_mode=HitlMode.HITL,
            reversible=True,
            rollback_steps=["kubectl rollout undo deploy/web"],
        ),
        ApprovalRequest(
            id="a1",
            situation_id="s1",
            playbook_id="restart-pod",
            requested_by="action-service",
            status="pending",
        ),
        RemediationOutcome(
            situation_id="s1",
            playbook_id="restart-pod",
            result=RemediationResult.SUCCESS,
            health_after="healthy",
            ts=NOW,
        ),
        AuditRecord(
            actor="action-service",
            action="execute_playbook",
            resource="restart-pod",
            decision="allow",
            ts=NOW,
            correlation_id="corr-1",
        ),
    ],
)
def test_contract_roundtrips(model):
    """Every contract survives a dump -> validate round-trip unchanged."""
    restored = type(model).model_validate(model.model_dump())
    assert restored == model


def test_enums_have_exact_values():
    assert {s.value for s in SituationStatus} == {
        "detected",
        "diagnosed",
        "acting",
        "resolved",
        "failed",
    }
    assert {m.value for m in HitlMode} == {"auto", "hitl", "disabled"}
    assert {r.value for r in RemediationResult} == {"success", "failure", "rolled_back"}


def test_reversible_playbook_defaults():
    pb = Playbook(id="p", name="p", match_rule="true", steps=["x"], hitl_mode=HitlMode.AUTO)
    assert pb.reversible is False
    assert pb.rollback_steps == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_contracts.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'common.contracts'`.

- [ ] **Step 3: Write the contracts**

`common/contracts.py`:

```python
"""Canonical data contracts passed between IntelliOps services.

These models are load-bearing: they are the shared vocabulary every service
uses over the bus. Defined once here so services cannot drift (see ADR-006).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class TelemetryKind(str, Enum):
    METRIC = "metric"
    LOG = "log"
    TRACE = "trace"


class SituationStatus(str, Enum):
    DETECTED = "detected"
    DIAGNOSED = "diagnosed"
    ACTING = "acting"
    RESOLVED = "resolved"
    FAILED = "failed"


class HitlMode(str, Enum):
    AUTO = "auto"
    HITL = "hitl"
    DISABLED = "disabled"


class RemediationResult(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    ROLLED_BACK = "rolled_back"


class TelemetryEvent(BaseModel):
    """A single normalized signal from any telemetry source."""

    source: str
    kind: TelemetryKind
    name: str
    value: float | None = None
    payload: dict | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    ts: datetime
    fingerprint: str


class Situation(BaseModel):
    """An alert storm collapsed into one incident — the universal currency."""

    id: str
    status: SituationStatus
    member_events: list[TelemetryEvent] = Field(default_factory=list)
    severity: str
    first_seen: datetime
    last_seen: datetime
    signature: str


class RootCauseHypothesis(BaseModel):
    situation_id: str
    description: str
    confidence: float
    evidence: list[str] = Field(default_factory=list)
    suggested_runbook_id: str | None = None


class Playbook(BaseModel):
    id: str
    name: str
    match_rule: str
    steps: list[str] = Field(default_factory=list)
    hitl_mode: HitlMode
    reversible: bool = False
    rollback_steps: list[str] = Field(default_factory=list)


class ApprovalRequest(BaseModel):
    id: str
    situation_id: str
    playbook_id: str
    requested_by: str
    status: str = "pending"
    decided_by: str | None = None


class RemediationOutcome(BaseModel):
    situation_id: str
    playbook_id: str
    result: RemediationResult
    health_after: str
    ts: datetime


class AuditRecord(BaseModel):
    actor: str
    action: str
    resource: str
    decision: str
    ts: datetime
    correlation_id: str
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_contracts.py -v
```

Expected: PASS (all round-trip, enum, and default cases green).

- [ ] **Step 5: Commit**

```bash
git add common/contracts.py tests/test_contracts.py
git commit -m "feat: add shared data contracts"
```

---

### Task 3: Adapter interfaces (`common/interfaces.py`)

**Files:**
- Create: `common/interfaces.py`
- Test: `tests/test_interfaces.py`

**Interfaces:**
- Consumes: `common.contracts` (Task 2).
- Produces: `Protocol`s importable from `common.interfaces` — `BusClient`, `TelemetrySource`, `Correlator`, `Remediator`, `AuditSink`. These are the swap points every service programs against. Exact method signatures below; later tasks (and the Redis bus in Task 4) must satisfy `BusClient`.

- [ ] **Step 1: Write the failing test**

`tests/test_interfaces.py`:

```python
from datetime import datetime, timezone

from common.contracts import AuditRecord, TelemetryEvent, TelemetryKind
from common.interfaces import (
    AuditSink,
    BusClient,
    Correlator,
    Remediator,
    TelemetrySource,
)


class FakeBus:
    """A structural BusClient used to prove the Protocol is satisfiable."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    def publish(self, topic: str, message: dict) -> None:
        self.published.append((topic, message))

    def consume(self, topic: str, group: str):
        yield from ()


class FakeAudit:
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def write(self, record: AuditRecord) -> None:
        self.records.append(record)


def test_fakebus_satisfies_protocol():
    bus: BusClient = FakeBus()
    bus.publish("telemetry.raw", {"k": "v"})
    assert isinstance(bus, BusClient)  # runtime_checkable


def test_fake_audit_satisfies_protocol():
    sink: AuditSink = FakeAudit()
    sink.write(
        AuditRecord(
            actor="a",
            action="b",
            resource="c",
            decision="allow",
            ts=datetime(2026, 8, 13, tzinfo=timezone.utc),
            correlation_id="x",
        )
    )
    assert isinstance(sink, AuditSink)


def test_protocols_are_importable():
    # Presence + runtime-checkable is enough at skeleton stage.
    for proto in (BusClient, TelemetrySource, Correlator, Remediator, AuditSink):
        assert hasattr(proto, "__protocol_attrs__") or hasattr(proto, "_is_protocol")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_interfaces.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'common.interfaces'`.

- [ ] **Step 3: Write the interfaces**

`common/interfaces.py`:

```python
"""Pluggable adapter interfaces (Protocols).

Services depend on these, never on concrete tools (Redis/Kafka/K8s/Ansible),
so implementations are swappable and tests can bind fakes (see ADR-005).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from common.contracts import AuditRecord, Situation, TelemetryEvent


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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_interfaces.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add common/interfaces.py tests/test_interfaces.py
git commit -m "feat: add pluggable adapter interfaces"
```

---

### Task 4: Config + Redis bus client (`common/config.py`, `common/bus.py`)

**Files:**
- Create: `common/config.py`
- Create: `common/bus.py`
- Test: `tests/test_bus.py`

**Interfaces:**
- Consumes: `common.interfaces.BusClient` (Task 3).
- Produces: `common.config.Settings` (a `pydantic_settings.BaseSettings` with `redis_url: str`, default `redis://localhost:6379`) and `get_settings() -> Settings`; `common.bus.RedisBus` implementing `BusClient` over Redis Streams, plus a module-level `make_bus(settings) -> BusClient` factory. Every service (Task 5) constructs its bus via `make_bus(get_settings())`.

- [ ] **Step 1: Write the failing test**

`tests/test_bus.py` — this test uses a **fakeredis** in-memory server so it needs no running Redis:

```python
import pytest

from common.contracts import TelemetryEvent, TelemetryKind
from common.interfaces import BusClient


@pytest.fixture()
def redis_bus():
    fakeredis = pytest.importorskip("fakeredis")
    from common.bus import RedisBus

    client = fakeredis.FakeStrictRedis(decode_responses=True)
    return RedisBus(client=client)


def test_redisbus_satisfies_protocol(redis_bus):
    assert isinstance(redis_bus, BusClient)


def test_publish_then_consume_roundtrips(redis_bus):
    redis_bus.publish("telemetry.raw", {"name": "cpu", "value": "0.9"})
    messages = list(_take(redis_bus.consume("telemetry.raw", group="g1"), 1))
    assert messages[0]["name"] == "cpu"
    assert messages[0]["value"] == "0.9"


def test_settings_default_redis_url():
    from common.config import get_settings

    assert get_settings().redis_url.startswith("redis://")


def _take(iterator, n):
    out = []
    for item in iterator:
        out.append(item)
        if len(out) >= n:
            break
    return out
```

- [ ] **Step 2: Add the fakeredis dev dependency**

```bash
uv add --dev fakeredis
```

- [ ] **Step 3: Run test to verify it fails**

```bash
uv run pytest tests/test_bus.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'common.bus'`.

- [ ] **Step 4: Write the config**

`common/config.py`:

```python
"""Runtime configuration, sourced from environment variables."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INTELLIOPS_", env_file=".env")

    redis_url: str = "redis://localhost:6379"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 5: Write the Redis bus**

`common/bus.py`:

```python
"""Event-bus client. Redis Streams is the dev binding of the BusClient protocol.

Consumer groups make delivery durable and load-balanced. `consume` blocks for
new entries and yields decoded field dicts. A `make_bus` factory lets services
stay unaware of the concrete implementation (see ADR-001, ADR-005).
"""

from __future__ import annotations

from collections.abc import Iterator

import redis

from common.config import Settings

_CONSUMER = "c1"


class RedisBus:
    def __init__(self, client: redis.Redis) -> None:
        self._r = client

    def publish(self, topic: str, message: dict) -> None:
        self._r.xadd(topic, message)

    def consume(self, topic: str, group: str) -> Iterator[dict]:
        try:
            self._r.xgroup_create(topic, group, id="0", mkstream=True)
        except redis.ResponseError as exc:  # group already exists
            if "BUSYGROUP" not in str(exc):
                raise
        while True:
            resp = self._r.xreadgroup(group, _CONSUMER, {topic: ">"}, count=1, block=1000)
            if not resp:
                continue
            for _stream, entries in resp:
                for entry_id, fields in entries:
                    self._r.xack(topic, group, entry_id)
                    yield fields


def make_bus(settings: Settings) -> RedisBus:
    return RedisBus(client=redis.from_url(settings.redis_url, decode_responses=True))
```

- [ ] **Step 6: Run test to verify it passes**

```bash
uv run pytest tests/test_bus.py -v
```

Expected: PASS (fakeredis backs the `RedisBus`; publish→consume round-trips).

- [ ] **Step 7: Commit**

```bash
git add common/config.py common/bus.py tests/test_bus.py pyproject.toml uv.lock
git commit -m "feat: add config and Redis bus client"
```

---

### Task 5: Service app factory + the six service stubs

**Files:**
- Create: `services/__init__.py`
- Create: `services/base.py`
- Create: `services/ingestion/__init__.py`, `services/ingestion/app.py`
- Create: `services/correlation/__init__.py`, `services/correlation/app.py`
- Create: `services/rca/__init__.py`, `services/rca/app.py`
- Create: `services/action/__init__.py`, `services/action/app.py`
- Create: `services/governance/__init__.py`, `services/governance/app.py`
- Create: `services/feedback/__init__.py`, `services/feedback/app.py`
- Test: `tests/test_services.py`

**Interfaces:**
- Consumes: `common.bus.make_bus`, `common.config.get_settings` (Task 4).
- Produces: `services.base.create_app(service_name: str) -> fastapi.FastAPI` — an app with `GET /health` returning `{"service": service_name, "status": "ok"}`, and `app.state.bus` set to a `BusClient`. Each service module exposes a module-level `app`. Uvicorn entrypoint per service is `services.<name>.app:app`.

- [ ] **Step 1: Write the failing test**

`tests/test_services.py`:

```python
import importlib

import pytest
from fastapi.testclient import TestClient

SERVICES = [
    ("ingestion", "ingestion-service"),
    ("correlation", "correlation-service"),
    ("rca", "rca-service"),
    ("action", "action-service"),
    ("governance", "governance-service"),
    ("feedback", "feedback-service"),
]


@pytest.mark.parametrize("module_name, service_name", SERVICES)
def test_health_endpoint(module_name, service_name):
    mod = importlib.import_module(f"services.{module_name}.app")
    client = TestClient(mod.app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"service": service_name, "status": "ok"}


def test_create_app_attaches_bus():
    from common.interfaces import BusClient
    from services.base import create_app

    app = create_app("test-service")
    assert isinstance(app.state.bus, BusClient)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_services.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'services'`.

- [ ] **Step 3: Write the app factory**

`services/__init__.py`:

```python
"""IntelliOps services: thin FastAPI apps, one per architectural role."""
```

`services/base.py`:

```python
"""Shared FastAPI app factory for all six services.

At skeleton stage every service is identical: a /health endpoint and a bus
client on app.state. Service-specific handlers arrive in later slices.
"""

from __future__ import annotations

from fastapi import FastAPI

from common.bus import make_bus
from common.config import get_settings


def create_app(service_name: str) -> FastAPI:
    app = FastAPI(title=f"IntelliOps · {service_name}")
    app.state.bus = make_bus(get_settings())

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"service": service_name, "status": "ok"}

    return app
```

- [ ] **Step 4: Write the six service modules**

For each service create an `__init__.py` containing a one-line docstring and an `app.py`. The
`app.py` files are identical except for the service name passed in.

`services/ingestion/__init__.py` (repeat the pattern for each, changing the name):

```python
"""Ingestion service: normalize + dedup telemetry onto the bus."""
```

`services/ingestion/app.py`:

```python
from services.base import create_app

app = create_app("ingestion-service")
```

`services/correlation/__init__.py`:

```python
"""Correlation service: anomaly detection + event clustering -> Situation."""
```

`services/correlation/app.py`:

```python
from services.base import create_app

app = create_app("correlation-service")
```

`services/rca/__init__.py`:

```python
"""RCA service: enrich a Situation and rank root-cause hypotheses."""
```

`services/rca/app.py`:

```python
from services.base import create_app

app = create_app("rca-service")
```

`services/action/__init__.py`:

```python
"""Action service: HITL-gated, reversible remediation."""
```

`services/action/app.py`:

```python
from services.base import create_app

app = create_app("action-service")
```

`services/governance/__init__.py`:

```python
"""Governance service: RBAC gate, audit log, playbook registry."""
```

`services/governance/app.py`:

```python
from services.base import create_app

app = create_app("governance-service")
```

`services/feedback/__init__.py`:

```python
"""Feedback service: label outcomes, close the loop, compute metrics."""
```

`services/feedback/app.py`:

```python
from services.base import create_app

app = create_app("feedback-service")
```

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/test_services.py -v
```

Expected: PASS (6 health checks + bus-attachment test green).

- [ ] **Step 6: Run the full suite and lint**

```bash
uv run pytest
uv run ruff check .
```

Expected: all tests pass; ruff reports no errors.

- [ ] **Step 7: Commit**

```bash
git add services/ tests/test_services.py
git commit -m "feat: add app factory and six service stubs"
```

---

### Task 6: Dockerize — compose stack with Redis + six services

**Files:**
- Create: `deploy/Dockerfile`
- Create: `deploy/docker-compose.yml`
- Create: `.dockerignore`
- Create: `deploy/README.md`

**Interfaces:**
- Consumes: the six `services.<name>.app:app` entrypoints (Task 5), `INTELLIOPS_REDIS_URL` env var read by `common.config.Settings` (Task 4).
- Produces: a `docker compose up` stack — one `redis` service and six app services on ports 8001–8006, each reachable at `/health`. This is the slice's final acceptance deliverable.

- [ ] **Step 1: Write the Dockerfile**

`deploy/Dockerfile` (a single image; each container overrides the service module via `SERVICE_MODULE`):

```dockerfile
FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY common/ ./common/
COPY services/ ./services/
RUN uv sync --frozen --no-dev

ENV SERVICE_MODULE=services.ingestion.app:app
ENV PORT=8000
CMD uv run uvicorn "$SERVICE_MODULE" --host 0.0.0.0 --port "$PORT"
```

- [ ] **Step 2: Write `.dockerignore`**

`.dockerignore`:

```dockerignore
.git
.venv
__pycache__
*.pyc
.pytest_cache
.ruff_cache
docs
tests
data
models
```

- [ ] **Step 3: Write the compose file**

`deploy/docker-compose.yml`:

```yaml
name: intelliops

x-service: &service
  build:
    context: ..
    dockerfile: deploy/Dockerfile
  environment:
    INTELLIOPS_REDIS_URL: redis://redis:6379
  depends_on:
    redis:
      condition: service_healthy

services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  ingestion:
    <<: *service
    environment:
      INTELLIOPS_REDIS_URL: redis://redis:6379
      SERVICE_MODULE: services.ingestion.app:app
      PORT: "8000"
    ports:
      - "8001:8000"

  correlation:
    <<: *service
    environment:
      INTELLIOPS_REDIS_URL: redis://redis:6379
      SERVICE_MODULE: services.correlation.app:app
      PORT: "8000"
    ports:
      - "8002:8000"

  rca:
    <<: *service
    environment:
      INTELLIOPS_REDIS_URL: redis://redis:6379
      SERVICE_MODULE: services.rca.app:app
      PORT: "8000"
    ports:
      - "8003:8000"

  action:
    <<: *service
    environment:
      INTELLIOPS_REDIS_URL: redis://redis:6379
      SERVICE_MODULE: services.action.app:app
      PORT: "8000"
    ports:
      - "8004:8000"

  governance:
    <<: *service
    environment:
      INTELLIOPS_REDIS_URL: redis://redis:6379
      SERVICE_MODULE: services.governance.app:app
      PORT: "8000"
    ports:
      - "8005:8000"

  feedback:
    <<: *service
    environment:
      INTELLIOPS_REDIS_URL: redis://redis:6379
      SERVICE_MODULE: services.feedback.app:app
      PORT: "8000"
    ports:
      - "8006:8000"
```

- [ ] **Step 4: Write `deploy/README.md`**

`deploy/README.md`:

````markdown
# Deploy (dev)

Bring up Redis + the six services:

```bash
docker compose -f deploy/docker-compose.yml up --build
```

Health-check every service:

```bash
for p in 8001 8002 8003 8004 8005 8006; do curl -s localhost:$p/health; echo; done
```

Tear down:

```bash
docker compose -f deploy/docker-compose.yml down
```
````

- [ ] **Step 5: Build and start the stack**

```bash
docker compose -f deploy/docker-compose.yml up --build -d
```

Expected: `redis` becomes healthy, then all six services start.

- [ ] **Step 6: Verify all six health endpoints**

```bash
for p in 8001 8002 8003 8004 8005 8006; do curl -s localhost:$p/health; echo; done
```

Expected output (order may vary):

```
{"service":"ingestion-service","status":"ok"}
{"service":"correlation-service","status":"ok"}
{"service":"rca-service","status":"ok"}
{"service":"action-service","status":"ok"}
{"service":"governance-service","status":"ok"}
{"service":"feedback-service","status":"ok"}
```

- [ ] **Step 7: Tear the stack down**

```bash
docker compose -f deploy/docker-compose.yml down
```

- [ ] **Step 8: Commit**

```bash
git add deploy/ .dockerignore
git commit -m "feat: add docker-compose dev stack"
```

---

### Task 7: Slice-0 acceptance — wire the docs to reality

**Files:**
- Modify: `README.md` (Roadmap table: flip Slice 0 status to done; fix the quickstart note)
- Create: `tests/test_slice0_acceptance.py`

**Interfaces:**
- Consumes: everything above.
- Produces: an acceptance test that asserts the full package surface imports and every service app is constructable in-process (no Docker needed for CI), plus updated docs.

- [ ] **Step 1: Write the acceptance test**

`tests/test_slice0_acceptance.py`:

```python
"""Slice-0 acceptance: the skeleton is fully wired and importable."""

import importlib

from fastapi import FastAPI


def test_common_surface_imports():
    from common import bus, config, contracts, interfaces

    assert hasattr(contracts, "Situation")
    assert hasattr(interfaces, "BusClient")
    assert hasattr(bus, "RedisBus")
    assert hasattr(config, "get_settings")


def test_all_service_apps_construct():
    for name in ("ingestion", "correlation", "rca", "action", "governance", "feedback"):
        mod = importlib.import_module(f"services.{name}.app")
        assert isinstance(mod.app, FastAPI)
```

- [ ] **Step 2: Run it**

```bash
uv run pytest tests/test_slice0_acceptance.py -v
```

Expected: PASS.

- [ ] **Step 3: Update the README Roadmap row for Slice 0**

In `README.md`, change the Slice 0 status cell from `⏳ planned` to `✅ done`, and change the
quickstart caveat line:

- From: `> Applies once **Slice 0** is built. Until then this repo is documentation only.`
- To: `> Slice 0 is built: the command below brings up Redis and six health-checked service stubs.`

- [ ] **Step 4: Run the entire suite and lint one final time**

```bash
uv run pytest
uv run ruff check .
```

Expected: all tests pass; no lint errors.

- [ ] **Step 5: Commit**

```bash
git add README.md tests/test_slice0_acceptance.py
git commit -m "test: add slice-0 acceptance and mark slice done"
```

---

## Self-Review

**1. Spec coverage (checked against the spec's §9 repo layout and §4–6 architecture):**
- `common/contracts.py` (spec §5) → Task 2 ✓
- `common/interfaces.py` (spec §6) → Task 3 ✓
- `common/bus.py` + `BusClient` (spec §4.2, ADR-001) → Task 4 ✓
- `common/config.py` → Task 4 ✓
- Six services with `/health` (spec §4.1) → Task 5 ✓
- `deploy/docker-compose.yml` with Redis dev bus (spec §9, §10 Slice 0) → Task 6 ✓
- `pyproject.toml`, repo layout (spec §9) → Task 1 ✓
- *Deferred by design for Slice 0, not gaps:* `playbooks/` (seeded in Slice 3 when the registry exists), Postgres audit/training store (Slice 2/4), any anomaly/RCA/action logic (Slices 1–4). Slice 0's spec scope is explicitly "skeleton: contracts, bus, compose, health endpoints" — matches.

**2. Placeholder scan:** No "TBD/TODO/handle edge cases/similar to Task N". Every code step shows full content; the repeated service `app.py` is spelled out per service rather than referenced. ✓

**3. Type consistency:**
- `create_app(service_name: str) -> FastAPI` — defined Task 5, used identically in Tasks 5 & 7. ✓
- `make_bus(settings) -> RedisBus` (satisfies `BusClient`) — defined Task 4, consumed in Task 5's `create_app`. ✓
- `get_settings() -> Settings` — defined Task 4, used Task 5. ✓
- Enum values (`SituationStatus`/`HitlMode`/`RemediationResult`) — asserted in Task 2 exactly as the spec §5 lists them. ✓
- `BusClient.publish/consume`, `AuditSink.write` signatures — defined Task 3, `RedisBus` matches in Task 4. ✓
- Service names (`"ingestion-service"` … `"feedback-service"`) — consistent between Task 5 modules and the Task 5/7 tests. ✓

No issues found.
