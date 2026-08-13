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
