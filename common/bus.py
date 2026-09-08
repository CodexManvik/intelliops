"""Event-bus client. Redis Streams is the dev binding of the BusClient protocol.

Consumer groups make delivery durable and load-balanced. `consume` blocks for
new entries and yields decoded field dicts. A `make_bus` factory lets services
stay unaware of the concrete implementation (see ADR-001, ADR-005).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator

import redis

from common.config import Settings

logger = logging.getLogger("intelliops.bus")

# How long to wait before retrying after a transient Redis connection error in a
# consume loop. Short enough to recover quickly at startup (Redis not yet up),
# long enough not to hot-spin.
_RECONNECT_BACKOFF_SECONDS = 2.0


class RedisBus:
    def __init__(self, client: redis.Redis, consumer_name: str = "c1") -> None:
        self._r = client
        self._consumer = consumer_name

    def publish(self, topic: str, message: dict) -> None:
        self._r.xadd(topic, message)

    def consume(self, topic: str, group: str) -> Iterator[dict]:
        # Resilient consume: a Redis ConnectionError/TimeoutError — Redis not yet
        # up at startup, or a mid-run blip — must NOT kill the consumer thread
        # (that silently stops the service processing the stream). Catch it,
        # back off, and retry the whole read; the consumer group + at-least-once
        # semantics mean no delivered-but-unacked entry is lost.
        group_ready = False
        while True:
            try:
                if not group_ready:
                    try:
                        self._r.xgroup_create(topic, group, id="0", mkstream=True)
                    except redis.ResponseError as exc:  # group already exists
                        if "BUSYGROUP" not in str(exc):
                            raise
                    group_ready = True
                resp = self._r.xreadgroup(group, self._consumer, {topic: ">"}, count=1, block=1000)
                if not resp:
                    continue
                for _stream, entries in resp:
                    for entry_id, fields in entries:
                        self._r.xack(topic, group, entry_id)
                        yield fields
            except (redis.ConnectionError, redis.TimeoutError) as exc:
                logger.warning(
                    "bus consume on %s lost Redis (%s); retrying in %ss",
                    topic,
                    exc.__class__.__name__,
                    _RECONNECT_BACKOFF_SECONDS,
                )
                group_ready = False  # re-ensure the group after reconnect
                time.sleep(_RECONNECT_BACKOFF_SECONDS)

    def ping(self) -> None:
        """Raise if the bus backend is unreachable (readiness probe uses this)."""
        self._r.ping()  # redis-py returns True; we discard it. Exceptions propagate.


class KafkaBus:
    """Kafka implementation of the BusClient protocol using kafka-python.

    Imports are deferred (lazy) inside each method so that kafka-python is
    only imported when a Kafka bus is actually instantiated — satisfying
    Requirement 4.7.

    Delivery semantics: at-most-once, matching RedisBus behaviour.
    enable_auto_commit=True means offsets are committed as soon as each
    record is read, before the caller finishes processing (Requirement 4.5).
    """

    def __init__(self, bootstrap_servers: str, consumer_name: str = "c1") -> None:
        self._bootstrap_servers = bootstrap_servers
        self._consumer = consumer_name
        self._producer = None

    def _get_producer(self):
        if self._producer is None:
            import json

            from kafka import KafkaProducer

            self._producer = KafkaProducer(
                bootstrap_servers=self._bootstrap_servers,
                value_serializer=lambda v: json.dumps(v).encode(),
            )
        return self._producer

    def publish(self, topic: str, message: dict) -> None:
        """JSON-serialize *message* and send it to *topic*, flushing before return."""
        producer = self._get_producer()
        producer.send(topic, value=message)
        producer.flush()

    def consume(self, topic: str, group: str) -> Iterator[dict]:
        """Subscribe to *topic* under *group* and yield deserialized message dicts."""
        import json

        from kafka import KafkaConsumer

        consumer = KafkaConsumer(
            topic,
            bootstrap_servers=self._bootstrap_servers,
            group_id=group,
            client_id=self._consumer,
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            value_deserializer=lambda b: json.loads(b.decode()),
        )
        for record in consumer:
            yield record.value

    def ping(self) -> None:
        """Check Kafka connectivity by creating a temporary admin client."""
        from kafka import KafkaAdminClient
        from kafka.errors import NoBrokersAvailable

        try:
            admin = KafkaAdminClient(bootstrap_servers=self._bootstrap_servers)
            admin.close()
        except NoBrokersAvailable as exc:
            raise ConnectionError(f"Kafka unavailable: {self._bootstrap_servers}") from exc


def make_bus(settings: Settings, consumer_name: str = "c1") -> RedisBus | KafkaBus:
    if settings.bus_backend == "redis":
        return RedisBus(
            # health_check_interval + retry_on_timeout let redis-py transparently
            # re-establish a dropped connection (e.g. Redis restart) instead of
            # surfacing a dead socket; the consume loop's own retry is the backstop.
            client=redis.from_url(
                settings.redis_url,
                decode_responses=True,
                health_check_interval=30,
                retry_on_timeout=True,
                socket_keepalive=True,
            ),
            consumer_name=consumer_name,
        )
    elif settings.bus_backend == "kafka":
        return KafkaBus(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            consumer_name=consumer_name,
        )
    else:
        raise ValueError(
            f"Unknown bus backend: {settings.bus_backend!r}. Expected 'redis' or 'kafka'."
        )
