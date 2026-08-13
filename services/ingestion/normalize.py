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
