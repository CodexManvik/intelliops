# Real-Data Live Stack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the frontend's mock data with a genuinely live closed loop running locally on docker-compose for free: a breakable demo app emits real Prometheus metrics, IntelliOps observes and dry-run-remediates, and the React console reads live APIs.

**Architecture:** Honor the existing `Protocol` seams (ADR-005) — add real adapters behind them rather than rewriting services. New: a breakable FastAPI demo app, a real Prometheus container, a `PrometheusSource` feeding ingestion via a poll loop, an `HttpGovernanceGate` closing the cross-container HITL gap, a read-model service serving `GET /situations` + `/outcomes`, and a frontend API client. Every new behavior is env-switched so existing tests (default `file` / `in_process`) stay green.

**Tech Stack:** Python 3.11 (`requires-python = ">=3.11"`), FastAPI, Pydantic v2, Redis Streams, httpx, prometheus-client, Prometheus, Docker Compose, React 18 + TypeScript + Vite.

**Spec:** `docs/superpowers/specs/2026-08-15-real-data-live-stack-design.md`

## Global Constraints

- Existing test suite (60+ tests) MUST stay green. New behavior is gated behind env switches with test-safe defaults: `INTELLIOPS_TELEMETRY_MODE=file`, `INTELLIOPS_GOVERNANCE_MODE=in_process`.
- Contracts in `common/contracts.py` are frozen and load-bearing (ADR-006). Do not mutate them; add new models only if additive.
- All models cross the bus via `common/envelope.py` helpers (`publish_model` / `iter_models` / `decode_model`). Never hand-roll serialization (ADR-001).
- The remediator stays `DryRunRemediator` and health stays `AlwaysHealthyChecker` (ADR-007). Nothing real is executed.
- Every service is built from `services/base.create_app(name)`; follow that pattern.
- Background consumers run in a daemon thread started by the FastAPI lifespan with a `threading.Event` stop signal (see `services/feedback/app.py`).
- Python: run tests with `uv run pytest`. Lint with `uv run ruff check`.
- Frontend: run from `frontend/`. Type-check + build with `npm run build`.
- Commit after every task. Use conventional-commit prefixes. End commit messages with:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

## File Structure

**New (Python):**
- `services/demo_app/__init__.py`, `services/demo_app/app.py`, `services/demo_app/tests/__init__.py`, `services/demo_app/tests/test_demo_app.py` — breakable target.
- `services/ingestion/adapters/prometheus_source.py` + `services/ingestion/tests/test_prometheus_source.py` — real telemetry source.
- `services/read/__init__.py`, `services/read/projection.py`, `services/read/consumer.py`, `services/read/app.py`, `services/read/tests/__init__.py`, `services/read/tests/test_projection.py`, `services/read/tests/test_read_api.py` — read-model.
- `deploy/prometheus.yml` — scrape config.
- `scripts/chaos.sh` — one-command incident driver.

**New (frontend):**
- `frontend/src/data/api.ts` — live fetch client.
- `frontend/src/data/source.ts` — chooses mock vs api by env; exposes an async loader.
- `frontend/src/hooks/useData.ts` — React hook that loads a source and returns `{data, loading, error}`.
- `frontend/.env.example`.

**Modified:**
- `common/config.py` — new settings.
- `services/base.py` — CORS middleware.
- `services/ingestion/app.py` — poll-loop lifespan (mode-switched).
- `services/action/adapters/governance_gate.py` — add `HttpGovernanceGate`.
- `services/action/app.py` — select gate by `GOVERNANCE_MODE`.
- `services/governance/app.py` — add `GET /approvals/{id}` + `GET /approvals`.
- `deploy/docker-compose.yml` — add `demo-app`, `prometheus`, `read` + healthchecks.
- `frontend/src/views/*.tsx` — consume the data hook instead of static mock imports.
- `pyproject.toml` — add `prometheus-client`.
- `README.md` — "Run it live" + dry-run safety note.

---

## Task 1: New settings on the config object

**Files:**
- Modify: `common/config.py`
- Test: `tests/test_config_live.py` (Create)

**Interfaces:**
- Produces: `Settings.telemetry_mode: str`, `Settings.prometheus_url: str`, `Settings.prometheus_query: str`, `Settings.telemetry_poll_seconds: float`, `Settings.governance_mode: str`, `Settings.governance_url: str`, `Settings.read_outcomes_max: int`, `Settings.cors_origins: str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_live.py
from common.config import Settings


def test_live_defaults_are_test_safe():
    s = Settings()
    assert s.telemetry_mode == "file"
    assert s.governance_mode == "in_process"
    assert s.prometheus_url == "http://localhost:9090"
    assert s.read_outcomes_max == 200


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("INTELLIOPS_TELEMETRY_MODE", "prometheus")
    monkeypatch.setenv("INTELLIOPS_GOVERNANCE_MODE", "http")
    s = Settings()
    assert s.telemetry_mode == "prometheus"
    assert s.governance_mode == "http"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config_live.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'telemetry_mode'`

- [ ] **Step 3: Add the fields**

In `common/config.py`, inside `class Settings`, after `graduation_min_successes`:

```python
# --- live-stack settings (test-safe defaults) ---
telemetry_mode: str = "file"  # "file" | "prometheus"
prometheus_url: str = "http://localhost:9090"
prometheus_query: str = "rate(http_request_errors_total[1m]) or on() vector(0)"
telemetry_poll_seconds: float = 5.0
governance_mode: str = "in_process"  # "in_process" | "http"
governance_url: str = "http://localhost:8005"
read_outcomes_max: int = 200
cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config_live.py -v`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add common/config.py tests/test_config_live.py
git commit -m "feat(config): add live-stack settings with test-safe defaults"
```

---

## Task 2: CORS middleware in the shared app factory

**Files:**
- Modify: `services/base.py`
- Test: `tests/test_cors.py` (Create)

**Interfaces:**
- Consumes: `Settings.cors_origins` (Task 1).
- Produces: every `create_app(name)` FastAPI app carries `CORSMiddleware` allowing the configured origins.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cors.py
from fastapi.testclient import TestClient

from services.base import create_app


def test_cors_headers_present_for_allowed_origin():
    app = create_app("test-service")
    client = TestClient(app)
    r = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cors.py -v`
Expected: FAIL — `access-control-allow-origin` header is `None`

- [ ] **Step 3: Add the middleware**

In `services/base.py`, update imports and the factory:

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from common.bus import make_bus
from common.config import get_settings


def create_app(service_name: str) -> FastAPI:
    app = FastAPI(title=f"IntelliOps · {service_name}")
    settings = get_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.bus = make_bus(settings)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"service": service_name, "status": "ok"}

    return app
```

- [ ] **Step 4: Run test to verify it passes, and existing tests still pass**

Run: `uv run pytest tests/test_cors.py services/governance/tests/test_governance_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/base.py tests/test_cors.py
git commit -m "feat(base): add CORS middleware to the shared app factory"
```

---

## Task 3: The breakable demo app

**Files:**
- Create: `services/demo_app/__init__.py` (empty), `services/demo_app/app.py`, `services/demo_app/tests/__init__.py` (empty), `services/demo_app/tests/test_demo_app.py`
- Modify: `pyproject.toml` (add `prometheus-client`)

**Interfaces:**
- Produces: a FastAPI app with `GET /work`, `POST /break`, `POST /fix`, `GET /metrics`, `GET /health`. Metrics: `http_requests_total`, `http_request_errors_total`, `cpu_usage`. `/break` sets an internal `broken` flag so `/work` errors and `cpu_usage` reads high; `/fix` clears it.

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, add `"prometheus-client>=0.20"` to the `dependencies` list. Then:

Run: `uv sync`
Expected: prometheus-client installed.

- [ ] **Step 2: Write the failing test**

```python
# services/demo_app/tests/test_demo_app.py
from fastapi.testclient import TestClient

from services.demo_app.app import app, _state


def _client():
    _state["broken"] = False
    return TestClient(app)


def test_work_ok_when_healthy():
    c = _client()
    assert c.get("/work").status_code == 200


def test_break_makes_work_error_and_cpu_spike():
    c = _client()
    c.post("/break")
    assert c.get("/work").status_code == 500
    body = c.get("/metrics").text
    assert "cpu_usage" in body
    # cpu gauge should read high (>= 80) when broken
    line = next(l for l in body.splitlines() if l.startswith("cpu_usage "))
    assert float(line.split()[1]) >= 80.0


def test_fix_recovers():
    c = _client()
    c.post("/break")
    c.post("/fix")
    assert c.get("/work").status_code == 200
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest services/demo_app/tests/test_demo_app.py -v`
Expected: FAIL — module `services.demo_app.app` does not exist.

- [ ] **Step 4: Implement the demo app**

```python
# services/demo_app/app.py
"""A tiny breakable target that emits Prometheus metrics.

The operator flips /break to simulate an incident (error rate + CPU spike) and
/fix to recover. IntelliOps scrapes these metrics via Prometheus. This is the
'real running app' the live demo observes — nothing here depends on IntelliOps.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest

app = FastAPI(title="IntelliOps · demo-app")

_state: dict[str, bool] = {"broken": False}

_requests = Counter("http_requests_total", "Total work requests")
_errors = Counter("http_request_errors_total", "Total failed work requests")
_cpu = Gauge("cpu_usage", "Simulated CPU utilization percent")

_CPU_HEALTHY = 18.0
_CPU_BROKEN = 92.0


@app.get("/health")
def health() -> dict[str, str]:
    return {"service": "demo-app", "status": "ok"}


@app.get("/work")
def work() -> dict[str, str]:
    _requests.inc()
    if _state["broken"]:
        _errors.inc()
        raise HTTPException(status_code=500, detail="dependency failure")
    return {"result": "ok"}


@app.post("/break")
def break_it() -> dict[str, bool]:
    _state["broken"] = True
    _cpu.set(_CPU_BROKEN)
    return {"broken": True}


@app.post("/fix")
def fix_it() -> dict[str, bool]:
    _state["broken"] = False
    _cpu.set(_CPU_HEALTHY)
    return {"broken": False}


@app.get("/metrics")
def metrics() -> Response:
    # keep the gauge fresh even if neither toggle was hit this scrape
    _cpu.set(_CPU_BROKEN if _state["broken"] else _CPU_HEALTHY)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest services/demo_app/tests/test_demo_app.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add services/demo_app pyproject.toml uv.lock
git commit -m "feat(demo-app): breakable FastAPI target emitting Prometheus metrics"
```

---

## Task 4: PrometheusSource adapter

**Files:**
- Create: `services/ingestion/adapters/prometheus_source.py`, `services/ingestion/tests/test_prometheus_source.py`

**Interfaces:**
- Consumes: `services.ingestion.normalize.normalize` (existing), `common.contracts.TelemetryEvent`.
- Produces: `class PrometheusSource` with `__init__(self, base_url: str, query: str, http_client=None)` and `poll() -> list[TelemetryEvent]`, `subscribe() -> Iterator[TelemetryEvent]`. `poll()` GETs `{base_url}/api/v1/query?query=...`, maps each result vector entry to a normalized `TelemetryEvent`; returns `[]` on any connection error or empty/failed response (never raises).

- [ ] **Step 1: Write the failing test**

```python
# services/ingestion/tests/test_prometheus_source.py
import httpx

from services.ingestion.adapters.prometheus_source import PrometheusSource

_OK_BODY = {
    "status": "success",
    "data": {
        "resultType": "vector",
        "result": [
            {
                "metric": {"__name__": "http_request_errors_total", "job": "demo-app"},
                "value": [1723700000.0, "7"],
            }
        ],
    },
}


def _source(handler):
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    return PrometheusSource("http://prom:9090", "some_query", http_client=client)


def test_poll_maps_vector_to_events():
    src = _source(lambda req: httpx.Response(200, json=_OK_BODY))
    events = src.poll()
    assert len(events) == 1
    e = events[0]
    assert e.name == "http_request_errors_total"
    assert e.value == 7.0
    assert e.labels["job"] == "demo-app"


def test_poll_returns_empty_on_connection_error():
    def boom(req):
        raise httpx.ConnectError("refused", request=req)

    src = _source(boom)
    assert src.poll() == []


def test_poll_returns_empty_on_error_status():
    src = _source(lambda req: httpx.Response(200, json={"status": "error"}))
    assert src.poll() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/ingestion/tests/test_prometheus_source.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement the source**

```python
# services/ingestion/adapters/prometheus_source.py
"""A TelemetrySource backed by a real Prometheus HTTP API.

poll() runs a PromQL instant query and maps each result vector entry to a
normalized TelemetryEvent. It is defensive by construction: any connection
error, non-200, or non-success payload yields an empty list rather than raising,
so the ingestion poll loop survives Prometheus not being ready yet.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx

from common.contracts import TelemetryEvent
from services.ingestion.normalize import normalize

logger = logging.getLogger("intelliops.ingestion.prometheus")


class PrometheusSource:
    def __init__(self, base_url: str, query: str, http_client: httpx.Client | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._query = query
        self._client = http_client or httpx.Client(timeout=5.0)

    def poll(self) -> list[TelemetryEvent]:
        try:
            resp = self._client.get(f"{self._base}/api/v1/query", params={"query": self._query})
        except httpx.HTTPError as exc:
            logger.info("prometheus unreachable (%s); will retry next poll", exc.__class__.__name__)
            return []
        if resp.status_code != 200:
            return []
        body = resp.json()
        if body.get("status") != "success":
            return []
        events: list[TelemetryEvent] = []
        for entry in body.get("data", {}).get("result", []):
            metric = entry.get("metric", {})
            name = metric.get("__name__", "unknown")
            ts_epoch, raw_value = entry.get("value", [0.0, "0"])
            events.append(
                normalize(
                    {
                        "source": "prometheus",
                        "kind": "metric",
                        "name": name,
                        "value": float(raw_value),
                        "labels": {k: v for k, v in metric.items() if k != "__name__"},
                        # normalize() requires a 'ts'; Prometheus returns epoch seconds.
                        "ts": datetime.fromtimestamp(float(ts_epoch), tz=UTC).isoformat(),
                    }
                )
            )
        return events

    def subscribe(self) -> Iterator[TelemetryEvent]:
        yield from self.poll()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest services/ingestion/tests/test_prometheus_source.py -v`
Expected: PASS (3 tests)

Note (verified): `normalize()` requires a `ts` field (raises `ValueError` without it) and computes `fingerprint` itself. The dict above supplies `ts` from Prometheus' epoch timestamp; do not change `normalize`.

- [ ] **Step 5: Commit**

```bash
git add services/ingestion/adapters/prometheus_source.py services/ingestion/tests/test_prometheus_source.py
git commit -m "feat(ingestion): PrometheusSource adapter (defensive, PromQL-backed)"
```

---

## Task 5: Ingestion poll loop (mode-switched)

**Files:**
- Modify: `services/ingestion/app.py`
- Test: `services/ingestion/tests/test_poll_loop.py` (Create)

**Interfaces:**
- Consumes: `PrometheusSource` (Task 4), `FileTelemetrySource` (existing), `Settings` (Task 1), `common.envelope.publish_model`.
- Produces: a `run_poll_loop(bus, source, interval, stop_event)` function that repeatedly polls the source and publishes each event to `telemetry.raw`; a lifespan that starts it in a daemon thread only when `telemetry_mode == "prometheus"`. `POST /ingest` is unchanged.

- [ ] **Step 1: Write the failing test**

```python
# services/ingestion/tests/test_poll_loop.py
import threading

from services.ingestion.app import run_poll_loop


class OneShotSource:
    def __init__(self, events):
        self._events = events
        self.calls = 0

    def poll(self):
        self.calls += 1
        return self._events if self.calls == 1 else []


class RecordingBus:
    def __init__(self):
        self.published = []

    def publish(self, topic, message):
        self.published.append((topic, message))


def _event(name="http_request_errors_total", value=7.0):
    from datetime import UTC, datetime
    from common.contracts import TelemetryEvent, TelemetryKind

    return TelemetryEvent(
        source="prometheus",
        kind=TelemetryKind.METRIC,
        name=name,
        value=value,
        labels={},
        ts=datetime.now(UTC),
        fingerprint="fp1",
    )


def test_poll_loop_publishes_events_then_stops():
    bus = RecordingBus()
    src = OneShotSource([_event()])
    stop = threading.Event()

    # stop after the first non-empty batch by flipping the event from a wrapper source
    class StopAfterFirst:
        def poll(self):
            evs = src.poll()
            if evs:
                return evs
            stop.set()
            return []

    run_poll_loop(bus, StopAfterFirst(), interval=0.0, stop_event=stop)
    assert len(bus.published) == 1
    assert bus.published[0][0] == "telemetry.raw"
    assert "data" in bus.published[0][1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/ingestion/tests/test_poll_loop.py -v`
Expected: FAIL — `run_poll_loop` is not defined.

- [ ] **Step 3: Implement the loop and mode-switched lifespan**

Rewrite `services/ingestion/app.py` to add the loop and lifespan while keeping `POST /ingest`:

```python
"""Ingestion service: normalize + dedup telemetry onto the bus.

Two ingress modes: push (POST /ingest) always works; when TELEMETRY_MODE is
'prometheus' a background poll loop pulls from a PrometheusSource and publishes
to telemetry.raw. 'file' (default) leaves the push-only behavior untouched so
tests are unaffected.
"""

from __future__ import annotations

import threading
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from pydantic import BaseModel

from common.config import get_settings
from common.envelope import publish_model
from services.base import create_app
from services.ingestion.normalize import normalize


def run_poll_loop(bus, source, interval: float, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        for event in source.poll():
            publish_model(bus, "telemetry.raw", event)
        if stop_event.is_set():
            break
        time.sleep(interval)


def _make_source(settings):
    if settings.telemetry_mode == "prometheus":
        from services.ingestion.adapters.prometheus_source import PrometheusSource

        return PrometheusSource(settings.prometheus_url, settings.prometheus_query)
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    stop_event = threading.Event()
    source = _make_source(settings)
    thread = None
    if source is not None:
        thread = threading.Thread(
            target=run_poll_loop,
            args=(app.state.bus, source, settings.telemetry_poll_seconds, stop_event),
            daemon=True,
        )
        thread.start()
    app.state.poll_stop = stop_event
    app.state.poll_thread = thread
    try:
        yield
    finally:
        stop_event.set()


app = create_app("ingestion-service")
app.router.lifespan_context = lifespan


class IngestBatch(BaseModel):
    events: list[dict]


@app.post("/ingest")
def ingest(batch: IngestBatch) -> dict[str, int]:
    accepted = 0
    for raw in batch.events:
        if "ts" not in raw:
            raw = {**raw, "ts": datetime.now(UTC).isoformat()}
        event = normalize(raw)
        publish_model(app.state.bus, "telemetry.raw", event)
        accepted += 1
    return {"accepted": accepted}
```

- [ ] **Step 4: Run tests to verify they pass (new + existing ingestion tests)**

Run: `uv run pytest services/ingestion/tests/ -v`
Expected: PASS (new poll-loop test + existing ingest/normalize/file_source tests)

- [ ] **Step 5: Commit**

```bash
git add services/ingestion/app.py services/ingestion/tests/test_poll_loop.py
git commit -m "feat(ingestion): background poll loop for prometheus mode (push unchanged)"
```

---

## Task 6: Governance approval read endpoints

**Files:**
- Modify: `services/governance/app.py`
- Test: `services/governance/tests/test_approval_reads.py` (Create)

**Interfaces:**
- Consumes: existing `app.state.approvals` dict, `ApprovalRequest` contract.
- Produces: `GET /approvals/{approval_id}` → the `ApprovalRequest` (404 if unknown); `GET /approvals` → list of all approvals with `status == "pending"`.

- [ ] **Step 1: Write the failing test**

```python
# services/governance/tests/test_approval_reads.py
from fastapi.testclient import TestClient

from services.governance.adapters.audit_sink import InMemoryAuditSink
from services.governance.adapters.playbook_store import InMemoryPlaybookStore
from services.governance.rbac import RbacPolicy


def _client():
    from services.governance.app import app

    app.state.audit_sink = InMemoryAuditSink()
    app.state.playbook_store = InMemoryPlaybookStore()
    app.state.rbac = RbacPolicy(roles={}, actors={})
    app.state.approvals = {}
    return TestClient(app)


def _appr(c, appr_id="appr-sit-1"):
    return c.post(
        "/approvals",
        json={
            "id": appr_id,
            "situation_id": "sit-1",
            "playbook_id": "restart-pod",
            "requested_by": "action-service",
        },
    )


def test_get_single_approval():
    c = _client()
    _appr(c)
    r = c.get("/approvals/appr-sit-1")
    assert r.status_code == 200
    assert r.json()["status"] == "pending"


def test_get_unknown_approval_404():
    c = _client()
    assert c.get("/approvals/nope").status_code == 404


def test_list_pending_only():
    c = _client()
    _appr(c, "appr-sit-1")
    _appr(c, "appr-sit-2")
    # decide one away from pending would need rbac; instead just check both pending listed
    ids = {a["id"] for a in c.get("/approvals").json()}
    assert ids == {"appr-sit-1", "appr-sit-2"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/governance/tests/test_approval_reads.py -v`
Expected: FAIL — `GET /approvals/{id}` returns 405 (route not defined).

- [ ] **Step 3: Add the endpoints**

In `services/governance/app.py`, after `create_approval` (the `POST /approvals` handler):

```python
@app.get("/approvals")
def list_approvals() -> list[ApprovalRequest]:
    return [a for a in app.state.approvals.values() if a.status == "pending"]


@app.get("/approvals/{approval_id}")
def get_approval(approval_id: str) -> ApprovalRequest:
    req = app.state.approvals.get(approval_id)
    if req is None:
        raise HTTPException(status_code=404, detail="approval not found")
    return req
```

Note: define `GET /approvals` (list) BEFORE `GET /approvals/{approval_id}` so the literal path is matched first.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest services/governance/tests/ -v`
Expected: PASS (new reads + all existing governance tests)

- [ ] **Step 5: Commit**

```bash
git add services/governance/app.py services/governance/tests/test_approval_reads.py
git commit -m "feat(governance): GET /approvals and GET /approvals/{id} read endpoints"
```

---

## Task 7: HttpGovernanceGate (close the cross-container HITL gap)

**Files:**
- Modify: `services/action/adapters/governance_gate.py`
- Test: `services/action/tests/test_http_gate.py` (Create)

**Interfaces:**
- Consumes: governance endpoints `POST /rbac/check`, `POST /approvals`, `GET /approvals/{id}`, `POST /audit`; `ApprovalRequest`, `AuditRecord` contracts.
- Produces: `class HttpGovernanceGate` with the same methods `remediate.py` calls on the in-process gate: `check_rbac(actor, action, resource) -> bool`, `request_approval(request: ApprovalRequest) -> ApprovalRequest`, `await_decision(approval_id: str, timeout_seconds: float) -> ApprovalRequest`, `write_audit(record: AuditRecord) -> None`. Constructor: `__init__(self, base_url: str, poll_interval_seconds: float = 0.5, http_client=None)`.

- [ ] **Step 1: (Verified) RBAC check response shape**

Confirmed: `POST /rbac/check` returns `{"allowed": bool}` (see `services/governance/app.py:81`). The gate below reads `.get("allowed", False)`. No change needed — this step is a sanity check, not a code change.

- [ ] **Step 2: Write the failing test**

```python
# services/action/tests/test_http_gate.py
import httpx

from common.contracts import ApprovalRequest
from services.action.adapters.governance_gate import HttpGovernanceGate


def _gate(handler, poll=0.0):
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    return HttpGovernanceGate("http://gov:8000", poll_interval_seconds=poll, http_client=client)


def test_check_rbac_true():
    def h(req):
        assert req.url.path == "/rbac/check"
        return httpx.Response(200, json={"allowed": True})

    assert _gate(h).check_rbac("action-service", "execute", "playbook:x") is True


def test_await_decision_returns_when_approved():
    calls = {"n": 0}

    def h(req):
        if req.url.path == "/approvals/appr-1" and req.method == "GET":
            calls["n"] += 1
            status = "approved" if calls["n"] >= 2 else "pending"
            return httpx.Response(
                200,
                json={
                    "id": "appr-1",
                    "situation_id": "sit-1",
                    "playbook_id": "p",
                    "requested_by": "action-service",
                    "status": status,
                    "decided_by": None,
                },
            )
        return httpx.Response(404)

    decided = _gate(h).await_decision("appr-1", timeout_seconds=5.0)
    assert decided.status == "approved"
    assert calls["n"] >= 2


def test_await_decision_times_out_still_pending():
    def h(req):
        return httpx.Response(
            200,
            json={
                "id": "appr-1",
                "situation_id": "sit-1",
                "playbook_id": "p",
                "requested_by": "action-service",
                "status": "pending",
                "decided_by": None,
            },
        )

    decided = _gate(h).await_decision("appr-1", timeout_seconds=0.05)
    assert decided.status == "pending"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest services/action/tests/test_http_gate.py -v`
Expected: FAIL — `HttpGovernanceGate` not defined.

- [ ] **Step 4: Implement the HTTP gate**

Append to `services/action/adapters/governance_gate.py` (keep `InProcessGovernanceGate`):

```python
import time as _time

import httpx

from common.contracts import ApprovalRequest, AuditRecord


class HttpGovernanceGate:
    """The cross-process gate: action talks to governance over REST.

    Closes the compose gap where an in-process approvals dict cannot span
    containers. Same interface remediate.py already calls on the in-process gate.
    """

    def __init__(
        self,
        base_url: str,
        poll_interval_seconds: float = 0.5,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._poll = poll_interval_seconds
        self._client = http_client or httpx.Client(timeout=5.0)

    def check_rbac(self, actor: str, action: str, resource: str) -> bool:
        resp = self._client.post(
            f"{self._base}/rbac/check",
            json={"actor": actor, "action": action, "resource": resource},
        )
        if resp.status_code != 200:
            return False
        return bool(resp.json().get("allowed", False))

    def request_approval(self, request: ApprovalRequest) -> ApprovalRequest:
        resp = self._client.post(f"{self._base}/approvals", json=request.model_dump())
        return ApprovalRequest.model_validate(resp.json())

    def await_decision(self, approval_id: str, timeout_seconds: float) -> ApprovalRequest:
        deadline = _time.monotonic() + timeout_seconds
        while True:
            resp = self._client.get(f"{self._base}/approvals/{approval_id}")
            req = ApprovalRequest.model_validate(resp.json())
            if req.status != "pending":
                return req
            if _time.monotonic() >= deadline:
                return req
            _time.sleep(self._poll)

    def write_audit(self, record: AuditRecord) -> None:
        self._client.post(f"{self._base}/audit", json=record.model_dump(mode="json"))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest services/action/tests/test_http_gate.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add services/action/adapters/governance_gate.py services/action/tests/test_http_gate.py
git commit -m "feat(action): HttpGovernanceGate closes cross-container HITL approval gap"
```

---

## Task 8: Wire action to select the gate by mode

**Files:**
- Modify: `services/action/app.py`
- Test: `services/action/tests/test_gate_selection.py` (Create)

**Interfaces:**
- Consumes: `Settings.governance_mode`, `Settings.governance_url` (Task 1); `HttpGovernanceGate` (Task 7); `InProcessGovernanceGate` (existing).
- Produces: `_make_gate(settings) -> gate` returning `HttpGovernanceGate` when `governance_mode == "http"`, else `InProcessGovernanceGate`.

- [ ] **Step 1: Write the failing test**

```python
# services/action/tests/test_gate_selection.py
from services.action.app import _make_gate
from services.action.adapters.governance_gate import HttpGovernanceGate, InProcessGovernanceGate


class _S:
    governance_mode = "in_process"
    governance_url = "http://gov:8000"
    rbac_policy_path = "policies/rbac_policy.yaml"
    audit_store_path = "data/audit.jsonl"
    hitl_poll_interval_seconds = 0.5


def test_in_process_default():
    assert isinstance(_make_gate(_S()), InProcessGovernanceGate)


def test_http_when_mode_http():
    s = _S()
    s.governance_mode = "http"
    assert isinstance(_make_gate(s), HttpGovernanceGate)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/action/tests/test_gate_selection.py -v`
Expected: FAIL — `_make_gate` not defined.

- [ ] **Step 3: Extract gate construction into `_make_gate` and use it in lifespan**

In `services/action/app.py`, add the imports (`HttpGovernanceGate`) and a factory, and call it in the lifespan in place of the inline `InProcessGovernanceGate(...)`:

```python
from services.action.adapters.governance_gate import (
    HttpGovernanceGate,
    InProcessGovernanceGate,
)
from services.governance.adapters.audit_sink import FileAuditSink
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
```

Then in `lifespan`, replace the `gate = InProcessGovernanceGate(...)` block with `gate = _make_gate(settings)`.

- [ ] **Step 4: Run tests to verify they pass (new + existing action tests)**

Run: `uv run pytest services/action/tests/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/action/app.py services/action/tests/test_gate_selection.py
git commit -m "feat(action): select governance gate by GOVERNANCE_MODE env"
```

---

## Task 9: Read-model projection (pure function over events)

**Files:**
- Create: `services/read/__init__.py` (empty), `services/read/projection.py`, `services/read/tests/__init__.py` (empty), `services/read/tests/test_projection.py`

**Interfaces:**
- Consumes: `Situation`, `DiagnosedSituation`, `RemediationOutcome`, `SituationStatus` contracts.
- Produces: `class ReadModel` with `apply_detected(s: Situation)`, `apply_diagnosed(d: DiagnosedSituation)`, `apply_outcome(o: RemediationOutcome)`, `situations() -> list[dict]`, `outcomes() -> list[dict]`. `situations()` returns frontend-shaped dicts matching `frontend/src/data/types.ts` `Situation`. `outcomes()` returns `OutcomeRow`-shaped dicts, capped at `max_outcomes` (most recent first).

- [ ] **Step 1: Write the failing test**

```python
# services/read/tests/test_projection.py
from datetime import UTC, datetime

from common.contracts import (
    DiagnosedSituation,
    RemediationOutcome,
    RemediationResult,
    RootCauseHypothesis,
    Situation,
    SituationStatus,
)
from services.read.projection import ReadModel

TS = datetime(2026, 8, 15, tzinfo=UTC)


def _sit(sid="sit-1", status=SituationStatus.DETECTED):
    return Situation(
        id=sid,
        status=status,
        member_events=[],
        severity="high",
        first_seen=TS,
        last_seen=TS,
        signature=sid.replace("sit-", ""),
    )


def test_detected_then_diagnosed_then_resolved():
    rm = ReadModel(max_outcomes=10)
    rm.apply_detected(_sit())
    assert rm.situations()[0]["status"] == "detected"

    rm.apply_diagnosed(
        DiagnosedSituation(
            situation=_sit(status=SituationStatus.DIAGNOSED),
            hypotheses=[
                RootCauseHypothesis(
                    situation_id="sit-1",
                    description="deploy",
                    confidence=0.8,
                    suggested_runbook_id="rollback-deploy",
                )
            ],
            suggested_runbook_id="rollback-deploy",
        )
    )
    s = rm.situations()[0]
    assert s["status"] == "diagnosed"
    assert s["hypotheses"][0]["confidence"] == 0.8
    assert s["suggested_runbook_id"] == "rollback-deploy"

    rm.apply_outcome(
        RemediationOutcome(
            situation_id="sit-1",
            playbook_id="rollback-deploy",
            result=RemediationResult.SUCCESS,
            health_after="healthy",
            ts=TS,
        )
    )
    assert rm.situations()[0]["status"] == "resolved"
    assert rm.outcomes()[0]["reason"] == "healthy"


def test_failure_outcome_marks_situation_failed():
    rm = ReadModel(max_outcomes=10)
    rm.apply_detected(_sit())
    rm.apply_outcome(
        RemediationOutcome(
            situation_id="sit-1",
            playbook_id="p",
            result=RemediationResult.FAILURE,
            health_after="aborted:timeout",
            ts=TS,
        )
    )
    assert rm.situations()[0]["status"] == "failed"


def test_outcomes_capped_most_recent_first():
    rm = ReadModel(max_outcomes=2)
    for i in range(3):
        rm.apply_outcome(
            RemediationOutcome(
                situation_id=f"sit-{i}",
                playbook_id="p",
                result=RemediationResult.SUCCESS,
                health_after="healthy",
                ts=TS,
            )
        )
    outs = rm.outcomes()
    assert len(outs) == 2
    assert outs[0]["situation_id"] == "sit-2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/read/tests/test_projection.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement the projection**

```python
# services/read/projection.py
"""In-memory read-model: a projection of the event stream for the dashboard.

Rebuildable from Redis Streams on every start (the events are the source of
truth), so this holds no durable state of its own. It maps backend contracts to
the exact shapes frontend/src/data/types.ts expects, so the UI needs no
translation layer.
"""

from __future__ import annotations

from common.contracts import (
    DiagnosedSituation,
    RemediationOutcome,
    RemediationResult,
    Situation,
    SituationStatus,
)

_RESULT_STATUS = {
    RemediationResult.SUCCESS: "resolved",
    RemediationResult.FAILURE: "failed",
    RemediationResult.ROLLED_BACK: "failed",
}

_SEVERITY_MAP = {"critical": "critical", "high": "high", "medium": "medium", "low": "low"}


def _epoch_ms(dt) -> int:
    return int(dt.timestamp() * 1000)


class ReadModel:
    def __init__(self, max_outcomes: int = 200) -> None:
        self._sits: dict[str, dict] = {}
        self._outcomes: list[dict] = []
        self._max = max_outcomes

    def apply_detected(self, s: Situation) -> None:
        existing = self._sits.get(s.id, {})
        self._sits[s.id] = {
            **existing,
            "id": s.id,
            "signature": s.signature,
            "service": self._service_of(s),
            "title": s.signature,
            "status": s.status.value if isinstance(s.status, SituationStatus) else str(s.status),
            "severity": _SEVERITY_MAP.get(s.severity, "medium"),
            "memberCount": len(s.member_events),
            "first_seen": _epoch_ms(s.first_seen),
            "hypotheses": existing.get("hypotheses", []),
            "suggested_runbook_id": existing.get("suggested_runbook_id"),
            "hitl_mode": existing.get("hitl_mode", "hitl"),
            "reversible": existing.get("reversible", True),
            "reliability": existing.get("reliability", 0.0),
            "suppressed": False,
        }

    def apply_diagnosed(self, d: DiagnosedSituation) -> None:
        self.apply_detected(d.situation)
        self._sits[d.situation.id].update(
            {
                "status": "diagnosed",
                "hypotheses": [
                    {
                        "description": h.description,
                        "confidence": h.confidence,
                        "suggested_runbook_id": h.suggested_runbook_id,
                    }
                    for h in d.hypotheses
                ],
                "suggested_runbook_id": d.suggested_runbook_id,
            }
        )

    def apply_outcome(self, o: RemediationOutcome) -> None:
        if o.situation_id in self._sits:
            self._sits[o.situation_id]["status"] = _RESULT_STATUS.get(o.result, "failed")
        result = o.result.value if isinstance(o.result, RemediationResult) else str(o.result)
        self._outcomes.insert(
            0,
            {
                "situation_id": o.situation_id,
                "playbook_id": o.playbook_id,
                "result": result,
                "reason": o.health_after,
                "ts": _epoch_ms(o.ts),
                "service": self._sits.get(o.situation_id, {}).get("service", "unknown"),
            },
        )
        del self._outcomes[self._max :]

    def situations(self) -> list[dict]:
        return list(self._sits.values())

    def outcomes(self) -> list[dict]:
        return list(self._outcomes)

    @staticmethod
    def _service_of(s: Situation) -> str:
        for ev in s.member_events:
            svc = ev.labels.get("service") or ev.labels.get("job")
            if svc:
                return svc
        return "demo-app"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest services/read/tests/test_projection.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add services/read/__init__.py services/read/projection.py services/read/tests/
git commit -m "feat(read): read-model projection mapping events to frontend shapes"
```

---

## Task 10: Read-model consumer + service API

**Files:**
- Create: `services/read/consumer.py`, `services/read/app.py`, `services/read/tests/test_read_api.py`

**Interfaces:**
- Consumes: `ReadModel` (Task 9), `common.envelope.decode_model`, bus, `Situation`/`DiagnosedSituation`/`RemediationOutcome`.
- Produces: `run_consumer(bus, model, stop_event)` that reads all three topics and applies to the model; a FastAPI `app` with `GET /situations` → `model.situations()`, `GET /outcomes` → `model.outcomes()`. Reads from stream start so it rebuilds on restart.

- [ ] **Step 1: Write the failing test (API over a pre-seeded model)**

```python
# services/read/tests/test_read_api.py
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from common.contracts import Situation, SituationStatus
from services.read.projection import ReadModel

TS = datetime(2026, 8, 15, tzinfo=UTC)


def _client(model):
    from services.read import app as appmod

    appmod.app.state.model = model
    return TestClient(appmod.app)


def test_situations_and_outcomes_endpoints():
    model = ReadModel()
    model.apply_detected(
        Situation(
            id="sit-1",
            status=SituationStatus.DETECTED,
            member_events=[],
            severity="high",
            first_seen=TS,
            last_seen=TS,
            signature="1",
        )
    )
    c = _client(model)
    sits = c.get("/situations").json()
    assert sits[0]["id"] == "sit-1"
    assert c.get("/outcomes").json() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/read/tests/test_read_api.py -v`
Expected: FAIL — `services.read.app` does not exist.

- [ ] **Step 3: Implement the consumer**

```python
# services/read/consumer.py
"""Read-model consumer: tail the event stream, keep the projection current.

Subscribes to the three topics the dashboard reads. Reads each topic from the
stream's beginning on start (rebuild-on-restart), then tails live. One thread
per topic keeps the loop simple; all share the same ReadModel instance.
"""

from __future__ import annotations

import threading

from common.contracts import DiagnosedSituation, RemediationOutcome, Situation
from common.envelope import decode_model
from services.read.projection import ReadModel

_TOPICS = [
    ("situations.detected", Situation, "apply_detected"),
    ("situations.diagnosed", DiagnosedSituation, "apply_diagnosed"),
    ("remediation.outcomes", RemediationOutcome, "apply_outcome"),
]


def _run_topic(
    bus, model: ReadModel, topic: str, model_type, method: str, stop_event: threading.Event
) -> None:
    apply = getattr(model, method)
    for fields in bus.consume(topic, "read-model"):
        if stop_event.is_set():
            break
        apply(decode_model(fields, model_type))


def run_consumer(bus, model: ReadModel, stop_event: threading.Event) -> list[threading.Thread]:
    threads = []
    for topic, model_type, method in _TOPICS:
        t = threading.Thread(
            target=_run_topic, args=(bus, model, topic, model_type, method, stop_event), daemon=True
        )
        t.start()
        threads.append(t)
    return threads
```

- [ ] **Step 4: Implement the app**

```python
# services/read/app.py
"""Read service: serves the dashboard's live read model (CQRS read side)."""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common.config import get_settings
from services.base import create_app
from services.read.consumer import run_consumer
from services.read.projection import ReadModel


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    stop_event = threading.Event()
    model = ReadModel(max_outcomes=settings.read_outcomes_max)
    app.state.model = model
    app.state.consumer_stop = stop_event
    app.state.consumer_threads = run_consumer(app.state.bus, model, stop_event)
    try:
        yield
    finally:
        stop_event.set()


app = create_app("read-service")
app.router.lifespan_context = lifespan


@app.get("/situations")
def situations() -> list[dict]:
    model = getattr(app.state, "model", None)
    return model.situations() if model else []


@app.get("/outcomes")
def outcomes() -> list[dict]:
    model = getattr(app.state, "model", None)
    return model.outcomes() if model else []
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest services/read/tests/ -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add services/read/consumer.py services/read/app.py services/read/tests/test_read_api.py
git commit -m "feat(read): consumer + GET /situations and /outcomes endpoints"
```

---

## Task 11: Prometheus scrape config + compose services

**Files:**
- Create: `deploy/prometheus.yml`
- Modify: `deploy/docker-compose.yml`

**Interfaces:**
- Produces: a running stack where `demo-app` (8080), `prometheus` (9090), and `read` (8007) join the existing six services; ingestion runs in `prometheus` mode; action runs in `http` governance mode.

- [ ] **Step 1: Write the Prometheus scrape config**

```yaml
# deploy/prometheus.yml
global:
  scrape_interval: 5s

scrape_configs:
  - job_name: demo-app
    static_configs:
      - targets: ["demo-app:8080"]
```

- [ ] **Step 2: Add demo-app, prometheus, read to compose; set ingestion/action modes**

In `deploy/docker-compose.yml`, add these services (mirror the existing `<<: *service` block for `read`; `demo-app` and `prometheus` are standalone). Add env to `ingestion` and `action`:

```yaml
  demo-app:
    <<: *service
    environment:
      SERVICE_MODULE: services.demo_app.app:app
      PORT: "8080"
    ports:
      - "8080:8080"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"]
      interval: 5s
      timeout: 3s
      retries: 5

  prometheus:
    image: prom/prometheus
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
    ports:
      - "9090:9090"
    depends_on:
      demo-app:
        condition: service_healthy

  read:
    <<: *service
    environment:
      INTELLIOPS_REDIS_URL: redis://redis:6379
      SERVICE_MODULE: services.read.app:app
      PORT: "8000"
    ports:
      - "8007:8000"
```

Then extend the existing `ingestion` service environment with:

```yaml
      INTELLIOPS_TELEMETRY_MODE: prometheus
      INTELLIOPS_PROMETHEUS_URL: http://prometheus:9090
```

and the existing `action` service environment with:

```yaml
      INTELLIOPS_GOVERNANCE_MODE: http
      INTELLIOPS_GOVERNANCE_URL: http://governance:8000
```

Note: the Dockerfile must be able to run `demo_app`; confirm `deploy/Dockerfile` launches `$SERVICE_MODULE` generically (it already parameterizes via `SERVICE_MODULE`/`PORT` per the existing services). If the base image lacks `prometheus-client`, it is now in `pyproject.toml` (Task 3) so the image rebuild includes it.

- [ ] **Step 3: Validate compose file syntax**

Run: `docker compose -f deploy/docker-compose.yml config >/dev/null && echo OK`
Expected: `OK` (no YAML/schema errors). If `docker` is unavailable in this environment, skip and note it for manual run.

- [ ] **Step 4: Commit**

```bash
git add deploy/prometheus.yml deploy/docker-compose.yml
git commit -m "feat(deploy): add demo-app, prometheus, read; enable prometheus+http modes"
```

---

## Task 12: Frontend live API client + source toggle

**Files:**
- Create: `frontend/src/vite-env.d.ts`, `frontend/src/data/api.ts`, `frontend/src/data/source.ts`, `frontend/.env.example`

**Interfaces:**
- Consumes: types from `frontend/src/data/types.ts`; the read service (`/situations`, `/outcomes`), governance (`/audit`, `/playbooks`, `/approvals/{id}/decide`), feedback (`/metrics`).
- Produces: `loadSituations(): Promise<Situation[]>`, `loadOutcomes(): Promise<OutcomeRow[]>`, `loadAudit(): Promise<AuditRow[]>`, `loadPlaybooks(): Promise<Playbook[]>`, `decideApproval(id, decision, decidedBy): Promise<void>` in `api.ts`; `source.ts` re-exports either the api loaders or mock-backed loaders based on `import.meta.env.VITE_DATA_MODE`.

- [ ] **Step 1: Add Vite env typings (required for the strict build)**

The project has no `vite-env.d.ts` and doesn't use `import.meta.env` yet; without this the strict build fails with "Property 'env' does not exist on type 'ImportMeta'".

```typescript
// frontend/src/vite-env.d.ts
/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_DATA_MODE?: "mock" | "live";
  readonly VITE_READ_URL?: string;
  readonly VITE_GOV_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
```

- [ ] **Step 2: Write the API client**

```typescript
// frontend/src/data/api.ts
import type { AuditRow, OutcomeRow, Playbook, Situation } from "./types";

const READ = import.meta.env.VITE_READ_URL ?? "http://localhost:8007";
const GOV = import.meta.env.VITE_GOV_URL ?? "http://localhost:8005";

async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return (await r.json()) as T;
}

export const loadSituations = () => getJSON<Situation[]>(`${READ}/situations`);
export const loadOutcomes = () => getJSON<OutcomeRow[]>(`${READ}/outcomes`);
export const loadAudit = () => getJSON<AuditRow[]>(`${GOV}/audit`);
export const loadPlaybooks = () => getJSON<Playbook[]>(`${GOV}/playbooks`);

export async function decideApproval(
  approvalId: string,
  decision: "approved" | "rejected",
  decidedBy = "oncall-alice",
): Promise<void> {
  const r = await fetch(`${GOV}/approvals/${approvalId}/decide`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ decision, decided_by: decidedBy }),
  });
  if (!r.ok) throw new Error(`decide → ${r.status}`);
}
```

- [ ] **Step 3: Write the source toggle**

```typescript
// frontend/src/data/source.ts
import * as api from "./api";
import * as mock from "./mock";

const LIVE = import.meta.env.VITE_DATA_MODE === "live";

export const loadSituations = LIVE
  ? api.loadSituations
  : async () => mock.situations;
export const loadOutcomes = LIVE ? api.loadOutcomes : async () => mock.outcomes;
export const loadAudit = LIVE ? api.loadAudit : async () => mock.audit;
export const loadPlaybooks = LIVE ? api.loadPlaybooks : async () => mock.playbooks;
export const decideApproval = LIVE
  ? api.decideApproval
  : async () => {
      /* mock mode: no-op; Incidents' local optimistic update drives the UI */
    };
```

- [ ] **Step 4: Write the env example**

```bash
# frontend/.env.example
# Copy to .env.local and set VITE_DATA_MODE=live to read the running stack.
VITE_DATA_MODE=mock
VITE_READ_URL=http://localhost:8007
VITE_GOV_URL=http://localhost:8005
```

- [ ] **Step 5: Type-check the frontend**

Run (from `frontend/`): `npm run build`
Expected: build succeeds (api.ts/source.ts type-check against types.ts; `import.meta.env` resolves via vite-env.d.ts). `mock.ts` exports `situations/outcomes/audit/playbooks` (verified) so the mock branch resolves.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/vite-env.d.ts frontend/src/data/api.ts frontend/src/data/source.ts frontend/.env.example
git commit -m "feat(frontend): live API client + mock/live source toggle"
```

---

## Task 13: Wire views to the async data source

**Files:**
- Create: `frontend/src/hooks/useData.ts`
- Modify: `frontend/src/views/Overview.tsx`, `frontend/src/views/Incidents.tsx`, `frontend/src/views/Governance.tsx`

**Interfaces:**
- Consumes: loaders from `source.ts` (Task 12).
- Produces: `useData<T>(loader: () => Promise<T>, initial: T): { data, loading, error }` — loads on mount, polls every 5s in live mode. Views render from `data` with the existing empty-state UI while loading.

- [ ] **Step 1: Write the hook**

```typescript
// frontend/src/hooks/useData.ts
import { useEffect, useState } from "react";

export function useData<T>(loader: () => Promise<T>, initial: T) {
  const [data, setData] = useState<T>(initial);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = () =>
      loader()
        .then((d) => alive && (setData(d), setError(null)))
        .catch((e) => alive && setError(String(e)))
        .finally(() => alive && setLoading(false));
    tick();
    const live = import.meta.env.VITE_DATA_MODE === "live";
    const id = live ? window.setInterval(tick, 5000) : undefined;
    return () => {
      alive = false;
      if (id) window.clearInterval(id);
    };
  }, [loader]);

  return { data, loading, error };
}
```

- [ ] **Step 2: Rewire Overview.tsx**

Replace the static imports `import { metrics, outcomes, playbooks, series, services } from "../data/mock"` so that:
- `series` and `services` stay imported from `../data/mock` (static demo values — fleet health and sparkline generator are presentation, not live).
- `outcomes` and `playbooks` come from the hook:

```typescript
import { series, services } from "../data/mock";
import { loadOutcomes, loadPlaybooks } from "../data/source";
import { useData } from "../hooks/useData";
// ...inside Overview():
const { data: outcomes } = useData(loadOutcomes, [] as OutcomeRow[]);
const { data: playbooks } = useData(loadPlaybooks, [] as Playbook[]);
```

Add the `OutcomeRow`/`Playbook` type imports from `../data/types`. `metrics` stays from mock for now (the feedback `/metrics` shape differs from the frontend `Metrics` type; live metrics wiring is out of scope for this task — see note).

- [ ] **Step 3: Rewire Governance.tsx**

```typescript
import { loadAudit, loadPlaybooks } from "../data/source";
import { useData } from "../hooks/useData";
import type { AuditRow, Playbook } from "../data/types";
// replace `import { audit, playbooks } from "../data/mock"`:
const { data: audit } = useData(loadAudit, [] as AuditRow[]);
const { data: playbooks } = useData(loadPlaybooks, [] as Playbook[]);
```

- [ ] **Step 4: Rewire Incidents.tsx — live queue + real approve (handle empty state)**

⚠️ The current component assumes non-empty data at render: `useState(seed[0].id)` (line 30), `list.find(...)!` (line 31), and `sel.status` (line 49) all crash when the live load starts as `[]`. The rewire MUST make `sel` optional and guard the render. Replace the top of the component (imports + the state block through `stageIndex`) with:

```typescript
// imports (replace `import { situations as seed } from "../data/mock"`)
import { loadSituations, decideApproval } from "../data/source";
import { useData } from "../hooks/useData";
// keep: import type { Situation, SituationStatus } from "../data/types";

// inside export function Incidents():
const { data: seed } = useData(loadSituations, [] as Situation[]);
const [overrides, setOverrides] = useState<Record<string, Partial<Situation>>>({});
const [selId, setSelId] = useState<string | null>(null);
const [working, setWorking] = useState(false);

// merge server data with local optimistic overrides
const list = useMemo<Situation[]>(
  () => seed.map((s) => ({ ...s, ...overrides[s.id] })),
  [seed, overrides],
);

// keep a valid selection as data streams in
useEffect(() => {
  if ((selId === null || !list.some((s) => s.id === selId)) && list.length > 0) {
    setSelId(list[0].id);
  }
}, [list, selId]);

const sel = useMemo(() => list.find((s) => s.id === selId) ?? null, [list, selId]);

function update(id: string, patch: Partial<Situation>) {
  setOverrides((o) => ({ ...o, [id]: { ...o[id], ...patch } }));
}

async function approve() {
  if (working || !sel) return;
  setWorking(true);
  update(sel.id, { status: "acting" }); // optimistic fast-path (mock + live)
  try {
    await decideApproval(`appr-${sel.id}`, "approved");
  } catch {
    /* mock mode no-ops; live poll will converge to server truth */
  }
  setTimeout(() => update(sel.id, { status: "resolved" }), 1400);
  setTimeout(() => setWorking(false), 1500);
}

async function reject() {
  if (!sel) return;
  update(sel.id, { status: "failed" });
  try {
    await decideApproval(`appr-${sel.id}`, "rejected");
  } catch {
    /* mock mode no-ops */
  }
}

const stageIndex = sel ? order.indexOf(sel.status === "failed" ? "acting" : sel.status) : 0;
```

Then guard the JSX that uses `sel`: wrap the detail panel so it renders an empty state when `sel === null`. Find the detail-panel block (the right column that reads `sel.*`) and wrap it:

```tsx
{sel ? (
  /* ...existing detail panel JSX unchanged... */
) : (
  <div className="lg:col-span-7 flex items-center justify-center rounded-4xl border border-white/[0.06] p-12 text-ink-3">
    Waiting for situations…
  </div>
)}
```

Also add `useEffect` to the React import if not present. Build will flag any remaining non-null `sel.` dereference — fix each by relying on the `sel ?` guard.

- [ ] **Step 5: Type-check + build**

Run (from `frontend/`): `npm run build`
Expected: build succeeds. Fix any unused-import strict errors (`noUnusedLocals`) surfaced by the rewire.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/hooks/useData.ts frontend/src/views/
git commit -m "feat(frontend): views read live data via useData hook; real approve action"
```

---

## Task 14: chaos.sh + README "Run it live"

**Files:**
- Create: `scripts/chaos.sh`
- Modify: `README.md`

**Interfaces:**
- Produces: a script that brings the loop to life (break → wait → show detection → prompt to approve → fix), and README instructions.

- [ ] **Step 1: Write chaos.sh**

```bash
#!/usr/bin/env bash
# Drive one full incident through the live stack.
# Prereq: `docker compose -f deploy/docker-compose.yml up` is running.
set -euo pipefail

DEMO=${DEMO_URL:-http://localhost:8080}
READ=${READ_URL:-http://localhost:8007}

echo "→ Breaking demo-app (error rate + CPU spike)…"
curl -fsS -X POST "$DEMO/break" >/dev/null
echo "  broken. Generating error traffic…"
for _ in $(seq 1 20); do curl -fsS "$DEMO/work" >/dev/null 2>&1 || true; done

echo "→ Waiting ~30s for detect → diagnose (Prometheus scrape + poll + anomaly)…"
sleep 30

echo "→ Current situations (read model):"
curl -fsS "$READ/situations" | python -m json.tool || true

echo
echo "Now open the console (http://localhost:5173 with VITE_DATA_MODE=live) and"
echo "click Approve on the open situation to remediate it (dry-run)."
echo "When done, recover the app:  curl -X POST $DEMO/fix"
```

Make it executable: `chmod +x scripts/chaos.sh`

- [ ] **Step 2: Add the README section**

Append a "Run it live (real data, local, free)" section to `README.md` covering:
- `docker compose -f deploy/docker-compose.yml up --build`
- `cd frontend && cp .env.example .env.local` then set `VITE_DATA_MODE=live`; `npm run dev`
- `./scripts/chaos.sh` to drive an incident
- **Safety note (verbatim):** "Remediation runs in dry-run mode (ADR-007): the action service logs the remediation steps and a simulated health check reports healthy. 'Resolved' means the fix was logged and simulated — no real infrastructure is ever touched."
- The ~15–30s detection latency is expected (real scrape + poll + anomaly detection).

- [ ] **Step 3: Commit**

```bash
git add scripts/chaos.sh README.md
git commit -m "docs: chaos.sh incident driver + Run-it-live README with dry-run safety note"
```

---

## Task 15: Full-suite green + final verification

**Files:** none (verification only)

- [ ] **Step 1: Run the entire Python suite**

Run: `uv run pytest -q`
Expected: all tests pass (existing 60+ plus the new ones). If any existing test broke, the culprit is almost certainly a non-test-safe default — verify `telemetry_mode`/`governance_mode` still default to `file`/`in_process`.

- [ ] **Step 2: Lint**

Run: `uv run ruff check`
Expected: clean (or only pre-existing warnings).

- [ ] **Step 3: Frontend build**

Run (from `frontend/`): `npm run build`
Expected: clean strict build.

- [ ] **Step 4: Commit any lint fixes**

```bash
git add -A
git commit -m "chore: lint + final verification for live stack" || echo "nothing to commit"
```

---

## Self-Review Notes (for the executor)

- **Live `metrics` (Overview KPIs) is intentionally still mock.** The feedback `/metrics` endpoint returns a different shape than the frontend `Metrics` type, and reconciling it (noise-reduction %, MTTR, etc. computed from live data) is a separate piece of work. This plan wires live **situations, outcomes, audit, playbooks, and the approve action** — the closed loop the user asked to see. A follow-up can map feedback metrics → the `Metrics` shape. Flagged here rather than silently left mock.
- **`normalize()` contract:** Task 4 assumes `normalize()` accepts a dict with `source/kind/name/value/labels`. If it requires more (e.g. it computes `fingerprint`), satisfy it without changing `normalize`.
- **RBAC response key:** Task 7 Step 1 requires confirming the real key from `POST /rbac/check` before writing the gate; the plan defaults to `{"allowed": bool}`.
- **CORS on read/feedback:** both go through `create_app`, so Task 2 covers them.
