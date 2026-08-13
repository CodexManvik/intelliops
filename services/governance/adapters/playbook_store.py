"""PlaybookStore implementations: in-memory (tests) and YAML-file-backed.

The registry is the CoE's shared playbook catalog — standardized, not
reinvented per team. Postgres is a deferred adapter.
"""

from __future__ import annotations

import glob
import os

import yaml

from common.contracts import Playbook


def load_seed_playbooks(path: str) -> list[Playbook]:
    out: list[Playbook] = []
    for f in sorted(glob.glob(os.path.join(path, "*.yaml"))):
        with open(f, encoding="utf-8") as fh:
            out.append(Playbook.model_validate(yaml.safe_load(fh)))
    return out


class InMemoryPlaybookStore:
    def __init__(self) -> None:
        self._by_id: dict[str, Playbook] = {}

    def register(self, playbook: Playbook) -> None:
        self._by_id[playbook.id] = playbook

    def get(self, playbook_id: str) -> Playbook | None:
        return self._by_id.get(playbook_id)

    def list(self) -> list[Playbook]:
        return list(self._by_id.values())


class FilePlaybookStore:
    def __init__(self, path: str) -> None:
        self._path = path
        os.makedirs(path, exist_ok=True)
        self._by_id: dict[str, Playbook] = {
            p.id: p for p in load_seed_playbooks(path)
        }

    def register(self, playbook: Playbook) -> None:
        self._by_id[playbook.id] = playbook
        with open(os.path.join(self._path, f"{playbook.id}.yaml"), "w", encoding="utf-8") as fh:
            yaml.safe_dump(playbook.model_dump(mode="json"), fh)

    def get(self, playbook_id: str) -> Playbook | None:
        return self._by_id.get(playbook_id)

    def list(self) -> list[Playbook]:
        return list(self._by_id.values())
