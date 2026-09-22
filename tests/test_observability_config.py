"""The Grafana provisioning files must agree with each other.

A dashboard panel that names a datasource uid nobody provisions renders as
"datasource not found", and nothing else would catch it before someone opens
the page.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

OBS = Path("deploy/k8s/platform/files/observability")


def _uids(node) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        ds = node.get("datasource")
        if isinstance(ds, dict) and "uid" in ds:
            found.add(ds["uid"])
        for v in node.values():
            found |= _uids(v)
    elif isinstance(node, list):
        for v in node:
            found |= _uids(v)
    return found


def test_dashboard_only_uses_provisioned_datasources():
    dashboard = json.loads((OBS / "intelliops-dashboard.json").read_text(encoding="utf-8"))
    provisioned = {
        d["uid"]
        for d in yaml.safe_load((OBS / "grafana-datasources.yaml").read_text(encoding="utf-8"))[
            "datasources"
        ]
    }
    used = _uids(dashboard)
    assert used, "dashboard references no datasources"
    assert used <= provisioned, used - provisioned


def test_every_file_the_chart_reads_exists():
    template = Path("deploy/k8s/platform/templates/observability.yaml").read_text(encoding="utf-8")
    for name in (
        "loki.yaml",
        "alloy-kubernetes.alloy",
        "grafana-datasources.yaml",
        "grafana-dashboards.yaml",
        "intelliops-dashboard.json",
    ):
        assert f"files/observability/{name}" in template
        assert (OBS / name).is_file(), name
