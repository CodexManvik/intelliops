import sys
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from common.interfaces import BusClient


@pytest.fixture()
def redis_bus():
    fakeredis = pytest.importorskip("fakeredis")
    from common.bus import RedisBus

    client = fakeredis.FakeStrictRedis(decode_responses=True)
    return RedisBus(client=client)


def test_redisbus_satisfies_protocol(redis_bus):
    assert isinstance(redis_bus, BusClient)


def test_publish_then_consume_roundtrips(redis_bus):
    redis_bus.publish("telemetry.raw", {"name": "cpu", "value": "0.9"})
    messages = list(_take(redis_bus.consume("telemetry.raw", group="g1"), 1))
    assert messages[0]["name"] == "cpu"
    assert messages[0]["value"] == "0.9"


def test_settings_default_redis_url():
    from common.config import get_settings

    assert get_settings().redis_url.startswith("redis://")


def test_redisbus_ping_ok():
    fakeredis = pytest.importorskip("fakeredis")
    from common.bus import RedisBus

    bus = RedisBus(client=fakeredis.FakeStrictRedis(decode_responses=True))
    bus.ping()  # must not raise against a live (fake) client


def test_redisbus_ping_raises_when_down():
    from common.bus import RedisBus

    class _DeadClient:
        def ping(self):
            raise ConnectionError("redis down")

    bus = RedisBus(client=_DeadClient())
    with pytest.raises(ConnectionError):
        bus.ping()


def _take(iterator, n):
    out = []
    for item in iterator:
        out.append(item)
        if len(out) >= n:
            break
    return out


# ---------------------------------------------------------------------------
# Unit tests for KafkaBus and make_bus (Task 3.3)
# ---------------------------------------------------------------------------


def test_kafkabus_satisfies_protocol():
    """KafkaBus must satisfy the BusClient structural protocol (Req 4.1)."""
    from common.bus import KafkaBus

    bus = KafkaBus(bootstrap_servers="localhost:9092")
    assert isinstance(bus, BusClient)


def settings_with_backend(backend: str):
    """Helper: return a Settings instance with the given bus_backend."""
    from common.config import Settings

    return Settings(bus_backend=backend)


def test_make_bus_returns_redis_bus():
    """make_bus returns RedisBus when bus_backend is 'redis' (Req 3.3)."""
    from common.bus import RedisBus, make_bus

    bus = make_bus(settings_with_backend("redis"))
    assert isinstance(bus, RedisBus)


def test_make_bus_returns_kafka_bus():
    """make_bus returns KafkaBus when bus_backend is 'kafka' (Req 3.4)."""
    from common.bus import KafkaBus, make_bus

    bus = make_bus(settings_with_backend("kafka"))
    assert isinstance(bus, KafkaBus)


def test_make_bus_raises_on_unknown_backend():
    """make_bus raises ValueError for an unrecognised backend (Req 3.5)."""
    from common.bus import make_bus

    with pytest.raises(ValueError, match="Unknown bus backend"):
        make_bus(settings_with_backend("nats"))


def test_lazy_import():
    """Importing KafkaBus must NOT trigger the kafka package import (Req 4.7).

    The 'kafka' top-level module should not appear in sys.modules after merely
    importing the KafkaBus class — lazy imports only happen inside methods.
    """
    # Remove kafka from sys.modules if somehow loaded by a previous test
    for key in list(sys.modules):
        if key == "kafka" or key.startswith("kafka."):
            del sys.modules[key]

    # Import the class — should not pull in kafka-python
    from common.bus import KafkaBus  # noqa: F401

    assert "kafka" not in sys.modules, (
        "Importing KafkaBus must not eagerly import the 'kafka' package"
    )


def test_enable_auto_commit():
    """KafkaBus.consume must call KafkaConsumer with enable_auto_commit=True (Req 4.4, 4.5)."""
    from common.bus import KafkaBus

    mock_consumer_instance = MagicMock()
    mock_consumer_instance.__iter__ = MagicMock(return_value=iter([]))

    with patch("kafka.KafkaConsumer", return_value=mock_consumer_instance) as mock_consumer_cls:
        bus = KafkaBus(bootstrap_servers="localhost:9092", consumer_name="test-consumer")
        # Consume from the generator — we only need to trigger the constructor call
        list(bus.consume("test-topic", "test-group"))

    mock_consumer_cls.assert_called_once()
    _, kwargs = mock_consumer_cls.call_args
    assert kwargs.get("enable_auto_commit") is True, (
        f"KafkaConsumer must be called with enable_auto_commit=True, "
        f"got: {kwargs.get('enable_auto_commit')!r}"
    )


# Feature: stream-d-implementation, Property 5: make_bus dispatch correctness
# Validates: Requirements 3.1, 3.3, 3.4
@given(st.sampled_from(["redis", "kafka"]))
@settings(max_examples=100)
def test_make_bus_dispatch_correctness(backend: str) -> None:
    """**Validates: Requirements 3.1, 3.3, 3.4**

    Property 5: make_bus dispatch correctness.

    For any bus_backend value that is either "redis" or "kafka", calling
    make_bus with a Settings instance containing that value returns an instance
    that satisfies the BusClient protocol and is of the correct concrete type
    (RedisBus for "redis", KafkaBus for "kafka").
    """
    from common.bus import KafkaBus, RedisBus, make_bus
    from common.config import Settings

    s = Settings(bus_backend=backend)
    bus = make_bus(s)

    # Must satisfy the BusClient structural protocol
    assert isinstance(bus, BusClient), (
        f"make_bus({backend!r}) returned {type(bus)!r} which does not satisfy BusClient"
    )

    # Must be the correct concrete type
    if backend == "redis":
        assert isinstance(bus, RedisBus), (
            f"Expected RedisBus for backend='redis', got {type(bus)!r}"
        )
    else:
        assert isinstance(bus, KafkaBus), (
            f"Expected KafkaBus for backend='kafka', got {type(bus)!r}"
        )


# Feature: stream-d-implementation, Property 6: Invalid backend rejection
@given(st.text().filter(lambda s: s not in ("redis", "kafka")))
@settings(max_examples=100)
def test_make_bus_raises_on_invalid_backend(invalid_backend: str) -> None:
    """**Validates: Requirements 3.5**

    For any string that is not "redis" and not "kafka", make_bus must raise
    a ValueError identifying the invalid backend value.
    """
    from common.bus import make_bus
    from common.config import Settings

    s = Settings(bus_backend=invalid_backend)
    with pytest.raises(ValueError, match="Unknown bus backend"):
        make_bus(s)


# --- Delivery semantics (issue #53) -----------------------------------------
#
# These are the tests that actually prove the at-least-once claim. Each drives
# the real RedisBus against fakeredis and asserts on the PENDING list, which is
# the ground truth for "was this acked?".


def _bus(delivery="at_most_once", **kw):
    fakeredis = pytest.importorskip("fakeredis")
    from common.bus import RedisBus

    client = fakeredis.FakeStrictRedis(decode_responses=True)
    return RedisBus(client=client, delivery=delivery, **kw)


def _pending(bus, topic, group):
    return bus._r.xpending(topic, group)["pending"]


def test_at_most_once_acks_before_yield_and_loses_the_in_flight_entry():
    """The default, pinned: the entry is already acked when the handler sees it,
    so a crash mid-handler loses it permanently and it is never redelivered."""
    bus = _bus("at_most_once")
    bus.publish("t", {"n": "1"})
    gen = bus.consume("t", "g")
    next(gen)
    assert _pending(bus, "t", "g") == 0  # already acked before we got it
    gen.close()  # the handler "crashed"

    # A restart sees nothing pending to recover.
    bus2 = _bus_sharing(bus, "at_most_once")
    assert _pending(bus2, "t", "g") == 0


def _bus_sharing(other, delivery, **kw):
    """A second bus on the SAME fakeredis client - i.e. a restarted consumer."""
    from common.bus import RedisBus

    return RedisBus(client=other._r, delivery=delivery, **kw)


def test_at_least_once_leaves_the_in_flight_entry_pending():
    bus = _bus("at_least_once")
    bus.publish("t", {"n": "1"})
    gen = bus.consume("t", "g")
    msg = next(gen)
    assert msg["n"] == "1"
    # Not acked: being resumed is the only proof the handler finished, and we
    # have not resumed it.
    assert _pending(bus, "t", "g") == 1
    gen.close()
    assert _pending(bus, "t", "g") == 1  # a crash must NOT ack


def test_at_least_once_acks_only_when_resumed():
    bus = _bus("at_least_once")
    bus.publish("t", {"n": "1"})
    bus.publish("t", {"n": "2"})
    gen = bus.consume("t", "g")
    next(gen)
    assert _pending(bus, "t", "g") == 1
    next(gen)  # coming back for the next entry acks the first
    assert _pending(bus, "t", "g") == 1  # #1 acked, #2 now in flight
    gen.close()


def test_at_least_once_redelivers_after_a_crash():
    """The headline guarantee: an event handled by a consumer that then dies is
    served again to the next consumer under the same name."""
    bus = _bus("at_least_once")
    bus.publish("t", {"n": "1"})
    gen = bus.consume("t", "g")
    assert next(gen)["n"] == "1"
    gen.close()  # crash before the ack

    restarted = _bus_sharing(bus, "at_least_once")
    gen2 = restarted.consume("t", "g")
    assert next(gen2)["n"] == "1"  # redelivered, not lost
    gen2.close()


def test_at_most_once_does_not_redeliver_after_a_crash():
    """The contrast that makes the previous test meaningful."""
    bus = _bus("at_most_once")
    bus.publish("t", {"n": "1"})
    bus.publish("t", {"n": "2"})
    gen = bus.consume("t", "g")
    assert next(gen)["n"] == "1"
    gen.close()

    restarted = _bus_sharing(bus, "at_most_once")
    gen2 = restarted.consume("t", "g")
    assert next(gen2)["n"] == "2"  # #1 is gone forever
    gen2.close()


def test_dlq_parks_an_entry_after_max_delivery_attempts():
    """A payload this build cannot handle must not be redelivered forever."""
    bus = _bus("at_least_once", dlq_mode="on", max_delivery_attempts=2)
    bus.publish("t", {"n": "poison"})
    # A follow-on entry, so the cycle that parks the poison has something to
    # return instead of blocking on an empty stream.
    bus.publish("t", {"n": "good"})

    # Crash-restart cycles: served, served, then parked and skipped.
    seen = []
    for _ in range(3):
        gen = bus.consume("t", "g")
        seen.append(next(gen)["n"])
        gen.close()
    # The third cycle skipped the parked poison and moved on.
    assert seen == ["poison", "poison", "good"]

    dlq = bus._r.xrange("t.dlq")
    assert len(dlq) == 1
    _entry_id, fields = dlq[0]
    assert fields["n"] == "poison"
    assert fields["_dlq_reason"] == "max-delivery-attempts"
    assert fields["_dlq_topic"] == "t"
    # The poison was acked when it was parked, so it is no longer pending. The
    # ONE remaining pending entry is "good" - correctly un-acked, because the
    # test closed the generator instead of coming back for the next entry.
    assert _pending(bus, "t", "g") == 1
    pending_ids = {e["message_id"] for e in bus._r.xpending_range("t", "g", "-", "+", 10)}
    poison_id = bus._r.xrange("t")[0][0]
    assert poison_id not in pending_ids


def test_dlq_off_by_default_leaves_the_entry_pending_forever():
    bus = _bus("at_least_once", max_delivery_attempts=1)
    bus.publish("t", {"n": "poison"})
    for _ in range(3):
        gen = bus.consume("t", "g")
        next(gen)
        gen.close()
    assert bus._r.exists("t.dlq") == 0
    assert _pending(bus, "t", "g") == 1
