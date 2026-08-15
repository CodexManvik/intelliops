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
            events.append(normalize({
                "source": "prometheus",
                "kind": "metric",
                "name": name,
                "value": float(raw_value),
                "labels": {k: v for k, v in metric.items() if k != "__name__"},
                # normalize() requires a 'ts'; Prometheus returns epoch seconds.
                "ts": datetime.fromtimestamp(float(ts_epoch), tz=UTC).isoformat(),
            }))
        return events

    def subscribe(self) -> Iterator[TelemetryEvent]:
        yield from self.poll()
