"""Record what the running stack does, as the oracle for the port.

Drives incidents through the demo app (approve, reject, let time out), then
dumps every bus stream, the read/governance GET responses and the database.
Each Rust service must parse these envelopes and reproduce the downstream ones.

    python rust/harness/capture.py
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
from pathlib import Path

from measure import COMPOSE, ROOT, http, run

OUT = ROOT / "rust" / "app-cases" / "golden"
DEMO = "http://127.0.0.1:8080"
GOV = "http://127.0.0.1:8005"
READ = "http://127.0.0.1:8007"
APPROVER = "oncall-alice"
TOPICS = [
    "telemetry.raw",
    "situations.detected",
    "situations.suppressed",
    "situations.diagnosed",
    "remediation.outcomes",
]
READS = {
    "read_situations": f"{READ}/situations",
    "read_metrics": f"{READ}/metrics",
    "read_outcomes": f"{READ}/outcomes",
    "read_system": f"{READ}/system",
    "governance_audit": f"{GOV}/audit",
    "governance_playbooks": f"{GOV}/playbooks",
    "governance_approvals": f"{GOV}/approvals",
}


def reset() -> None:
    http(f"{DEMO}/fix", {})
    http("http://127.0.0.1:8002/reset-baseline", {})
    http(f"{READ}/reset", {})
    http(f"{GOV}/reset-approvals", {})


def pending() -> list[dict]:
    return [a for a in json.loads(http(f"{GOV}/approvals")) if a["status"] == "pending"]


def incident(decision: str | None) -> str:
    """Break the demo app, wait for an approval request, then decide it (or not)."""
    reset()
    time.sleep(60)  # let the detector learn a healthy baseline again
    http(f"{DEMO}/break", {})
    for _ in range(120):
        waiting = pending()
        if waiting:
            break
        time.sleep(1)
    else:
        return "no approval requested"
    approval = waiting[0]
    if decision is None:
        time.sleep(45)  # past the HITL poll timeout
        return f"{approval['id']}: left to time out"
    try:
        http(
            f"{GOV}/approvals/{approval['id']}/decide",
            {"decision": decision, "decided_by": APPROVER},
        )
    except urllib.error.HTTPError as exc:
        return f"{approval['id']}: decide failed {exc.code}"
    time.sleep(30)
    return f"{approval['id']}: {decision}"


def dump_streams() -> dict[str, int]:
    counts = {}
    for topic in TOPICS:
        raw = run(
            [*COMPOSE, "exec", "-T", "redis", "redis-cli", "--json", "XRANGE", topic, "-", "+"]
        )
        entries = json.loads(raw) if raw else []
        # telemetry.raw is large and repetitive; a slice is enough to parse-test.
        if topic == "telemetry.raw":
            entries = entries[-500:]
        counts[topic] = len(entries)
        (OUT / f"stream.{topic}.json").write_text(json.dumps(entries, indent=1), encoding="utf-8")
    return counts


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    scenarios = {
        name: incident(d)
        for name, d in [
            ("approve", "approved"),
            ("reject", "rejected"),
            ("timeout", None),
        ]
    }
    counts = dump_streams()
    for name, url in READS.items():
        body = json.loads(http(url))
        (OUT / f"{name}.json").write_text(json.dumps(body, indent=1), encoding="utf-8")
    for name, flag in [("schema", "--schema-only"), ("data", "--data-only")]:
        sql = subprocess.run(
            [
                *COMPOSE,
                "exec",
                "-T",
                "postgres",
                "pg_dump",
                "-U",
                "intelliops",
                flag,
                "--no-owner",
                "intelliops",
            ],
            check=True,
            capture_output=True,
        ).stdout
        Path(OUT / f"postgres.{name}.sql").write_bytes(sql)
    http(f"{DEMO}/fix", {})
    summary = {"scenarios": scenarios, "stream_entries": counts}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
