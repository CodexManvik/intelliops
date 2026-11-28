"""Language-agnostic performance harness for the compose stack.

Run it against the Python stack and again, unchanged, against the Rust one:

    python rust/harness/measure.py --label python
    python rust/harness/measure.py --label rust-ingestion         --compose-file deploy/docker-compose.rust.yml

It only talks to Docker, Redis (via `docker compose exec`) and the services' HTTP
ports, so nothing here depends on how a service is implemented. Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ["docker", "compose", "-f", str(ROOT / "deploy" / "docker-compose.yml")]
SERVICES = {
    "ingestion": 8001,
    "correlation": 8002,
    "rca": 8003,
    "action": 8004,
    "governance": 8005,
    "feedback": 8006,
    "read": 8007,
}
BATCHES = 100
BATCH_SIZE = 200
RESTARTS = 3


def run(cmd: list[str]) -> str:
    # utf-8 explicitly: the Windows default (cp1252) cannot decode the streams.
    done = subprocess.run(cmd, check=True, capture_output=True, text=True, encoding="utf-8")
    return done.stdout.strip()


def http(url: str, body: dict | None = None, timeout: float = 30.0) -> bytes:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def wait_ready(port: int, limit: float = 300.0) -> None:
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        try:
            http(f"http://127.0.0.1:{port}/ready", timeout=2.0)
            return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"port {port} never became ready")


def image_sizes() -> dict[str, float]:
    out = {}
    for service in SERVICES:
        cid = run([*COMPOSE, "ps", "-q", service])
        image = run(["docker", "inspect", "-f", "{{.Image}}", cid])
        size = int(run(["docker", "image", "inspect", "-f", "{{.Size}}", image]))
        out[service] = round(size / 1024 / 1024, 1)
    return out


def memory_mib() -> dict[str, float]:
    ids = {run([*COMPOSE, "ps", "-q", s])[:12]: s for s in SERVICES}
    raw = run(["docker", "stats", "--no-stream", "--format", "{{.ID}} {{.MemUsage}}", *ids])
    units = {"KiB": 1 / 1024, "MiB": 1.0, "GiB": 1024.0}
    out = {}
    for line in raw.splitlines():
        cid, used = line.split()[0], line.split()[1]
        unit = used[-3:]
        out[ids[cid[:12]]] = round(float(used[:-3]) * units[unit], 1)
    return out


def cold_start_seconds() -> dict[str, float]:
    out = {}
    for service, port in SERVICES.items():
        samples = []
        for _ in range(RESTARTS):
            run([*COMPOSE, "stop", service])
            start = time.monotonic()
            run([*COMPOSE, "start", service])
            wait_ready(port)
            samples.append(time.monotonic() - start)
        out[service] = round(sorted(samples)[len(samples) // 2], 2)
    return out


def correlation_lag() -> int | None:
    raw = run(
        [*COMPOSE, "exec", "-T", "redis", "redis-cli", "--json", "XINFO", "GROUPS", "telemetry.raw"]
    )
    for group in json.loads(raw):
        if isinstance(group, list):
            group = dict(zip(group[::2], group[1::2], strict=True))
        if group["name"].startswith("correlation"):
            return group["lag"] + group["pending"]
    return None


def ingest_throughput() -> dict[str, float]:
    """Flood /ingest, then time how long correlation takes to drain the stream.

    Values are flat so no incident is raised: this measures the hot path
    (parse, fingerprint, publish, consume, score), not remediation.
    """
    ts = datetime.now(UTC).isoformat()
    events = [
        {
            "source": "bench",
            "kind": "metric",
            "name": f"bench_metric_{i % 20}",
            "value": 1.0,
            "labels": {"service": f"svc-{i % 10}"},
            "ts": ts,
        }
        for i in range(BATCH_SIZE)
    ]
    latencies = []
    start = time.monotonic()
    for _ in range(BATCHES):
        t0 = time.monotonic()
        http("http://127.0.0.1:8001/ingest", {"events": events})
        latencies.append((time.monotonic() - t0) * 1000)
    accepted = time.monotonic() - start
    while (lag := correlation_lag()) is None or lag > 0:
        time.sleep(0.2)
        if time.monotonic() - start > 600:
            raise TimeoutError("correlation never drained telemetry.raw")
    drained = time.monotonic() - start
    total = BATCHES * BATCH_SIZE
    latencies.sort()
    return {
        "events": total,
        "ingest_events_per_s": round(total / accepted),
        "end_to_end_events_per_s": round(total / drained),
        "ingest_batch_p50_ms": round(latencies[len(latencies) // 2], 1),
        "ingest_batch_p99_ms": round(latencies[int(len(latencies) * 0.99) - 1], 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True, help="e.g. python or rust")
    parser.add_argument("--compose-file", action="append", default=[], help="extra compose file")
    args = parser.parse_args()
    for extra in args.compose_file:
        COMPOSE.extend(["-f", str(ROOT / extra)])
    for port in SERVICES.values():
        wait_ready(port)
    result = {
        "label": args.label,
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "host": json.loads(run(["docker", "info", "--format", "{{json .NCPU}}"])),
        "image_mib": image_sizes(),
        "idle_memory_mib": memory_mib(),
        "ingest": ingest_throughput(),
        "loaded_memory_mib": memory_mib(),
        "cold_start_s": cold_start_seconds(),
    }
    out = ROOT / "rust" / "baselines" / f"{args.label}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
