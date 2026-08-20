"""PlaybookStore implementations: in-memory (tests) and YAML-file-backed.

The registry is the CoE's shared playbook catalog — standardized, not
reinvented per team. Postgres is a deferred adapter.
"""

from __future__ import annotations

import glob
import os
from datetime import UTC, datetime

import yaml
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from common.contracts import Playbook
from common.db import from_payload, playbooks, to_payload


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


class PostgresPlaybookStore:
    def __init__(self, engine, seed_path: str) -> None:
        self._engine = engine
        for pb in load_seed_playbooks(seed_path):
            self.register(pb)

    def register(self, playbook: Playbook) -> None:
        mode = playbook.hitl_mode.value if hasattr(playbook.hitl_mode, "value") else str(playbook.hitl_mode)
        values = {"id": playbook.id, "name": playbook.name, "hitl_mode": mode,
                  "reversible": playbook.reversible, "payload": to_payload(playbook),
                  "updated_at": datetime.now(UTC)}
        stmt = pg_insert(playbooks).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[playbooks.c.id],
            set_={"name": stmt.excluded.name, "hitl_mode": stmt.excluded.hitl_mode,
                  "reversible": stmt.excluded.reversible, "payload": stmt.excluded.payload,
                  "updated_at": stmt.excluded.updated_at})
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def get(self, playbook_id: str) -> Playbook | None:
        stmt = select(playbooks.c.payload).where(playbooks.c.id == playbook_id)
        with self._engine.connect() as conn:
            row = conn.execute(stmt).first()
        return from_payload(row.payload, Playbook) if row else None

    def list(self) -> list[Playbook]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(playbooks.c.payload).order_by(playbooks.c.id)).all()
        return [from_payload(r.payload, Playbook) for r in rows]
