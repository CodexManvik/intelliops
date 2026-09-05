"""Meridian gateway service: the entry point for financial submissions/reports.

Sample target for IntelliOps — see services/meridian/common.py for the
shared fault mechanism (/admin/fault, /admin/clear, /metrics).

Task-1 scope was domain routes + the shared service scaffold only. Task 3
adds the ops-proxy (server-side fault/clear calls to the other 3 Meridian
services), /admin/deploy (writes the rollback-scenario deploy marker into
the rca-context volume shared with the rca service), and the StaticFiles
mount that serves the UI built in Task 4.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import HTTPException
from fastapi.staticfiles import StaticFiles

from common.config import get_settings
from services.meridian.common import make_meridian_service
from services.meridian.gateway.ops_target import ops_target_url

# The 4 Meridian services, reachable in-compose by their service name. The
# ops-proxy and /admin/deploy both key off this short name (not the full
# "meridian-<svc>" container name) so the UI/CLI caller doesn't need to know
# the compose naming convention.
_MERIDIAN_SERVICES = {"gateway", "validation", "aggregation", "reporting"}

# The full pinned USE+RED metric set (Metrics Phase 1 Task 1). `ops_metrics`
# below scrapes exactly these names and folds each into its service's row
# generically; any other __name__ in the Prometheus result is ignored
# (fail-soft against future/unknown series).
_KNOWN_METRIC_NAMES = {
    "cpu_usage",
    "meridian_error_rate",
    "request_rate",
    "latency_p50_ms",
    "latency_p99_ms",
    "memory_usage_mb",
    "saturation",
    "queue_depth",
    "db_pool_in_use",
    "db_pool_max",
    "disk_usage_percent",
}

# Where /admin/deploy writes deploys.json. Defaults to a repo-relative path
# for local/test runs; the compose gateway service overrides this to the
# mounted rca-context volume path so the rca service (Task 2) can read the
# same deploy markers for the rollback RCA scenario.
_RCA_CONTEXT = os.environ.get("INTELLIOPS_RCA_CONTEXT_PATH", "data/rca_context")


def _known_service(name: str) -> str:
    """Reject any service the ops-proxy doesn't recognize.

    The proxy interpolates the service name into an in-cluster URL
    (http://meridian-<svc>:8000/...), so an unvalidated value is a
    request-forgery seam. Only the four Meridian services are ever a
    legitimate ops target; anything else is a 400, never a URL.
    """
    if name not in _MERIDIAN_SERVICES:
        raise HTTPException(status_code=400, detail=f"Unknown Meridian service: {name!r}")
    return name


def _routes(app, state) -> None:
    @app.post("/api/submissions")
    def submit(payload: dict) -> dict:
        # Simulate accepting a financial submission; a real endpoint so
        # traffic actually flows through the service (and can be
        # latency-injected by a fault).
        client = payload.get("client", "")
        period = payload.get("period", "")
        amount = payload.get("amount", 0.0)
        return {"accepted": True, "client": client, "period": period, "amount": amount}

    @app.get("/api/reports")
    def reports() -> dict:
        # Simulate listing reports; a real endpoint so traffic actually
        # flows through the service.
        return {"reports": []}

    # --- Ops proxy -----------------------------------------------------
    #
    # The Meridian UI (Task 4) drives fault injection and deploy markers
    # through these routes rather than calling the 4 backend services
    # directly, so the browser never needs to hold the demo's admin token
    # and every ops call stays same-origin with the gateway.

    @app.post("/api/ops/fault")
    def ops_fault(body: dict) -> dict:
        svc = _known_service(body["service"])
        spec = body["spec"]
        url = ops_target_url(svc, "admin/fault", get_settings().meridian_ops_target_mode)
        with httpx.Client(timeout=5.0) as c:
            # demo-app-style targets are un-tokenized; server-side call.
            r = c.post(url, json=spec)
        return {"status": r.status_code}

    @app.post("/api/ops/clear")
    def ops_clear(body: dict) -> dict:
        svc = _known_service(body["service"])
        url = ops_target_url(svc, "admin/clear", get_settings().meridian_ops_target_mode)
        with httpx.Client(timeout=5.0) as c:
            r = c.post(url)
        return {"status": r.status_code}

    @app.post("/api/ops/deploy")
    def ops_deploy(body: dict) -> dict:
        svc = f"meridian-{_known_service(body['service'])}"
        os.makedirs(_RCA_CONTEXT, exist_ok=True)
        path = os.path.join(_RCA_CONTEXT, "deploys.json")
        entry = {
            "service": svc,
            "version": body.get("version", "v2.3.1"),
            "ts": datetime.now(UTC).isoformat(),
        }
        with open(path, "w") as f:
            json.dump([entry], f)
        return {"deployed": svc}

    @app.get("/api/ops/metrics")
    def ops_metrics() -> dict:
        """Live per-service telemetry, proxied from Prometheus server-side so the
        UI stays same-origin (no CORS) and never holds the Prometheus URL.
        Fail-soft: any Prometheus error yields an empty payload, never a 5xx."""
        prom = get_settings().prometheus_url.rstrip("/")
        # Metrics Phase 1 Task 4: broadened from cpu_usage|meridian_error_rate
        # to the full pinned USE+RED metric set (Task 1) so the ops panel can
        # surface every metric a fault scenario (Task 2) might move.
        query = (
            '{__name__=~"cpu_usage|meridian_error_rate|request_rate|'
            "latency_p50_ms|latency_p99_ms|memory_usage_mb|saturation|"
            'queue_depth|db_pool_in_use|db_pool_max|disk_usage_percent"}'
        )
        try:
            with httpx.Client(timeout=5.0) as c:
                resp = c.get(f"{prom}/api/v1/query", params={"query": query})
        except httpx.HTTPError:
            return {"scraped": False, "services": []}
        if resp.status_code != 200:
            return {"scraped": False, "services": []}
        try:
            body = resp.json()
        except ValueError:
            return {"scraped": False, "services": []}
        if not isinstance(body, dict) or body.get("status") != "success":
            return {"scraped": False, "services": []}
        # Fold the flat result vector into per-service rows. Every known
        # metric name is carried generically (row[name] = val) so the ops
        # panel gets the full USE+RED set, not just cpu/error; cpu_usage and
        # meridian_error_rate are additionally mirrored onto the legacy
        # cpu_usage/error_rate keys the `healthy` heuristic below reads.
        # Unknown metric names are silently ignored (fail-soft).
        by_service: dict[str, dict] = {}
        for entry in body.get("data", {}).get("result", []):
            metric = entry.get("metric", {})
            svc = metric.get("service")
            name = metric.get("__name__")
            value_pair = entry.get("value", [0, "0"])
            if not svc or not isinstance(value_pair, list) or len(value_pair) < 2:
                continue
            try:
                val = float(value_pair[1])
            except (TypeError, ValueError):
                continue
            if name not in _KNOWN_METRIC_NAMES:
                continue
            row = by_service.setdefault(
                svc, {"service": svc, "cpu_usage": None, "error_rate": None}
            )
            row[name] = val
            if name == "cpu_usage":
                row["cpu_usage"] = val
            elif name == "meridian_error_rate":
                row["error_rate"] = val
        services = []
        for row in by_service.values():
            cpu = row["cpu_usage"]
            err = row["error_rate"]
            row["healthy"] = (cpu is None or cpu < 50) and (err is None or err < 0.1)
            services.append(row)
        services.sort(key=lambda r: r["service"])
        return {"scraped": bool(services), "services": services}


app = make_meridian_service("meridian-gateway", _routes)

# --- StaticFiles UI mount ----------------------------------------------
#
# MUST be the last thing registered on the app: StaticFiles(html=True) is a
# catch-all mount at "/", so every /api, /admin, /metrics, /health route
# above needs to be registered first or the mount would shadow them. Guarded
# on .exists() because ui/dist is only produced by the Task-4 `npm run
# build` step — the gateway must still start (and pass tests/CI) before
# that directory exists.
_ui = Path(__file__).parent.parent / "ui" / "dist"
if _ui.exists():
    app.mount("/", StaticFiles(directory=str(_ui), html=True), name="ui")
