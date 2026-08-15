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
        try:
            body = resp.json()
        except ValueError as exc:
            # A 200 response with a malformed / non-JSON body (e.g. a reverse
            # proxy returning an HTML error page with status 200). Treat like
            # any other unusable response rather than raising out of poll().
            logger.info("prometheus returned non-JSON body (%s); will retry next poll", exc.__class__.__name__)
            return []
        if not isinstance(body, dict) or body.get("status") != "success":
            return []
        events: list[TelemetryEvent] = []
        for entry in body.get("data", {}).get("result", []):
            metric = entry.get("metric", {})
            name = metric.get("__name__", "unknown")
            value_pair = entry.get("value", [0.0, "0"])
            if not isinstance(value_pair, list) or len(value_pair) < 2:
                # Malformed result entry (e.g. a short `value` array). Skip
                # just this entry so one junk entry doesn't drop the rest.
                logger.info("prometheus result entry has malformed 'value'; skipping entry")
                continue
            ts_epoch, raw_value = value_pair[0], value_pair[1]
            try:
                events.append(normalize({
                    "source": "prometheus",
                    "kind": "metric",
                    "name": name,
                    "value": float(raw_value),
                    "labels": {k: v for k, v in metric.items() if k != "__name__"},
                    # normalize() requires a 'ts'; Prometheus returns epoch seconds.
                    "ts": datetime.fromtimestamp(float(ts_epoch), tz=UTC).isoformat(),
                }))
            except (TypeError, ValueError) as exc:
                logger.info("prometheus result entry could not be normalized (%s); skipping entry", exc.__class__.__name__)
                continue
        return events

    def subscribe(self) -> Iterator[TelemetryEvent]:
        yield from self.poll()
