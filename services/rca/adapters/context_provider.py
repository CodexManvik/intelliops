"""ContextProvider implementations for RCA enrichment.

NullContextProvider is the safe default and test double. FileContextProvider
reads static JSON (deploys/topology/config) — a stand-in for real Prometheus /
CMDB / git integrations, which slot in behind the same protocol later.
"""

from __future__ import annotations

import json
import os


class NullContextProvider:
    def recent_deploys(self) -> list[dict]:
        return []

    def topology_for(self, labels: dict[str, str]) -> dict:
        return {}

    def config_changes(self) -> list[dict]:
        return []


class FileContextProvider:
    def __init__(self, path: str) -> None:
        self._path = path

    def _read(self, filename: str, default):
        full = os.path.join(self._path, filename)
        if not os.path.exists(full):
            return default
        with open(full, encoding="utf-8") as fh:
            return json.load(fh)

    def recent_deploys(self) -> list[dict]:
        return self._read("deploys.json", [])

    def topology_for(self, labels: dict[str, str]) -> dict:
        return self._read("topology.json", {})

    def config_changes(self) -> list[dict]:
        return self._read("config_changes.json", [])
