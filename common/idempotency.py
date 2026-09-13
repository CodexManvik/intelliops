"""Idempotency guards — make at-least-once redelivery safe to replay (issue #53).

Once the bus stops acking before the handler runs, a crash mid-handler means the
event comes back. That is the point — but only if the handler can tell a repeat
from a first sighting. A guard answers exactly that, keyed on the envelope's
event id (`common/envelope.publish_model`) or on a domain key such as a
situation id.

Three implementations, selected by `bus_idempotency_mode` and defaulting to the
inert one so nothing changes until an operator opts in:

- `NullGuard` ("off", the default) — every claim succeeds. The call sites exist
  but do nothing, exactly as before this module.
- `MemoryGuard` ("memory") — a bounded, thread-safe LRU. Honest limits: it is
  process-local and lost on restart, so it de-duplicates a handler-raise retry
  but NOT the crash-and-restart case #53 exists for. It is the fallback when
  there is no Redis client to share (e.g. `BUS_BACKEND=kafka`).
- `RedisGuard` ("redis") — `SET NX EX` against the client the bus already holds.
  Survives a process restart, self-prunes via the TTL.

Deliberately NOT wired through `common/stores.py`: this is a bus concern, and
coupling it to `STORE_BACKEND` would force read-service to grow a database it
does not otherwise need.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from typing import Protocol

logger = logging.getLogger(__name__)


class IdempotencyGuard(Protocol):
    """Deliberately not in common/interfaces.py: that module's protocols are
    `runtime_checkable` and asserted against in the test suite, so adding one
    there has a blast radius this does not need."""

    def claim(self, key: str) -> bool:
        """True if this caller is the FIRST to claim `key`; False if it is a repeat."""
        ...

    def state(self, key: str) -> str | None: ...

    def set_state(self, key: str, value: str) -> None: ...


class NullGuard:
    """The default. Present so call sites need no conditionals; does nothing."""

    def claim(self, key: str) -> bool:
        return True

    def state(self, key: str) -> str | None:
        return None

    def set_state(self, key: str, value: str) -> None:
        return None


class MemoryGuard:
    """Process-local bounded LRU. Lost on restart — see the module docstring."""

    def __init__(self, max_keys: int = 50_000) -> None:
        self._max = max_keys
        self._seen: OrderedDict[str, str] = OrderedDict()
        self._lock = threading.Lock()

    def _touch(self, key: str, value: str) -> None:
        self._seen[key] = value
        self._seen.move_to_end(key)
        while len(self._seen) > self._max:
            self._seen.popitem(last=False)

    def claim(self, key: str) -> bool:
        with self._lock:
            if key in self._seen:
                return False
            self._touch(key, "1")
            return True

    def state(self, key: str) -> str | None:
        with self._lock:
            return self._seen.get(key)

    def set_state(self, key: str, value: str) -> None:
        with self._lock:
            self._touch(key, value)


class RedisGuard:
    """Shared, restart-surviving guard built on the bus's own Redis client."""

    def __init__(self, client, ttl_seconds: int = 86_400) -> None:
        self._r = client
        self._ttl = ttl_seconds

    @staticmethod
    def _k(key: str) -> str:
        return f"idem:{key}"

    def claim(self, key: str) -> bool:
        # SET NX EX is atomic, so two consumers racing the same event cannot both win.
        return bool(self._r.set(self._k(key), "1", nx=True, ex=self._ttl))

    def state(self, key: str) -> str | None:
        return self._r.get(self._k(key))

    def set_state(self, key: str, value: str) -> None:
        self._r.set(self._k(key), value, ex=self._ttl)


def make_guard(settings, bus=None) -> IdempotencyGuard:
    """Select a guard from settings, reusing the bus's Redis client when asked."""
    mode = getattr(settings, "bus_idempotency_mode", "off")
    ttl = getattr(settings, "bus_idempotency_ttl_seconds", 86_400)
    if mode == "redis":
        client = getattr(bus, "_r", None)
        if client is None:
            # The BUS_BACKEND=kafka path: no Redis client to share. Degrade loudly
            # rather than silently pretending to be durable.
            logger.warning(
                "bus_idempotency_mode=redis but the bus has no Redis client; "
                "falling back to MemoryGuard (process-local, lost on restart)"
            )
            return MemoryGuard()
        return RedisGuard(client, ttl_seconds=ttl)
    if mode == "memory":
        return MemoryGuard()
    return NullGuard()
