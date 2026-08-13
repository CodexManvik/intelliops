"""Static role→permission RBAC policy.

Actors map to roles; roles map to allowed (action, resource-glob) rules. A
check passes when any rule of any of the actor's roles matches the action
exactly and the resource by fnmatch glob. Default deny (see ADR-003).
"""

from __future__ import annotations

from fnmatch import fnmatch

import yaml


class RbacPolicy:
    def __init__(self, roles: dict, actors: dict) -> None:
        self._roles = roles
        self._actors = actors

    @classmethod
    def from_file(cls, path: str) -> RbacPolicy:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return cls(roles=data.get("roles", {}), actors=data.get("actors", {}))

    def check(self, actor: str, action: str, resource: str) -> bool:
        for role in self._actors.get(actor, []):
            for rule in self._roles.get(role, []):
                if rule.get("action") == action and fnmatch(resource, rule.get("resource", "")):
                    return True
        return False
