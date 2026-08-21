"""Event-bus client. Redis Streams is the dev binding of the BusClient protocol.

Consumer groups make delivery durable and load-balanced. `consume` blocks for
new entries and yields decoded field dicts. A `make_bus` factory lets services
stay unaware of the concrete implementation (see ADR-001, ADR-005).
"""

from __future__ import annotations

from collections.abc import Iterator

import redis

from common.config import Settings


class RedisBus:
    def __init__(self, client: redis.Redis, consumer_name: str = "c1") -> None:
        self._r = client
        self._consumer = consumer_name

    def publish(self, topic: str, message: dict) -> None:
        self._r.xadd(topic, message)

    def consume(self, topic: str, group: str) -> Iterator[dict]:
        try:
            self._r.xgroup_create(topic, group, id="0", mkstream=True)
        except redis.ResponseError as exc:  # group already exists
            if "BUSYGROUP" not in str(exc):
                raise
        while True:
            resp = self._r.xreadgroup(group, self._consumer, {topic: ">"}, count=1, block=1000)
            if not resp:
                continue
            for _stream, entries in resp:
                for entry_id, fields in entries:
                    self._r.xack(topic, group, entry_id)
                    yield fields

    def ping(self) -> None:
        """Raise if the bus backend is unreachable (readiness probe uses this)."""
        self._r.ping()  # redis-py returns True; we discard it. Exceptions propagate.


def make_bus(settings: Settings, consumer_name: str = "c1") -> RedisBus:
    return RedisBus(
        client=redis.from_url(settings.redis_url, decode_responses=True),
        consumer_name=consumer_name,
    )
