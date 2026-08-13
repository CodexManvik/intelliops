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
