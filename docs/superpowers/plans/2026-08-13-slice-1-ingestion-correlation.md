# Slice 1 — Ingestion → Correlation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Slice-0 ingestion and correlation stubs into a working vertical slice — raw telemetry enters ingestion (HTTP push or file source), is normalized and published to the bus, and correlation detects anomalies, clusters related ones in a time window, and emits exactly one `Situation` on `situations.detected`.

**Architecture:** A JSON-envelope helper in `common/` carries nested Pydantic models over the flat Redis-Streams bus. `ingestion-service` gains a `POST /ingest` endpoint and a `FileTelemetrySource`, both normalizing raw signals into `TelemetryEvent`s on `telemetry.raw`. `correlation-service` runs a background daemon thread (started via FastAPI lifespan) that consumes `telemetry.raw`, scores each event with a `RiverCorrelator` (per-metric online z-score baseline built on `river.stats`), buffers anomalies in a rolling time window, and emits one `Situation` per window via `correlate()`.

**Tech Stack:** Python 3.11 · FastAPI · Pydantic v2 · Redis Streams (redis-py) / fakeredis · river 0.25 (`river.stats.Mean`/`Var`) · pytest

**Spec:** [docs/superpowers/specs/2026-08-13-intelliops-coe-design.md](../specs/2026-08-13-intelliops-coe-design.md)

## Global Constraints

- **Python floor:** 3.11. **Package manager:** `uv` only (`uv add`, `uv run`). Never bare `pip`.
- **Pydantic v2** API only. **Lint gate:** `uv run ruff check .` must pass (0 errors). **Test:** `uv run pytest` from repo root.
- **Contracts are frozen:** `TelemetryEvent` and `Situation` in `common/contracts.py` are NOT modified. `TelemetryEvent(source, kind: TelemetryKind, name, value: float|None, payload: dict|None, labels: dict[str,str], ts: datetime, fingerprint)`. `Situation(id, status: SituationStatus, member_events: list[TelemetryEvent], severity: str, first_seen: datetime, last_seen: datetime, signature)`.
- **Bus carries flat string dicts.** All model transport goes through the `common/envelope.py` helpers — services never hand-roll serialization. Envelope shape: `{"data": model.model_dump_json()}`.
- **`Correlator` protocol** (`common/interfaces.py`, frozen): `detect(event: TelemetryEvent) -> float`, `correlate(events: list[TelemetryEvent]) -> Situation`, `retrain(training_data: list[dict]) -> None`.
- **Bus topics:** ingestion publishes `telemetry.raw`; correlation consumes `telemetry.raw` and publishes `situations.detected`.
- **River API (verified on river 0.25):** `learn_one(x: dict)` mutates in place and returns `None` — never chain `m = m.learn_one(x)`. `river.stats.Mean()` / `river.stats.Var()` have `.update(v)` and `.get()`. Std dev = `Var().get() ** 0.5`.
- **Determinism:** correlation logic must be deterministic for fixed input sequences (no wall-clock reads inside scoring/correlation — timestamps come from the events).
- **Baselines in tests MUST be jittered, never dead-flat (verified, load-bearing).** The z-score detector divides by the running std dev. A baseline of N identical values drives std dev to ~0, so *any* later deviation (even 9.9 vs a 10.0 mean) scores as a massive z and falsely flags as anomalous. Every test that primes a baseline uses `random.Random(seed).gauss(mean, sigma)` with `sigma≈1.0`, seeded for determinism. Do NOT "simplify" a jittered baseline back to a constant — it will break the anomaly assertions. This was confirmed empirically against river 0.25 while writing the plan.
- **Fix the Slice-0 deferred minor:** `common/bus.py` `_CONSUMER = "c1"` is hardcoded; parameterize it (Task 6) now that a real consumer runs.

---

### Task 1: JSON envelope helpers (`common/envelope.py`)

**Files:**
- Create: `common/envelope.py`
- Test: `tests/test_envelope.py`

**Interfaces:**
- Consumes: `common.contracts` models; a `BusClient` (duck-typed — anything with `publish`/`consume`).
- Produces:
  - `publish_model(bus, topic: str, model: BaseModel) -> None` — publishes `{"data": model.model_dump_json()}`.
  - `iter_models(bus, topic: str, group: str, model_type: type[T]) -> Iterator[T]` — consumes dicts, validates each `fields["data"]` into `model_type`, yields typed models.
  - `decode_model(fields: dict, model_type: type[T]) -> T` — parse one raw bus dict into a model (used by both the iterator and tests).

- [ ] **Step 1: Write the failing test**

`tests/test_envelope.py`:

```python
from datetime import datetime, timezone

from common.contracts import Situation, SituationStatus, TelemetryEvent, TelemetryKind
from common.envelope import decode_model, iter_models, publish_model

NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)


class FakeBus:
    def __init__(self):
        self.published: list[tuple[str, dict]] = []
        self._to_consume: dict[str, list[dict]] = {}

    def publish(self, topic, message):
        self.published.append((topic, message))

    def consume(self, topic, group):
        yield from self._to_consume.get(topic, [])


def _event(name="cpu", value=0.9):
    return TelemetryEvent(
        source="prometheus", kind=TelemetryKind.METRIC, name=name,
        value=value, labels={"pod": "web-1"}, ts=NOW, fingerprint="fp1",
    )


def test_publish_model_wraps_json_in_data_field():
    bus = FakeBus()
    publish_model(bus, "telemetry.raw", _event())
    topic, message = bus.published[0]
    assert topic == "telemetry.raw"
    assert set(message.keys()) == {"data"}
    assert '"name":"cpu"' in message["data"]


def test_decode_model_roundtrips():
    bus = FakeBus()
    publish_model(bus, "telemetry.raw", _event())
    _topic, message = bus.published[0]
    restored = decode_model(message, TelemetryEvent)
    assert restored == _event()


def test_iter_models_yields_typed_models():
    bus = FakeBus()
    ev = _event()
    bus._to_consume["telemetry.raw"] = [{"data": ev.model_dump_json()}]
    out = list(iter_models(bus, "telemetry.raw", "g1", TelemetryEvent))
    assert out == [ev]


def test_iter_models_handles_situation_with_members():
    bus = FakeBus()
    sit = Situation(
        id="s1", status=SituationStatus.DETECTED, member_events=[_event(), _event("mem", 0.8)],
        severity="high", first_seen=NOW, last_seen=NOW, signature="sig1",
    )
    bus._to_consume["situations.detected"] = [{"data": sit.model_dump_json()}]
    out = list(iter_models(bus, "situations.detected", "g1", Situation))
    assert out == [sit]
    assert len(out[0].member_events) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_envelope.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'common.envelope'`.

- [ ] **Step 3: Write the envelope helpers**

`common/envelope.py`:

```python
"""JSON-envelope serialization for putting Pydantic models on the flat bus.

The bus (Redis Streams) carries dict[str, str]. Nested contracts don't fit
flat fields, so a model travels as its JSON string in a single "data" field.
Every service uses these helpers; none hand-rolls serialization (see ADR-001).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def publish_model(bus, topic: str, model: BaseModel) -> None:
    bus.publish(topic, {"data": model.model_dump_json()})


def decode_model(fields: dict, model_type: type[T]) -> T:
    return model_type.model_validate_json(fields["data"])


def iter_models(bus, topic: str, group: str, model_type: type[T]) -> Iterator[T]:
    for fields in bus.consume(topic, group):
        yield decode_model(fields, model_type)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_envelope.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add common/envelope.py tests/test_envelope.py
git commit -m "feat: add JSON envelope helpers for bus transport"
```

---

### Task 2: Ingestion normalization (`services/ingestion/normalize.py`)

**Files:**
- Create: `services/ingestion/normalize.py`
- Test: `services/ingestion/tests/__init__.py`, `services/ingestion/tests/test_normalize.py`

**Interfaces:**
- Consumes: `common.contracts.TelemetryEvent`, `TelemetryKind`.
- Produces:
  - `compute_fingerprint(source: str, name: str, labels: dict[str, str]) -> str` — stable hex digest of identity (sorted labels), for dedup.
  - `normalize(raw: dict) -> TelemetryEvent` — maps a raw signal dict into a `TelemetryEvent`. Raw shape: `{"source", "kind", "name", "value"?, "payload"?, "labels"?, "ts"?}`. Missing `ts` → the event carries the string as-is only if present; if absent, raise `ValueError` (caller supplies ts). `fingerprint` is computed, never taken from raw.

- [ ] **Step 1: Write the failing test**

`services/ingestion/tests/__init__.py`: (empty file)

`services/ingestion/tests/test_normalize.py`:

```python
from datetime import datetime, timezone

import pytest

from common.contracts import TelemetryEvent, TelemetryKind
from services.ingestion.normalize import compute_fingerprint, normalize

TS = "2026-08-13T00:00:00+00:00"


def test_fingerprint_is_stable_and_label_order_independent():
    a = compute_fingerprint("prom", "cpu", {"pod": "web-1", "ns": "prod"})
    b = compute_fingerprint("prom", "cpu", {"ns": "prod", "pod": "web-1"})
    assert a == b
    assert isinstance(a, str) and len(a) >= 8


def test_fingerprint_differs_on_different_identity():
    a = compute_fingerprint("prom", "cpu", {"pod": "web-1"})
    b = compute_fingerprint("prom", "cpu", {"pod": "web-2"})
    assert a != b


def test_normalize_builds_telemetry_event():
    raw = {
        "source": "prometheus", "kind": "metric", "name": "cpu_usage",
        "value": 0.97, "labels": {"pod": "web-1"}, "ts": TS,
    }
    ev = normalize(raw)
    assert isinstance(ev, TelemetryEvent)
    assert ev.source == "prometheus"
    assert ev.kind == TelemetryKind.METRIC
    assert ev.name == "cpu_usage"
    assert ev.value == 0.97
    assert ev.labels == {"pod": "web-1"}
    assert ev.ts == datetime(2026, 8, 13, tzinfo=timezone.utc)
    assert ev.fingerprint == compute_fingerprint("prometheus", "cpu_usage", {"pod": "web-1"})


def test_normalize_defaults_missing_optional_fields():
    raw = {"source": "loki", "kind": "log", "name": "err", "ts": TS}
    ev = normalize(raw)
    assert ev.value is None
    assert ev.labels == {}


def test_normalize_raises_without_ts():
    with pytest.raises(ValueError):
        normalize({"source": "s", "kind": "metric", "name": "n"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/ingestion/tests/test_normalize.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.ingestion.normalize'`.

- [ ] **Step 3: Write the normalizer**

`services/ingestion/normalize.py`:

```python
"""Normalize raw telemetry signals into canonical TelemetryEvents.

One canonical shape means every downstream service is source-agnostic, and a
stable fingerprint kills duplicate alerts at the door (see flow.md 5.1).
"""

from __future__ import annotations

import hashlib

from common.contracts import TelemetryEvent, TelemetryKind


def compute_fingerprint(source: str, name: str, labels: dict[str, str]) -> str:
    parts = [source, name]
    for key in sorted(labels):
        parts.append(f"{key}={labels[key]}")
    digest = hashlib.sha1("|".join(parts).encode()).hexdigest()
    return digest[:16]


def normalize(raw: dict) -> TelemetryEvent:
    if "ts" not in raw:
        raise ValueError("raw telemetry must include a 'ts' field")
    labels = raw.get("labels") or {}
    return TelemetryEvent(
        source=raw["source"],
        kind=TelemetryKind(raw["kind"]),
        name=raw["name"],
        value=raw.get("value"),
        payload=raw.get("payload"),
        labels=labels,
        ts=raw["ts"],
        fingerprint=compute_fingerprint(raw["source"], raw["name"], labels),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest services/ingestion/tests/test_normalize.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add services/ingestion/normalize.py services/ingestion/tests/
git commit -m "feat: add ingestion normalization and fingerprinting"
```

---

### Task 3: File telemetry source (`services/ingestion/adapters/file_source.py`)

**Files:**
- Create: `services/ingestion/adapters/__init__.py`, `services/ingestion/adapters/file_source.py`
- Test: `services/ingestion/tests/test_file_source.py`

**Interfaces:**
- Consumes: `common.contracts.TelemetryEvent`; `services.ingestion.normalize.normalize`.
- Produces: `FileTelemetrySource(path: str)` implementing the `TelemetrySource` protocol:
  - `poll() -> list[TelemetryEvent]` — reads all JSONL lines from `path`, normalizes each, returns the list.
  - `subscribe() -> Iterator[TelemetryEvent]` — yields the same events one at a time.
  - Satisfies `common.interfaces.TelemetrySource` (runtime_checkable).

- [ ] **Step 1: Write the failing test**

`services/ingestion/tests/test_file_source.py`:

```python
from common.contracts import TelemetryEvent
from common.interfaces import TelemetrySource
from services.ingestion.adapters.file_source import FileTelemetrySource

SAMPLE = (
    '{"source":"prom","kind":"metric","name":"cpu","value":0.9,'
    '"labels":{"pod":"web-1"},"ts":"2026-08-13T00:00:00+00:00"}\n'
    '{"source":"prom","kind":"metric","name":"cpu","value":0.95,'
    '"labels":{"pod":"web-2"},"ts":"2026-08-13T00:00:01+00:00"}\n'
)


def test_file_source_satisfies_protocol(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(SAMPLE)
    src = FileTelemetrySource(str(f))
    assert isinstance(src, TelemetrySource)


def test_poll_reads_and_normalizes_all_lines(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(SAMPLE)
    events = FileTelemetrySource(str(f)).poll()
    assert len(events) == 2
    assert all(isinstance(e, TelemetryEvent) for e in events)
    assert events[0].name == "cpu"
    assert events[1].labels == {"pod": "web-2"}


def test_poll_skips_blank_lines(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(SAMPLE + "\n   \n")
    assert len(FileTelemetrySource(str(f)).poll()) == 2


def test_subscribe_yields_each_event(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(SAMPLE)
    out = list(FileTelemetrySource(str(f)).subscribe())
    assert len(out) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/ingestion/tests/test_file_source.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.ingestion.adapters'`.

- [ ] **Step 3: Write the file source**

`services/ingestion/adapters/__init__.py`:

```python
"""Ingestion adapters: concrete TelemetrySource implementations."""
```

`services/ingestion/adapters/file_source.py`:

```python
"""A TelemetrySource that reads newline-delimited JSON (JSONL) from a file.

Lets the ingestion poll loop run with no external infra. A real
PrometheusSource is just another TelemetrySource behind the same protocol.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

from common.contracts import TelemetryEvent
from services.ingestion.normalize import normalize


class FileTelemetrySource:
    def __init__(self, path: str) -> None:
        self._path = path

    def poll(self) -> list[TelemetryEvent]:
        events: list[TelemetryEvent] = []
        with open(self._path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                events.append(normalize(json.loads(line)))
        return events

    def subscribe(self) -> Iterator[TelemetryEvent]:
        yield from self.poll()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest services/ingestion/tests/test_file_source.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add services/ingestion/adapters/ services/ingestion/tests/test_file_source.py
git commit -m "feat: add file telemetry source"
```

---

### Task 4: Ingestion POST /ingest endpoint (`services/ingestion/app.py`)

**Files:**
- Modify: `services/ingestion/app.py`
- Test: `services/ingestion/tests/test_ingest_endpoint.py`

**Interfaces:**
- Consumes: `services.base.create_app`; `services.ingestion.normalize.normalize`; `common.envelope.publish_model`; `app.state.bus`.
- Produces: `POST /ingest` on ingestion-service. Request body: `{"events": [ <raw signal>, ... ]}`. For each raw signal: `normalize` it, `publish_model(bus, "telemetry.raw", event)`. Response: `{"accepted": <n>}` (HTTP 200). A raw signal missing `ts` gets it defaulted to server-provided ISO time BEFORE normalize (so the endpoint is forgiving where the file source is strict).

- [ ] **Step 1: Write the failing test**

`services/ingestion/tests/test_ingest_endpoint.py`:

```python
from fastapi.testclient import TestClient

from common.contracts import TelemetryEvent
from common.envelope import decode_model


class RecordingBus:
    def __init__(self):
        self.published = []

    def publish(self, topic, message):
        self.published.append((topic, message))

    def consume(self, topic, group):
        yield from ()


def _client():
    from services.ingestion.app import app

    app.state.bus = RecordingBus()
    return TestClient(app), app.state.bus


def test_ingest_publishes_normalized_events_to_telemetry_raw():
    client, bus = _client()
    resp = client.post("/ingest", json={"events": [
        {"source": "prom", "kind": "metric", "name": "cpu", "value": 0.9,
         "labels": {"pod": "web-1"}, "ts": "2026-08-13T00:00:00+00:00"},
    ]})
    assert resp.status_code == 200
    assert resp.json() == {"accepted": 1}
    assert len(bus.published) == 1
    topic, message = bus.published[0]
    assert topic == "telemetry.raw"
    ev = decode_model(message, TelemetryEvent)
    assert ev.name == "cpu"
    assert ev.fingerprint  # computed


def test_ingest_defaults_missing_ts():
    client, bus = _client()
    resp = client.post("/ingest", json={"events": [
        {"source": "prom", "kind": "metric", "name": "cpu", "value": 0.1},
    ]})
    assert resp.status_code == 200
    ev = decode_model(bus.published[0][1], TelemetryEvent)
    assert ev.ts is not None


def test_ingest_empty_batch_accepts_zero():
    client, bus = _client()
    resp = client.post("/ingest", json={"events": []})
    assert resp.json() == {"accepted": 0}
    assert bus.published == []


def test_health_still_works():
    client, _ = _client()
    assert client.get("/health").json() == {"service": "ingestion-service", "status": "ok"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/ingestion/tests/test_ingest_endpoint.py -v`
Expected: FAIL — 404 on `/ingest` (endpoint not defined yet).

- [ ] **Step 3: Extend the ingestion app**

Replace `services/ingestion/app.py` with:

```python
"""Ingestion service: normalize + dedup telemetry onto the bus."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel

from common.envelope import publish_model
from services.base import create_app
from services.ingestion.normalize import normalize

app = create_app("ingestion-service")


class IngestBatch(BaseModel):
    events: list[dict]


@app.post("/ingest")
def ingest(batch: IngestBatch) -> dict[str, int]:
    accepted = 0
    for raw in batch.events:
        if "ts" not in raw:
            raw = {**raw, "ts": datetime.now(timezone.utc).isoformat()}
        event = normalize(raw)
        publish_model(app.state.bus, "telemetry.raw", event)
        accepted += 1
    return {"accepted": accepted}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest services/ingestion/tests/test_ingest_endpoint.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add services/ingestion/app.py services/ingestion/tests/test_ingest_endpoint.py
git commit -m "feat: add POST /ingest endpoint to ingestion service"
```

---

### Task 5: RiverCorrelator — detect + correlate (`services/correlation/adapters/river_correlator.py`)

**Files:**
- Create: `services/correlation/adapters/__init__.py`, `services/correlation/adapters/river_correlator.py`
- Test: `services/correlation/tests/__init__.py`, `services/correlation/tests/test_river_correlator.py`

**Interfaces:**
- Consumes: `common.contracts` (`TelemetryEvent`, `Situation`, `SituationStatus`); `river.stats`.
- Produces: `RiverCorrelator(z_threshold: float = 3.0)` implementing the `Correlator` protocol:
  - `detect(event: TelemetryEvent) -> float` — returns the current z-score of `event.value` against a per-`event.name` online baseline (`river.stats.Mean` + `Var`), THEN updates that baseline with the value. Events with `value is None` score `0.0`. First observation of a metric (std dev 0) scores `0.0`.
  - `is_anomaly(event: TelemetryEvent) -> bool` — `detect(event) > z_threshold` **without** re-scoring (helper that calls detect once). NOTE: to avoid double-updating the baseline, `is_anomaly` calls `detect` exactly once and compares.
  - `correlate(events: list[TelemetryEvent]) -> Situation` — builds ONE `Situation` from a list of (already-anomalous) events: `member_events` = the events, `first_seen`/`last_seen` from min/max `ts`, `signature` = stable hash of sorted member fingerprints, `severity` from `_severity_band(max score)` — but since scores aren't stored on events, severity is passed in. Signature: `hashlib.sha1` of `"|".join(sorted(e.fingerprint for e in events))`, first 16 hex chars. `id` = `"sit-" + signature`. Raises `ValueError` on empty list.
  - `retrain(training_data: list[dict]) -> None` — no-op stub this slice (feedback loop is Slice 4).
  - `_severity_band(score: float) -> str` — `>= 8 -> "high"`, `>= 5 -> "medium"`, else `"low"`.

- [ ] **Step 1: Write the failing test**

`services/correlation/tests/__init__.py`: (empty file)

`services/correlation/tests/test_river_correlator.py`:

```python
import random
from datetime import datetime, timezone

import pytest

from common.contracts import Situation, SituationStatus, TelemetryEvent, TelemetryKind
from services.correlation.adapters.river_correlator import RiverCorrelator


def _event(name="cpu", value=10.0, fp="fp", ts_sec=0):
    return TelemetryEvent(
        source="prom", kind=TelemetryKind.METRIC, name=name, value=value,
        labels={}, ts=datetime(2026, 8, 13, 0, 0, ts_sec, tzinfo=timezone.utc),
        fingerprint=fp,
    )


def _feed_baseline(correlator, n=200, mean=10.0, sigma=1.0, seed=42):
    """Feed a baseline with realistic jitter.

    A dead-flat baseline (every value identical) drives std dev to ~0, which
    makes the z-score explode on ANY later deviation. Real metrics vary, so the
    baseline must too. Seeded for determinism.
    """
    rng = random.Random(seed)
    for _ in range(n):
        correlator.detect(_event(value=round(rng.gauss(mean, sigma), 3)))


def test_detect_scores_zero_for_none_value():
    c = RiverCorrelator()
    assert c.detect(_event(value=None)) == 0.0


def test_detect_flags_outlier_after_baseline():
    c = RiverCorrelator(z_threshold=3.0)
    _feed_baseline(c)  # jittered baseline around 10
    normal_score = c.detect(_event(value=10.2))
    outlier_score = c.detect(_event(value=100.0))
    assert normal_score < 3.0
    assert outlier_score > 3.0


def test_is_anomaly_matches_threshold():
    c = RiverCorrelator(z_threshold=3.0)
    _feed_baseline(c)
    assert c.is_anomaly(_event(value=100.0)) is True
    # a value near the mean is not anomalous
    assert c.is_anomaly(_event(value=10.1)) is False


def test_correlate_builds_one_situation_with_stable_signature():
    c = RiverCorrelator()
    events = [_event(fp="a", ts_sec=1), _event(fp="b", ts_sec=5), _event(fp="c", ts_sec=3)]
    sit = c.correlate(events, severity="high")
    assert isinstance(sit, Situation)
    assert sit.status == SituationStatus.DETECTED
    assert len(sit.member_events) == 3
    assert sit.severity == "high"
    assert sit.first_seen.second == 1
    assert sit.last_seen.second == 5
    # signature is order-independent and stable
    sit2 = c.correlate(list(reversed(events)), severity="high")
    assert sit.signature == sit2.signature
    assert sit.id == "sit-" + sit.signature


def test_correlate_raises_on_empty():
    with pytest.raises(ValueError):
        RiverCorrelator().correlate([], severity="low")


def test_severity_band():
    c = RiverCorrelator()
    assert c._severity_band(9.0) == "high"
    assert c._severity_band(6.0) == "medium"
    assert c._severity_band(2.0) == "low"


def test_retrain_is_noop():
    RiverCorrelator().retrain([])  # must not raise
```

- [ ] **Step 2: Add river dependency**

Run: `uv add river`
Expected: adds `river` (0.25+) to `[project.dependencies]` and updates `uv.lock`.

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest services/correlation/tests/test_river_correlator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.correlation.adapters'`.

- [ ] **Step 4: Write the correlator**

`services/correlation/adapters/__init__.py`:

```python
"""Correlation adapters: concrete Correlator implementations."""
```

`services/correlation/adapters/river_correlator.py`:

```python
"""Online anomaly detection + time/label correlation.

detect() maintains a per-metric online z-score baseline (river.stats). An
event scores high when its value is many std devs from that metric's running
mean. correlate() collapses a set of anomalous events into one Situation with
a stable signature so recurring storms are recognizable (see flow.md 5.2).

NOTE (river 0.25): stats objects update in place via .update(v) and read via
.get(); they are not chained.
"""

from __future__ import annotations

import hashlib

from river import stats

from common.contracts import Situation, SituationStatus, TelemetryEvent


class RiverCorrelator:
    def __init__(self, z_threshold: float = 3.0) -> None:
        self._z_threshold = z_threshold
        self._mean: dict[str, stats.Mean] = {}
        self._var: dict[str, stats.Var] = {}

    def detect(self, event: TelemetryEvent) -> float:
        if event.value is None:
            return 0.0
        name = event.name
        mean = self._mean.setdefault(name, stats.Mean())
        var = self._var.setdefault(name, stats.Var())
        m = mean.get()
        sd = var.get() ** 0.5
        score = 0.0 if sd == 0 else abs(event.value - m) / sd
        mean.update(event.value)
        var.update(event.value)
        return score

    def is_anomaly(self, event: TelemetryEvent) -> bool:
        return self.detect(event) > self._z_threshold

    def correlate(self, events: list[TelemetryEvent], severity: str = "low") -> Situation:
        if not events:
            raise ValueError("cannot correlate an empty event list")
        signature = self._signature(events)
        return Situation(
            id="sit-" + signature,
            status=SituationStatus.DETECTED,
            member_events=list(events),
            severity=severity,
            first_seen=min(e.ts for e in events),
            last_seen=max(e.ts for e in events),
            signature=signature,
        )

    def retrain(self, training_data: list[dict]) -> None:
        # Feedback-driven retraining lands in Slice 4; the method exists so the
        # Correlator protocol is satisfied now.
        return None

    def _severity_band(self, score: float) -> str:
        if score >= 8:
            return "high"
        if score >= 5:
            return "medium"
        return "low"

    @staticmethod
    def _signature(events: list[TelemetryEvent]) -> str:
        joined = "|".join(sorted(e.fingerprint for e in events))
        return hashlib.sha1(joined.encode()).hexdigest()[:16]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest services/correlation/tests/test_river_correlator.py -v`
Expected: PASS (7 passed).

- [ ] **Step 6: Commit**

```bash
git add services/correlation/adapters/ services/correlation/tests/ pyproject.toml uv.lock
git commit -m "feat: add RiverCorrelator with z-score detection and correlation"
```

---

### Task 6: Windowed correlation engine + parameterized consumer name

**Files:**
- Create: `services/correlation/engine.py`
- Modify: `common/bus.py` (parameterize `_CONSUMER`)
- Test: `services/correlation/tests/test_engine.py`, `tests/test_bus_consumer_name.py`

**Interfaces:**
- Consumes: `RiverCorrelator`; `common.contracts` (`TelemetryEvent`, `Situation`).
- Produces:
  - `CorrelationEngine(correlator, window_seconds: float = 30.0)`:
    - `add(event: TelemetryEvent) -> Situation | None` — scores the event; if anomalous, buffers it; when the buffer's time span (`last.ts - first.ts`) reaches `window_seconds` OR a flush is triggered, returns a correlated `Situation` and clears the buffer. Non-anomalous events return `None`. When an anomalous event's `ts` is more than `window_seconds` beyond the buffer's first event, it FLUSHES the existing buffer (returns that Situation) and starts a new buffer with the new event.
    - `flush() -> Situation | None` — force-correlate whatever is buffered (or `None` if empty).
    - severity for the emitted Situation = `_severity_band(max score seen in the window)`.
  - `common/bus.py`: `RedisBus.__init__(self, client, consumer_name: str = "c1")`; `consume` uses `self._consumer`. `make_bus(settings, consumer_name=...)` passes it through. Default preserves Slice-0 behavior.

- [ ] **Step 1: Write the failing test**

`services/correlation/tests/test_engine.py`:

```python
import random
from datetime import datetime, timezone

from common.contracts import Situation, TelemetryEvent, TelemetryKind
from services.correlation.adapters.river_correlator import RiverCorrelator
from services.correlation.engine import CorrelationEngine


def _event(value=10.0, fp="fp", ts_sec=0):
    return TelemetryEvent(
        source="prom", kind=TelemetryKind.METRIC, name="cpu", value=value,
        labels={}, ts=datetime(2026, 8, 13, 0, 0, ts_sec, tzinfo=timezone.utc),
        fingerprint=fp,
    )


def _prime(engine):
    # Feed a jittered baseline so later spikes are anomalous but normal values
    # are not. A dead-flat baseline drives std dev to ~0 and makes the z-score
    # explode on any deviation. Baseline values sit near the mean, so add()
    # returns None throughout. Seeded for determinism.
    rng = random.Random(42)
    for i in range(200):
        engine.add(_event(value=round(rng.gauss(10.0, 1.0), 3), fp=f"base{i}", ts_sec=0))


def test_non_anomalous_events_return_none():
    engine = CorrelationEngine(RiverCorrelator(), window_seconds=30)
    _prime(engine)
    assert engine.add(_event(value=10.1, fp="x", ts_sec=1)) is None


def test_flush_emits_situation_from_buffered_anomalies():
    engine = CorrelationEngine(RiverCorrelator(), window_seconds=30)
    _prime(engine)
    # three spikes within the window -> buffered, no emit yet
    assert engine.add(_event(value=100.0, fp="a", ts_sec=1)) is None
    assert engine.add(_event(value=120.0, fp="b", ts_sec=2)) is None
    sit = engine.flush()
    assert isinstance(sit, Situation)
    assert {e.fingerprint for e in sit.member_events} == {"a", "b"}
    assert sit.severity in {"high", "medium", "low"}


def test_window_span_triggers_emit():
    engine = CorrelationEngine(RiverCorrelator(), window_seconds=10)
    _prime(engine)
    engine.add(_event(value=100.0, fp="a", ts_sec=1))       # buffer starts at t=1
    # an anomaly at t=15 is >10s past buffer start -> flush old buffer, return it
    emitted = engine.add(_event(value=100.0, fp="b", ts_sec=15))
    assert isinstance(emitted, Situation)
    assert {e.fingerprint for e in emitted.member_events} == {"a"}
    # new buffer now holds "b"
    tail = engine.flush()
    assert {e.fingerprint for e in tail.member_events} == {"b"}


def test_flush_empty_returns_none():
    engine = CorrelationEngine(RiverCorrelator(), window_seconds=30)
    assert engine.flush() is None
```

`tests/test_bus_consumer_name.py`:

```python
def test_redisbus_accepts_custom_consumer_name():
    import fakeredis

    from common.bus import RedisBus

    client = fakeredis.FakeStrictRedis(decode_responses=True)
    bus = RedisBus(client=client, consumer_name="correlation-1")
    bus.publish("t", {"data": "x"})
    got = next(iter(bus.consume("t", group="g")))
    assert got == {"data": "x"}


def test_make_bus_passes_consumer_name():
    from common.bus import make_bus
    from common.config import Settings

    bus = make_bus(Settings(redis_url="redis://localhost:6379"), consumer_name="c-2")
    assert bus._consumer == "c-2"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest services/correlation/tests/test_engine.py tests/test_bus_consumer_name.py -v`
Expected: FAIL — `ModuleNotFoundError: services.correlation.engine`; `TypeError` on unexpected `consumer_name`.

- [ ] **Step 3: Parameterize the consumer name in `common/bus.py`**

Replace `common/bus.py` with:

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


class RedisBus:
    def __init__(self, client: redis.Redis, consumer_name: str = "c1") -> None:
        self._r = client
        self._consumer = consumer_name

    def publish(self, topic: str, message: dict) -> None:
        self._r.xadd(topic, message)

    def consume(self, topic: str, group: str) -> Iterator[dict]:
        try:
            self._r.xgroup_create(topic, group, id="0", mkstream=True)
        except redis.ResponseError as exc:  # group already exists
            if "BUSYGROUP" not in str(exc):
                raise
        while True:
            resp = self._r.xreadgroup(group, self._consumer, {topic: ">"}, count=1, block=1000)
            if not resp:
                continue
            for _stream, entries in resp:
                for entry_id, fields in entries:
                    self._r.xack(topic, group, entry_id)
                    yield fields


def make_bus(settings: Settings, consumer_name: str = "c1") -> RedisBus:
    return RedisBus(
        client=redis.from_url(settings.redis_url, decode_responses=True),
        consumer_name=consumer_name,
    )
```

- [ ] **Step 4: Write the correlation engine**

`services/correlation/engine.py`:

```python
"""Windowed correlation: buffer anomalous events and emit one Situation per window.

The engine scores each event via the correlator; anomalies accumulate in a
rolling time window keyed on event timestamps. When the window's span exceeds
window_seconds (or on an explicit flush), the buffer collapses into a single
Situation. Timestamps come from events, so behavior is deterministic.
"""

from __future__ import annotations

from common.contracts import Situation, TelemetryEvent
from services.correlation.adapters.river_correlator import RiverCorrelator


class CorrelationEngine:
    def __init__(self, correlator: RiverCorrelator, window_seconds: float = 30.0) -> None:
        self._correlator = correlator
        self._window = window_seconds
        self._buffer: list[TelemetryEvent] = []
        self._max_score = 0.0

    def add(self, event: TelemetryEvent) -> Situation | None:
        score = self._correlator.detect(event)
        if score <= self._correlator._z_threshold:
            return None
        emitted: Situation | None = None
        if self._buffer:
            span = (event.ts - self._buffer[0].ts).total_seconds()
            if span > self._window:
                emitted = self._correlate_buffer()
        self._buffer.append(event)
        self._max_score = max(self._max_score, score)
        return emitted

    def flush(self) -> Situation | None:
        if not self._buffer:
            return None
        return self._correlate_buffer()

    def _correlate_buffer(self) -> Situation:
        severity = self._correlator._severity_band(self._max_score)
        sit = self._correlator.correlate(self._buffer, severity=severity)
        self._buffer = []
        self._max_score = 0.0
        return sit
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest services/correlation/tests/test_engine.py tests/test_bus_consumer_name.py -v`
Expected: PASS (4 + 2 = 6 passed).

- [ ] **Step 6: Run the full suite (nothing regressed from the bus change)**

Run: `uv run pytest`
Expected: all pass (Slice-0 bus tests still green with the new default arg).

- [ ] **Step 7: Commit**

```bash
git add services/correlation/engine.py common/bus.py services/correlation/tests/test_engine.py tests/test_bus_consumer_name.py
git commit -m "feat: add windowed correlation engine; parameterize bus consumer name"
```

---

### Task 7: Correlation consumer thread wired via FastAPI lifespan (`services/correlation/app.py`)

**Files:**
- Create: `services/correlation/consumer.py`
- Modify: `services/correlation/app.py`
- Test: `services/correlation/tests/test_consumer.py`

**Interfaces:**
- Consumes: `CorrelationEngine`, `RiverCorrelator`; `common.envelope` (`iter_models`, `publish_model`); `common.contracts.TelemetryEvent`.
- Produces:
  - `run_consumer(bus, engine, stop_event) -> None` — loops over `iter_models(bus, "telemetry.raw", "correlation", TelemetryEvent)`; for each event calls `engine.add`; when it returns a `Situation`, `publish_model(bus, "situations.detected", sit)`. Checks `stop_event.is_set()` each iteration to exit cleanly. (For test determinism it also accepts a bus whose `consume` is finite; on natural exhaustion it flushes and publishes any final Situation.)
  - `services/correlation/app.py`: uses FastAPI `lifespan` to start `run_consumer` in a daemon thread on startup and signal the `stop_event` on shutdown. `app.state.bus` unchanged; keeps `/health`.

- [ ] **Step 1: Write the failing test**

`services/correlation/tests/test_consumer.py`:

```python
import random
import threading
from datetime import datetime, timezone

from common.contracts import Situation, TelemetryEvent, TelemetryKind
from common.envelope import decode_model
from services.correlation.adapters.river_correlator import RiverCorrelator
from services.correlation.consumer import run_consumer
from services.correlation.engine import CorrelationEngine


def _raw_event(value, fp, ts_sec):
    ev = TelemetryEvent(
        source="prom", kind=TelemetryKind.METRIC, name="cpu", value=value,
        labels={}, ts=datetime(2026, 8, 13, 0, 0, ts_sec, tzinfo=timezone.utc),
        fingerprint=fp,
    )
    return {"data": ev.model_dump_json()}


def _event(value, fp, ts_sec):
    return TelemetryEvent(
        source="prom", kind=TelemetryKind.METRIC, name="cpu", value=value,
        labels={}, ts=datetime(2026, 8, 13, 0, 0, ts_sec, tzinfo=timezone.utc),
        fingerprint=fp,
    )


def _prime_and_flush(engine, n=200, seed=42):
    """Warm the engine's per-metric baseline with jittered values, then flush.

    A dead-flat baseline drives std dev to ~0 and makes any deviation explode
    into a huge z-score. Even with jitter, river.stats.Var is UNSTABLE during
    warm-up (the first ~50 samples): a few early baseline values legitimately
    cross the z-threshold and get buffered as spurious anomalies. Since those
    baseline events all share ts_sec=0, the window never advances to flush them.
    So we prime the engine directly, then flush() once to DISCARD that warm-up
    noise — mirroring a real deployment that warms up before trusting anomalies.
    Seeded for determinism. Verified empirically against river 0.25.
    """
    rng = random.Random(seed)
    for i in range(n):
        engine.add(_event(round(rng.gauss(10.0, 1.0), 3), f"b{i}", 0))
    engine.flush()  # discard warm-up noise; return value intentionally ignored


class ScriptedBus:
    """A finite bus: consume() yields a fixed script then stops."""

    def __init__(self, script):
        self._script = script
        self.published = []

    def publish(self, topic, message):
        self.published.append((topic, message))

    def consume(self, topic, group):
        yield from self._script


def test_consumer_emits_situation_for_correlated_anomalies():
    # Pre-warm the engine, THEN feed only the two spikes through the consumer.
    engine = CorrelationEngine(RiverCorrelator(z_threshold=3.0), window_seconds=30)
    _prime_and_flush(engine)

    bus = ScriptedBus([_raw_event(100.0, "a", 1), _raw_event(120.0, "b", 2)])
    run_consumer(bus, engine, threading.Event())

    situations = [m for (t, m) in bus.published if t == "situations.detected"]
    assert len(situations) == 1
    sit = decode_model(situations[0], Situation)
    assert {e.fingerprint for e in sit.member_events} == {"a", "b"}


def test_consumer_publishes_nothing_without_anomalies():
    # Pre-warm, then feed only normal (near-mean) events -> no anomalies emitted.
    engine = CorrelationEngine(RiverCorrelator(z_threshold=3.0), window_seconds=30)
    _prime_and_flush(engine)

    bus = ScriptedBus([_raw_event(10.0, "n1", 1), _raw_event(10.1, "n2", 2)])
    run_consumer(bus, engine, threading.Event())
    assert [m for (t, m) in bus.published if t == "situations.detected"] == []


def test_consumer_stops_on_stop_event():
    # An infinite script; stop_event is pre-set so the loop exits immediately.
    def infinite():
        while True:
            yield _raw_event(10.0, "b", 0)

    class InfBus(ScriptedBus):
        def consume(self, topic, group):
            return infinite()

    bus = InfBus([])
    stop = threading.Event()
    stop.set()
    run_consumer(bus, engine=CorrelationEngine(RiverCorrelator()), stop_event=stop)
    assert bus.published == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/correlation/tests/test_consumer.py -v`
Expected: FAIL — `ModuleNotFoundError: services.correlation.consumer`.

- [ ] **Step 3: Write the consumer**

`services/correlation/consumer.py`:

```python
"""Bus consumer loop for correlation-service.

Consumes normalized telemetry, feeds it to the windowed correlation engine,
and publishes each emitted Situation to situations.detected. Runs in a daemon
thread started by the FastAPI lifespan; a stop_event allows clean shutdown.
"""

from __future__ import annotations

import threading

from common.contracts import Situation, TelemetryEvent
from common.envelope import iter_models, publish_model
from services.correlation.engine import CorrelationEngine


def run_consumer(bus, engine: CorrelationEngine, stop_event: threading.Event) -> None:
    for event in iter_models(bus, "telemetry.raw", "correlation", TelemetryEvent):
        if stop_event.is_set():
            break
        emitted = engine.add(event)
        if emitted is not None:
            publish_model(bus, "situations.detected", emitted)
    # Finite/interrupted stream: publish any final buffered Situation.
    tail: Situation | None = engine.flush()
    if tail is not None:
        publish_model(bus, "situations.detected", tail)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest services/correlation/tests/test_consumer.py -v`
Expected: PASS (3 passed).

Note: `test_consumer_stops_on_stop_event` — because `stop_event` is pre-set, the loop body breaks on the first iteration before publishing; the final `flush()` returns `None` (empty buffer). Passes.

- [ ] **Step 5: Wire the lifespan in `services/correlation/app.py`**

Replace `services/correlation/app.py` with:

```python
"""Correlation service: anomaly detection + event clustering -> Situation."""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from services.base import create_app
from services.correlation.adapters.river_correlator import RiverCorrelator
from services.correlation.consumer import run_consumer
from services.correlation.engine import CorrelationEngine


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop_event = threading.Event()
    engine = CorrelationEngine(RiverCorrelator())
    thread = threading.Thread(
        target=run_consumer, args=(app.state.bus, engine, stop_event), daemon=True
    )
    thread.start()
    app.state.consumer_stop = stop_event
    app.state.consumer_thread = thread
    try:
        yield
    finally:
        stop_event.set()


app = create_app("correlation-service")
app.router.lifespan_context = lifespan
```

- [ ] **Step 6: Run the service tests (health + consumer wiring)**

Run: `uv run pytest services/correlation/tests/ services/ -k "correlation or health" -v`
Expected: PASS. `/health` for correlation-service still returns `{"service": "correlation-service", "status": "ok"}`.

- [ ] **Step 7: Commit**

```bash
git add services/correlation/consumer.py services/correlation/app.py services/correlation/tests/test_consumer.py
git commit -m "feat: wire correlation consumer thread via FastAPI lifespan"
```

---

### Task 8: End-to-end slice acceptance + sample data + docs

**Files:**
- Create: `services/ingestion/sample_data/telemetry_sample.jsonl`
- Create: `tests/test_slice1_acceptance.py`
- Modify: `README.md` (roadmap: Slice 1 → done; add a one-line Slice-1 note under Quickstart)

**Interfaces:**
- Consumes: everything above — `normalize`, `publish_model`/`decode_model`, `RiverCorrelator`, `CorrelationEngine`, `run_consumer`.
- Produces: an in-process end-to-end test proving ingestion→correlation without Docker or a live Redis, using an in-memory bus; a committed sample JSONL; updated docs.

- [ ] **Step 1: Write the sample data**

`services/ingestion/sample_data/telemetry_sample.jsonl`:

```jsonl
{"source":"prom","kind":"metric","name":"cpu","value":10.0,"labels":{"pod":"web-1"},"ts":"2026-08-13T00:00:00+00:00"}
{"source":"prom","kind":"metric","name":"cpu","value":10.1,"labels":{"pod":"web-1"},"ts":"2026-08-13T00:00:01+00:00"}
{"source":"prom","kind":"metric","name":"cpu","value":9.9,"labels":{"pod":"web-1"},"ts":"2026-08-13T00:00:02+00:00"}
{"source":"prom","kind":"metric","name":"cpu","value":98.0,"labels":{"pod":"web-1"},"ts":"2026-08-13T00:00:03+00:00"}
{"source":"prom","kind":"metric","name":"cpu","value":102.0,"labels":{"pod":"web-2"},"ts":"2026-08-13T00:00:04+00:00"}
```

- [ ] **Step 2: Write the end-to-end acceptance test**

`tests/test_slice1_acceptance.py`:

```python
"""Slice-1 acceptance: telemetry in -> exactly one Situation out, in-process."""

import random
import threading

from common.contracts import Situation, TelemetryEvent
from common.envelope import decode_model, publish_model
from services.correlation.adapters.river_correlator import RiverCorrelator
from services.correlation.consumer import run_consumer
from services.correlation.engine import CorrelationEngine
from services.ingestion.adapters.file_source import FileTelemetrySource


class InMemoryBus:
    """Publish records into per-topic lists; consume replays telemetry.raw once."""

    def __init__(self):
        self.topics: dict[str, list[dict]] = {}

    def publish(self, topic, message):
        self.topics.setdefault(topic, []).append(message)

    def consume(self, topic, group):
        yield from list(self.topics.get(topic, []))


def _prime_and_flush(engine, n=200, seed=42):
    """Warm the engine's baseline with jittered values, then flush warm-up noise.

    A dead-flat baseline drives std dev to ~0 (any deviation → huge z). And even
    with jitter, river.stats.Var is unstable during warm-up, so a few early
    baseline values legitimately cross the z-threshold and buffer as spurious
    anomalies; sharing one timestamp, they never window-flush on their own. So we
    warm the engine directly, then flush() once to discard that noise — as a real
    deployment warms up before trusting anomalies. Seeded for determinism.
    """
    rng = random.Random(seed)
    for i in range(n):
        engine.add(
            TelemetryEvent.model_validate(
                {"source": "prom", "kind": "metric", "name": "cpu",
                 "value": round(rng.gauss(10.0, 1.0), 3), "labels": {},
                 "ts": "2026-08-13T00:00:00+00:00", "fingerprint": f"base{i}"}
            )
        )
    engine.flush()


def test_ingestion_to_correlation_emits_one_situation():
    bus = InMemoryBus()

    # 1. Correlation side: build the engine and warm its baseline (so the two
    #    sample spikes read as anomalies but the normal sample rows do not).
    engine = CorrelationEngine(RiverCorrelator(z_threshold=3.0), window_seconds=60)
    _prime_and_flush(engine)

    # 2. Ingestion side: read the sample file through the real FileTelemetrySource
    #    and publish the normalized events onto the bus (the ingestion->bus path).
    events = FileTelemetrySource(
        "services/ingestion/sample_data/telemetry_sample.jsonl"
    ).poll()
    for ev in events:
        publish_model(bus, "telemetry.raw", ev)

    # 3. Correlation consumer drains telemetry.raw and emits Situation(s).
    run_consumer(bus, engine, threading.Event())

    # 3. Assert exactly one Situation, containing the two spike events.
    situations = bus.topics.get("situations.detected", [])
    assert len(situations) == 1
    sit = decode_model(situations[0], Situation)
    assert isinstance(sit, Situation)
    assert len(sit.member_events) == 2
    spike_values = sorted(e.value for e in sit.member_events)
    assert spike_values == [98.0, 102.0]
    assert sit.signature and sit.id == "sit-" + sit.signature
```

- [ ] **Step 3: Run the acceptance test**

Run: `uv run pytest tests/test_slice1_acceptance.py -v`
Expected: PASS (1 passed).

- [ ] **Step 4: Run the full suite + lint**

Run: `uv run pytest` then `uv run ruff check .`
Expected: all tests pass; ruff clean. If ruff flags anything in the new files, fix it (e.g. unused imports) and re-run until green.

- [ ] **Step 5: Update the README roadmap**

In `README.md`, change the Slice 1 roadmap row status from `⏳ planned` to `✅ done` (ONLY the Slice 1 row; Slices 2-4 stay `⏳ planned`). Under the Quickstart section, add this line after the existing Slice-0 caveat line:

```
> Slice 1 adds `POST /ingest` on ingestion (8001) and a correlation consumer that emits `Situation`s onto the bus.
```

- [ ] **Step 6: Commit**

```bash
git add services/ingestion/sample_data/ tests/test_slice1_acceptance.py README.md
git commit -m "test: add slice-1 end-to-end acceptance; mark slice done"
```

---

## Self-Review

**1. Spec coverage** (against spec §4.1, §5, §6, §7 correlation, §10 Slice 1):
- Ingestion normalize + fingerprint (spec §7 ingestion) → Task 2 ✓
- File telemetry source / `TelemetrySource` binding (spec §6) → Task 3 ✓
- `POST /ingest` push path (design decision) → Task 4 ✓
- Bus serialization for nested models (gap the design closed) → Task 1 ✓
- `Correlator` detect + correlate (spec §6, §7 correlation) → Task 5 ✓
- Windowed clustering into one `Situation` (spec §4.1 "collapse alert storms") → Task 6 ✓
- Consumer thread emitting `situations.detected` (spec §4.2 topics) → Task 7 ✓
- End-to-end `ingestion → correlation` (spec §10 Slice 1 deliverable) → Task 8 ✓
- Slice-0 deferred minor (`_CONSUMER` hardcoded) → Task 6 ✓
- *Deferred by design (not gaps):* `retrain` real logic (Slice 4), RCA/governance/action (Slices 2-3), real Prometheus source (later). `situations.detected` has no consumer yet — that's `rca-service` in Slice 2.

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N". Every code and test step is complete. `retrain` is an intentional documented no-op stub, not a placeholder.

**3. Type consistency:**
- `publish_model(bus, topic, model)` / `decode_model(fields, model_type)` / `iter_models(bus, topic, group, model_type)` — defined Task 1, used identically in Tasks 4, 7, 8. ✓
- `normalize(raw) -> TelemetryEvent`, `compute_fingerprint(source, name, labels)` — defined Task 2, used in Tasks 3, 4. ✓
- `RiverCorrelator(z_threshold=3.0)` with `detect`/`is_anomaly`/`correlate(events, severity)`/`retrain`/`_severity_band`/`_signature` — defined Task 5, used in Tasks 6, 7, 8. `correlate` takes `severity` kwarg consistently. ✓
- `CorrelationEngine(correlator, window_seconds=30.0)` with `add`/`flush` — defined Task 6, used in Tasks 7, 8. ✓
- `run_consumer(bus, engine, stop_event)` — defined Task 7, used in Task 8. ✓
- `RedisBus(client, consumer_name="c1")`, `make_bus(settings, consumer_name="c1")` — Task 6; default preserves Slice-0 callers. ✓
- Bus envelope shape `{"data": ...}` consistent across Tasks 1, 6-test, 7, 8. ✓
- `Situation.id == "sit-" + signature` asserted consistently in Tasks 5 and 8. ✓

One cross-task note verified: Task 6's `CorrelationEngine.add` uses `self._correlator._z_threshold` and `_severity_band` — both defined on `RiverCorrelator` in Task 5. The engine depends on the concrete `RiverCorrelator` (not just the `Correlator` protocol) because it needs the threshold and severity band; this is intentional and the constructor is typed to `RiverCorrelator`. No inconsistency.
