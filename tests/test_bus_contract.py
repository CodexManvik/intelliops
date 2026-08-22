"""Bus contract test suite — parametrized over RedisBus and KafkaBus.

Tests run against both backends through the same assertions, verifying that
KafkaBus satisfies the same BusClient contract as RedisBus.

Kafka-parametrized test cases carry the ``kafka`` marker and are excluded
from the fast CI job (``not kafka``).

Requirements: 1.1, 1.2, 1.3, 1.8
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from common.interfaces import BusClient

# ── marker registration ───────────────────────────────────────────────────────


def pytest_configure(config: pytest.Config) -> None:
    """Register the ``kafka`` marker so pytest doesn't warn about unknown marks."""
    config.addinivalue_line(
        "markers",
        "kafka: tests that require a real Kafka broker (testcontainers + Docker)",
    )


# ── session-scoped Kafka container fixture ────────────────────────────────────


@pytest.fixture(scope="session")
def kafka_bootstrap(request: pytest.FixtureRequest) -> str:  # type: ignore[return]
    """Start a KafkaContainer for the session; skip gracefully if Docker is unavailable.

    Returns the bootstrap server string (e.g. ``"localhost:XXXXX"``) that
    KafkaBus uses to connect.

    Requirements: 1.3
    """
    testcontainers_kafka = pytest.importorskip(
        "testcontainers.kafka",
        reason="testcontainers[kafka] not installed",
    )

    try:
        container = testcontainers_kafka.KafkaContainer()
        container.start()
    except (OSError, RuntimeError) as exc:  # Docker daemon not running or unavailable
        pytest.skip(f"Docker unavailable, skipping Kafka tests: {exc}")

    bootstrap = container.get_bootstrap_server()

    # Ensure the container is stopped at the end of the session
    request.addfinalizer(container.stop)

    return bootstrap


# ── parametrized bus fixture ──────────────────────────────────────────────────


@pytest.fixture(
    params=[
        "redis",
        pytest.param("kafka", marks=pytest.mark.kafka),
    ]
)
def bus(request: pytest.FixtureRequest):
    """Parametrized BusClient fixture.

    - ``redis``: backed by ``fakeredis.FakeRedis`` — no external services needed.
    - ``kafka``: backed by a real ``KafkaContainer`` broker.

    Requirements: 1.1, 1.2, 1.3, 1.8
    """
    if request.param == "redis":
        fakeredis = pytest.importorskip(
            "fakeredis",
            reason="fakeredis not installed",
        )
        from common.bus import RedisBus

        return RedisBus(client=fakeredis.FakeRedis(decode_responses=True))

    else:  # kafka
        # Only request kafka_bootstrap lazily for kafka param to avoid Docker
        # dependency in redis-parameterized tests.
        kafka_bootstrap = request.getfixturevalue("kafka_bootstrap")
        from common.bus import KafkaBus

        return KafkaBus(bootstrap_servers=kafka_bootstrap)


# ── Hypothesis strategies ─────────────────────────────────────────────────────

# Message dict strategy: alphanumeric str keys and values, 1–8 pairs.
# Keys: 1–20 chars; Values: 0–100 chars — both from the alphanumeric alphabet
# so they survive JSON round-trips and Kafka/Redis encoding without surprises.
_ALNUM = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

message_strategy = st.dictionaries(
    keys=st.text(alphabet=_ALNUM, min_size=1, max_size=20),
    values=st.text(alphabet=_ALNUM, min_size=0, max_size=100),
    min_size=1,
    max_size=8,
)

# Topic name strategy: lowercase alphanumeric, 3–30 chars.
# Kept simple to avoid broker-side validation errors on topic names.
topic_strategy = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
    min_size=3,
    max_size=30,
)

# Group name strategy: alphanumeric, 3–20 chars.
group_strategy = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
    min_size=3,
    max_size=20,
)


# ── contract tests ────────────────────────────────────────────────────────────


def test_satisfies_protocol(bus) -> None:
    """Assert the bus fixture satisfies the BusClient protocol.

    Requirements: 1.1
    """
    assert isinstance(bus, BusClient)


def test_publish_consume_roundtrip(bus) -> None:
    """Publish a message then consume it; assert received dict equals published dict.

    # Feature: stream-d-implementation, Property 1: bus publish/consume round-trip

    Requirements: 1.4
    """
    # Validates: Requirements 1.4
    topic = "roundtrip-test"
    message = {"event": "deploy", "service": "ingestion"}
    bus.publish(topic, message)
    received = next(bus.consume(topic, group="g-roundtrip"))
    assert received == message


@given(message=message_strategy, topic=topic_strategy)
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_prop_publish_consume_roundtrip(bus, message: dict, topic: str) -> None:
    """For any message and topic, publish then consume returns the same dict.

    # Feature: stream-d-implementation, Property 1: bus publish/consume round-trip

    Validates: Requirements 1.4
    """
    # Validates: Requirements 1.4
    bus.publish(topic, message)
    received = next(bus.consume(topic, group="g-prop-roundtrip"))
    assert received == message


@given(
    messages=st.lists(
        message_strategy,
        min_size=2,
        max_size=8,
        unique_by=lambda d: tuple(sorted(d.items())),
    ),
    topic=topic_strategy,
)
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_prop_consumer_group_load_balancing(bus, messages: list, topic: str) -> None:
    """For any list of ≥2 distinct messages on one topic, two consumers in the same
    group receive disjoint sets whose union equals all published messages.

    # Feature: stream-d-implementation, Property 2: consumer group load-balancing

    Validates: Requirements 1.5
    """
    group = "g-lb-prop"

    # Flush the Redis stream for this topic before each example to prevent
    # stale messages from prior Hypothesis iterations polluting the results.
    if hasattr(bus, "_r"):
        bus._r.delete(topic)

    # Publish every message to the topic
    for msg in messages:
        bus.publish(topic, msg)

    # Drain up to len(messages) items across two consumers in the same group.
    # Each consumer may receive anywhere from 0 to N messages; together they
    # must cover the full set exactly once (disjoint + union = published).
    consumer_a = bus.consume(topic, group=group)
    consumer_b = bus.consume(topic, group=group)

    received_a: list[dict] = []
    received_b: list[dict] = []

    # Round-robin between the two consumers until all messages are collected.
    remaining = len(messages)
    gen_a_exhausted = False
    gen_b_exhausted = False

    while remaining > 0:
        if not gen_a_exhausted:
            try:
                received_a.append(next(consumer_a))
                remaining -= 1
                if remaining == 0:
                    break
            except StopIteration:
                gen_a_exhausted = True

        if not gen_b_exhausted:
            try:
                received_b.append(next(consumer_b))
                remaining -= 1
            except StopIteration:
                gen_b_exhausted = True

        if gen_a_exhausted and gen_b_exhausted:
            break

    # Convert to frozensets for set-algebra assertions
    def to_frozen(lst: list[dict]) -> set[frozenset]:
        return {frozenset(d.items()) for d in lst}

    set_a = to_frozen(received_a)
    set_b = to_frozen(received_b)
    set_all = to_frozen(messages)

    assert set_a.isdisjoint(set_b), "Consumer groups in same group received overlapping messages"
    assert set_a | set_b == set_all, "Union of both consumers does not equal all published messages"


def test_independent_groups_fanout(bus) -> None:
    """Two consumers each in a different group both receive the same published message.

    # Feature: stream-d-implementation, Property 3: independent groups fan-out

    Publish one message to a topic; create two consumers under different group names;
    assert both receive a dict with identical field values.

    Requirements: 1.6
    """
    # Validates: Requirements 1.6
    topic = "fanout-test"
    message = {"event": "alert", "severity": "high"}

    bus.publish(topic, message)

    received_g1 = next(bus.consume(topic, group="g-fanout-1"))
    received_g2 = next(bus.consume(topic, group="g-fanout-2"))

    assert received_g1 == message
    assert received_g2 == message
    assert received_g1 == received_g2


@given(
    message=message_strategy,
    topic=topic_strategy,
    groups=st.lists(group_strategy, min_size=2, max_size=2, unique=True),
)
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_prop_independent_groups_fanout(bus, message: dict, topic: str, groups: list) -> None:
    """For any message and two distinct group names, both groups receive the message.

    # Feature: stream-d-implementation, Property 3: independent groups fan-out

    Validates: Requirements 1.6
    """
    g1, g2 = groups

    # Flush the Redis stream for this topic before each example so that
    # accumulated messages from prior Hypothesis iterations don't pollute
    # the consumer groups (which are created at id="0" and would otherwise
    # see stale entries from earlier examples on the shared FakeRedis instance).
    if hasattr(bus, "_r"):
        bus._r.delete(topic)

    bus.publish(topic, message)

    received_g1 = next(bus.consume(topic, group=g1))
    received_g2 = next(bus.consume(topic, group=g2))

    assert received_g1 == message
    assert received_g2 == message
    assert received_g1 == received_g2


def test_idempotent_group_creation(bus) -> None:
    """Calling consume on the same topic/group twice does not raise an exception.

    # Feature: stream-d-implementation, Property 4: idempotent consumer group creation

    Creates a consumer group by consuming once, then creates the same group again;
    asserts no exception is raised on the second call.

    Requirements: 1.7
    """
    # Validates: Requirements 1.7
    topic = "idempotent-group-test"
    group = "g-idempotent"
    message = {"check": "idempotent"}

    # First consume — creates the consumer group
    bus.publish(topic, message)
    next(bus.consume(topic, group=group))

    # Second consume with the same topic/group — must not raise even though the group
    # already exists (RedisBus catches BUSYGROUP; KafkaBus consumer groups are
    # inherently idempotent)
    bus.publish(topic, message)
    next(bus.consume(topic, group=group))


@given(topic=topic_strategy, group=group_strategy)
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_prop_idempotent_group_creation(bus, topic: str, group: str) -> None:
    """For any topic and group name, calling consume twice on the same topic/group
    must not raise any exception.

    # Feature: stream-d-implementation, Property 4: idempotent consumer group creation

    Validates: Requirements 1.7
    """
    message = {"check": "idempotent"}

    # First consume — establishes the consumer group
    bus.publish(topic, message)
    next(bus.consume(topic, group=group))

    # Second consume — group already exists; must not raise
    bus.publish(topic, message)
    next(bus.consume(topic, group=group))
