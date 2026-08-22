from datetime import UTC, datetime

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
        "source": "prometheus",
        "kind": "metric",
        "name": "cpu_usage",
        "value": 0.97,
        "labels": {"pod": "web-1"},
        "ts": TS,
    }
    ev = normalize(raw)
    assert isinstance(ev, TelemetryEvent)
    assert ev.source == "prometheus"
    assert ev.kind == TelemetryKind.METRIC
    assert ev.name == "cpu_usage"
    assert ev.value == 0.97
    assert ev.labels == {"pod": "web-1"}
    assert ev.ts == datetime(2026, 8, 13, tzinfo=UTC)
    assert ev.fingerprint == compute_fingerprint("prometheus", "cpu_usage", {"pod": "web-1"})


def test_normalize_defaults_missing_optional_fields():
    raw = {"source": "loki", "kind": "log", "name": "err", "ts": TS}
    ev = normalize(raw)
    assert ev.value is None
    assert ev.labels == {}


def test_normalize_raises_without_ts():
    with pytest.raises(ValueError):
        normalize({"source": "s", "kind": "metric", "name": "n"})
