"""Record what the Python /ingest route accepts and publishes, odd inputs included.

uv run python rust/harness/record_ingest.py
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from services.ingestion.app import app

OUT = Path(__file__).resolve().parents[1] / "app-cases" / "ingest.json"


class Bus:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def publish(self, topic: str, message: dict) -> None:
        self.sent.append(json.loads(message["data"]))


BASE = {"source": "s", "kind": "metric", "name": "n", "ts": "2026-01-02T03:04:05Z"}
MISSING = object()

VARIANTS: dict[str, list] = {
    "ts": [
        "2026-01-02T03:04:05Z",
        "2026-01-02T03:04:05.123Z",
        "2026-01-02T03:04:05.123456789Z",
        "2026-01-02T03:04:05+05:30",
        "2026-01-02T03:04:05+0530",
        "2026-01-02 03:04:05",
        "2026-01-02T03:04:05",
        "2026-01-02t03:04:05z",
        "2026-01-02_03:04:05",
        "2026-01-02T03:04",
        "2026-01-02",
        " 2026-01-02T03:04:05Z",
        "1767323045",
        "1767323045.5",
        1767323045,
        1767323045.1234567,
        1767323045123,
        -1,
        0,
        True,
        "",
        "yesterday",
        None,
        [],
    ],
    "value": [
        1,
        1.5,
        0,
        -3,
        1e308,
        "1.5",
        " 2 ",
        "1_000",
        "1e3",
        "abc",
        "",
        True,
        False,
        None,
        [],
        {},
    ],
    "labels": [
        {},
        {"a": "b"},
        {"service": "x", "a": ""},
        {"a": 1},
        {"a": None},
        {"a": True},
        None,
        [],
        "",
        0,
        "x",
        [1],
    ],
    "payload": [None, {}, {"a": [1, {"b": None}]}, [], "x", 1],
    "kind": ["metric", "log", "trace", "METRIC", "other", "", 1, None],
    "source": ["s", "", 1, None, True],
    "name": ["n", "métrique 📈", "", 1, None],
}


def cases() -> list[dict]:
    events = [dict(BASE)]
    for field, values in VARIANTS.items():
        events += [{**BASE, field: v} for v in values]
        events.append({k: v for k, v in BASE.items() if k != field})
    events.append({**BASE, "extra": 1, "fingerprint": "forged"})
    bodies = [{"events": [e]} for e in events]
    bodies += [{"events": []}, {}, {"events": None}, {"events": {}}, {"events": [1]}, [], "x"]
    bodies.append({"events": [BASE, {**BASE, "kind": "nope"}, BASE]})
    return [{"body": b} for b in bodies]


def main() -> None:
    results = []
    with TestClient(app, raise_server_exceptions=False) as client:
        for case in cases():
            bus = app.state.bus = Bus()
            response = client.post("/ingest", json=case["body"])
            ok = response.status_code == 200
            results.append(
                {
                    **case,
                    "status": response.status_code,
                    "response": response.json() if ok else None,
                    "published": bus.sent,
                }
            )
    OUT.write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    by_status: dict[int, int] = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    print(len(results), "cases", by_status)


if __name__ == "__main__":
    main()
