"""Ingestion service: normalize + dedup telemetry onto the bus."""

from __future__ import annotations

from datetime import UTC, datetime

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
            raw = {**raw, "ts": datetime.now(UTC).isoformat()}
        event = normalize(raw)
        publish_model(app.state.bus, "telemetry.raw", event)
        accepted += 1
    return {"accepted": accepted}
