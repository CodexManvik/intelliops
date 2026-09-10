"""SystemContextProvider: reads the curated, system-agnostic description of the
target system the author drafts for. Content is filled in per target; a missing,
empty, or all-placeholder file is valid and reads as 'unconfigured' (the agent
then drafts from incident + experience alone). Never raises."""

from __future__ import annotations

import logging

import yaml

logger = logging.getLogger("intelliops.governance.system_context")


class SystemContextProvider:
    def __init__(self, path: str) -> None:
        self._path = path

    def load(self) -> dict | None:
        try:
            with open(self._path) as f:
                data = yaml.safe_load(f)
        except (OSError, yaml.YAMLError):
            logger.info("system context unreadable at %s; treating as unconfigured", self._path)
            return None
        if not isinstance(data, dict):
            return None
        name = (data.get("system") or {}).get("name") or ""
        services = data.get("services") or []
        if not name and not services:
            return None  # all-placeholder
        return data

    def summarize(self) -> str:
        data = self.load()
        if data is None:
            return "System context: unconfigured (no target system described yet)."
        sys_ = data.get("system", {})
        lines = [f"System: {sys_.get('name', '?')} — {sys_.get('summary', '')}".strip()]
        for svc in data.get("services", []):
            deps = ", ".join(svc.get("depends_on", [])) or "none"
            mets = ", ".join(svc.get("key_metrics", [])) or "none"
            lines.append(
                f"- {svc.get('name', '?')}: {svc.get('role', '')}; depends on {deps}; metrics {mets}"
            )
        actions = data.get("actions") or {}
        if actions:
            lines.append("Action notes: " + "; ".join(f"{k}={v}" for k, v in actions.items() if v))
        if data.get("notes"):
            lines.append(f"Notes: {data['notes']}")
        return "\n".join(lines)
