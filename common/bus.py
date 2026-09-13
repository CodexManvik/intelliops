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


_DRAIN_BATCH = 100


class RedisBus:
    def __init__(
        self,
        client: redis.Redis,
        consumer_name: str = "c1",
        stream_maxlen: int = 0,
        delivery: str = "at_most_once",
        dlq_mode: str = "off",
        dlq_suffix: str = ".dlq",
        max_delivery_attempts: int = 5,
        idempotency_ttl_seconds: int = 86_400,
    ) -> None:
        self._r = client
        self._consumer = consumer_name
        # See consume() for what each of these changes.
        self._delivery = delivery
        self._dlq_mode = dlq_mode
        self._dlq_suffix = dlq_suffix
        self._max_delivery_attempts = max_delivery_attempts
        self._idempotency_ttl = idempotency_ttl_seconds
        # Approximate per-stream cap (issue #54): 0 = unbounded (test/back-compat),
        # >0 trims each stream to ~stream_maxlen entries on publish.
        self._stream_maxlen = stream_maxlen

    def publish(self, topic: str, message: dict) -> None:
        if self._stream_maxlen > 0:
            # approximate=True → MAXLEN ~ N: cheap amortized trimming, real length
            # may sit slightly above N. Consumer groups + at-least-once redelivery
            # are unaffected — trimming only drops already-old entries.
            self._r.xadd(topic, message, maxlen=self._stream_maxlen, approximate=True)
        else:
            self._r.xadd(topic, message)

    def _attempts_key(self, group: str, topic: str, entry_id: str) -> str:
        return f"bus:attempts:{group}:{topic}:{entry_id}"

    def send_to_dlq(self, topic: str, fields: dict, reason: str, group: str) -> None:
        """Park an entry on `{topic}{suffix}` with why it was parked."""
        self.publish(
            topic + self._dlq_suffix,
            {**fields, "_dlq_reason": reason, "_dlq_topic": topic, "_dlq_group": group},
        )

    def _entries(self, topic: str, group: str, drain_first: bool) -> Iterator[tuple[str, dict]]:
        """Yield (entry_id, fields) forever, owning group creation and reconnects.

        Resilient: a Redis ConnectionError/TimeoutError — Redis not yet up at
        startup, or a mid-run blip — must NOT kill the consumer thread (that
        silently stops the service processing the stream). Catch it, back off,
        retry the whole read.

        When `drain_first`, one pass over this consumer's OWN pending entries
        (start id "0") runs before tailing with ">", which never returns pending
        entries. That pass is what actually recovers an un-acked entry after a
        crash — deferring the ack alone would make events durable but
        unreachable. It repeats after a reconnect, since the PEL survives.
        """
        group_ready = False
        drained = not drain_first
        while True:
            try:
                if not group_ready:
                    try:
                        self._r.xgroup_create(topic, group, id="0", mkstream=True)
                    except redis.ResponseError as exc:  # group already exists
                        if "BUSYGROUP" not in str(exc):
                            raise
                    group_ready = True
                if not drained:
                    resp = self._r.xreadgroup(
                        group, self._consumer, {topic: "0"}, count=_DRAIN_BATCH
                    )
                    drained = True
                    for _stream, entries in resp or []:
                        for entry_id, fields in entries:
                            yield entry_id, fields
                    continue
                resp = self._r.xreadgroup(group, self._consumer, {topic: ">"}, count=1, block=1000)
                if not resp:
                    continue
                for _stream, entries in resp:
                    for entry_id, fields in entries:
                        yield entry_id, fields
            except (redis.ConnectionError, redis.TimeoutError) as exc:
                logger.warning(
                    "bus consume on %s lost Redis (%s); retrying in %ss",
                    topic,
                    exc.__class__.__name__,
                    _RECONNECT_BACKOFF_SECONDS,
                )
                group_ready = False  # re-ensure the group after reconnect
                drained = not drain_first  # the PEL survived; re-drain it
                time.sleep(_RECONNECT_BACKOFF_SECONDS)

    def consume(self, topic: str, group: str) -> Iterator[dict]:
        """Yield each entry's fields.

        DELIVERY SEMANTICS (issue #53), selected by `bus_delivery`:

        - "at_most_once" (default, unchanged): XACK fires BEFORE the entry is
          yielded, so a crash / validation error / DB failure in the handler
          loses that event permanently.
        - "at_least_once": the ack is deferred until the caller comes BACK for
          the next entry. Being resumed is the only proof the handler finished
          — if it raises, or breaks on stop_event, or abandons the generator,
          the entry stays pending and the self-drain re-serves it on reconnect.

        Note the ack is NOT in a finally:/GeneratorExit handler, deliberately:
        an abandoned in-flight entry must stay pending. The flip side is that
        this relies on the caller being resumed, so on a runtime that defers
        generator finalization the single in-flight entry degrades to
        at-most-once — no worse than the default. See ADR-033.
        """
        at_least_once = self._delivery == "at_least_once"
        dlq_on = self._dlq_mode == "on"
        pending_ack: str | None = None
        for entry_id, fields in self._entries(topic, group, drain_first=at_least_once):
            # ACK POINT: we were resumed, so the previously yielded entry was
            # handled. Inert under at_most_once, where pending_ack is never set.
            if pending_ack is not None:
                self._r.xack(topic, group, pending_ack)
                if dlq_on:
                    self._r.delete(self._attempts_key(group, topic, pending_ack))
                pending_ack = None
            if not at_least_once:
                self._r.xack(topic, group, entry_id)
                yield fields
                continue
            if dlq_on:
                # A payload this build cannot handle would otherwise be redelivered
                # forever. Park it and move on rather than wedge the consumer.
                key = self._attempts_key(group, topic, entry_id)
                attempts = int(self._r.incr(key))
                self._r.expire(key, self._idempotency_ttl)
                if attempts > self._max_delivery_attempts:
                    logger.warning(
                        "bus: entry %s on %s exceeded %s delivery attempts; sending to DLQ",
                        entry_id,
                        topic,
                        self._max_delivery_attempts,
                    )
                    self.send_to_dlq(topic, fields, "max-delivery-attempts", group)
                    self._r.xack(topic, group, entry_id)
                    self._r.delete(key)
                    continue
            pending_ack = entry_id
            yield fields

    def ping(self) -> None:
        """Raise if the bus backend is unreachable (readiness probe uses this)."""
        self._r.ping()  # redis-py returns True; we discard it. Exceptions propagate.


class KafkaBus:
    """Kafka implementation of the BusClient protocol using kafka-python.

    Imports are deferred (lazy) inside each method so that kafka-python is
    only imported when a Kafka bus is actually instantiated — satisfying
    Requirement 4.7.

    Delivery semantics follow `bus_delivery`, matching RedisBus:
    at_most_once (default) uses enable_auto_commit=True, so offsets are
    committed as soon as each record is read, before the caller finishes
    processing (Requirement 4.5). at_least_once turns auto-commit off and
    commits only when the caller comes back for the next record.
    """

    def __init__(
        self,
        bootstrap_servers: str,
        consumer_name: str = "c1",
        delivery: str = "at_most_once",
    ) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._consumer = consumer_name
        self._delivery = delivery
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

        at_least_once = self._delivery == "at_least_once"
        consumer = KafkaConsumer(
            topic,
            bootstrap_servers=self._bootstrap_servers,
            group_id=group,
            client_id=self._consumer,
            auto_offset_reset="earliest",
            enable_auto_commit=not at_least_once,
            value_deserializer=lambda b: json.loads(b.decode()),
        )
        committed = True
        for record in consumer:
            # Same resume-point rule as RedisBus: being asked for the next
            # record is the only proof the previous one was handled.
            if not committed:
                consumer.commit()
                committed = True
            if at_least_once:
                committed = False
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
            stream_maxlen=settings.bus_stream_maxlen,
            delivery=settings.bus_delivery,
            dlq_mode=settings.bus_dlq_mode,
            dlq_suffix=settings.bus_dlq_topic_suffix,
            max_delivery_attempts=settings.bus_max_delivery_attempts,
            idempotency_ttl_seconds=settings.bus_idempotency_ttl_seconds,
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
