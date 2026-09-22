"""Generate the provisioned Grafana dashboard (logs + metrics + audit trail).

Run from the repo root:

    uv run python scripts/make-grafana-dashboard.py

Writes deploy/k8s/platform/files/observability/intelliops-dashboard.json, which
both docker-compose (bind mount) and the Helm chart (.Files.Get) provision.
Generated rather than hand-edited so the JSON stays valid and reviewable here.
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path("deploy/k8s/platform/files/observability/intelliops-dashboard.json")

LOKI = {"type": "loki", "uid": "loki"}
PROM = {"type": "prometheus", "uid": "prometheus"}
PG = {"type": "grafana-postgresql-datasource", "uid": "intelliops-postgres"}

SVC = '{service=~"$service"}'


def _grid(x, y, w, h):
    return {"x": x, "y": y, "w": w, "h": h}


def row(title, y, pid):
    return {
        "type": "row",
        "title": title,
        "id": pid,
        "gridPos": _grid(0, y, 24, 1),
        "collapsed": False,
    }


def timeseries(title, pid, grid, ds, targets, unit=None, stack=False, desc=""):
    defaults = {"custom": {"lineWidth": 1, "fillOpacity": 18 if stack else 6}}
    if stack:
        defaults["custom"]["stacking"] = {"mode": "normal"}
    if unit:
        defaults["unit"] = unit
    return {
        "type": "timeseries",
        "title": title,
        "description": desc,
        "id": pid,
        "gridPos": grid,
        "datasource": ds,
        "targets": targets,
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {
            "legend": {"displayMode": "list", "placement": "bottom"},
            "tooltip": {"mode": "multi"},
        },
    }


def loki_expr(expr, legend="{{service}}", ref="A"):
    return {
        "datasource": LOKI,
        "refId": ref,
        "expr": expr,
        "legendFormat": legend,
        "queryType": "range",
    }


def prom_expr(expr, legend="{{service}}", ref="A"):
    return {"datasource": PROM, "refId": ref, "expr": expr, "legendFormat": legend}


panels = [
    row("Logs", 0, 100),
    timeseries(
        "Log lines per minute, by service",
        1,
        _grid(0, 1, 12, 7),
        LOKI,
        [loki_expr(f"sum by (service) (count_over_time({SVC} [1m]))")],
        stack=True,
    ),
    timeseries(
        "Errors and warnings per minute, by service",
        2,
        _grid(12, 1, 12, 7),
        LOKI,
        [
            loki_expr(
                'sum by (service, level) (count_over_time({service=~"$service", '
                'level=~"ERROR|WARNING|CRITICAL"} [1m]))',
                legend="{{service}} {{level}}",
            )
        ],
        stack=True,
        desc="Counted from the JSON `level` field. Services that log plain text (redis, "
        "postgres, uvicorn access lines) carry no level and do not appear here.",
    ),
    {
        "type": "logs",
        "title": "Service logs",
        "description": "Every container's stdout/stderr. Filter by service above; the search box "
        "is a case-insensitive regex over the line.",
        "id": 3,
        "gridPos": _grid(0, 8, 24, 14),
        "datasource": LOKI,
        "targets": [
            {
                "datasource": LOKI,
                "refId": "A",
                "expr": SVC + ' |~ "(?i)$search"',
                "queryType": "range",
            }
        ],
        "options": {
            "showTime": True,
            "showLabels": False,
            "showCommonLabels": False,
            "wrapLogMessage": True,
            "prettifyLogMessage": False,
            "enableLogDetails": True,
            "dedupStrategy": "none",
            "sortOrder": "Descending",
        },
    },
    row("Metrics (Prometheus)", 22, 101),
    timeseries(
        "CPU usage",
        4,
        _grid(0, 23, 12, 7),
        PROM,
        [prom_expr("cpu_usage")],
        unit="percent",
    ),
    timeseries(
        "Latency p99",
        5,
        _grid(12, 23, 12, 7),
        PROM,
        [prom_expr("latency_p99_ms")],
        unit="ms",
    ),
    timeseries(
        "Error rate",
        6,
        _grid(0, 30, 12, 7),
        PROM,
        [prom_expr("meridian_error_rate")],
        unit="percentunit",
    ),
    timeseries(
        "Service up",
        7,
        _grid(12, 30, 12, 7),
        PROM,
        [prom_expr("service_up")],
        desc="1 = serving, 0 = down. Scraped from each Meridian service and the demo app.",
    ),
    row("Governance (Postgres)", 37, 102),
    {
        "type": "table",
        "title": "Audit trail",
        "description": "The durable audit log (audit_records): every diagnosis, approval, "
        "remediation and graduation - the latest 500, whatever the time range. "
        "Empty when STORE_BACKEND=file.",
        "id": 8,
        "gridPos": _grid(0, 38, 24, 12),
        "datasource": PG,
        "targets": [
            {
                "datasource": PG,
                "refId": "A",
                "format": "table",
                "rawQuery": True,
                "editorMode": "code",
                # Deliberately not bound to the time picker: an audit log is read
                # as "what happened most recently", however long ago that was.
                "rawSql": 'SELECT ts AS "time", actor, action, resource, decision, '
                "correlation_id FROM audit_records ORDER BY ts DESC LIMIT 500",
            }
        ],
        "options": {"showHeader": True, "cellHeight": "sm"},
        "fieldConfig": {"defaults": {}, "overrides": []},
    },
]

dashboard = {
    "uid": "intelliops-overview",
    "title": "IntelliOps — Logs, metrics and audit",
    "tags": ["intelliops"],
    "timezone": "browser",
    "schemaVersion": 39,
    "version": 1,
    "editable": True,
    "refresh": "10s",
    "time": {"from": "now-30m", "to": "now"},
    "templating": {
        "list": [
            {
                "name": "service",
                "label": "Service",
                "type": "query",
                "datasource": LOKI,
                "query": {
                    "label": "service",
                    "refId": "LokiVariableQueryEditor-VariableQuery",
                    "stream": "",
                    "type": 1,
                },
                "refresh": 2,
                "includeAll": True,
                "multi": True,
                "allValue": ".+",
                "current": {"selected": True, "text": ["All"], "value": ["$__all"]},
                "sort": 1,
            },
            {
                "name": "search",
                "label": "Search logs",
                "type": "textbox",
                "query": "",
                "current": {"text": "", "value": ""},
            },
        ]
    },
    "annotations": {"list": []},
    "panels": panels,
}

OUT.write_text(json.dumps(dashboard, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"wrote {OUT}")
