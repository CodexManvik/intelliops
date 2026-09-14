"""Idempotency guards and the two-phase claim in iter_models (issue #53).

This module exists because its absence let two feature-voiding bugs ship green:
`common/idempotency.py` had no tests at all, and nothing anywhere exercised
`iter_models` with a guard. The regression test that matters most is
`test_crashed_handler_is_reprocessed_not_skipped`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from common.contracts import Situation, SituationStatus
from common.envelope import iter_models, publish_model
from common.idempotency import MemoryGuard, NullGuard, RedisGuard, make_guard

TS = datetime(2026, 9, 14, tzinfo=UTC)


def _sit(sid):
    return Situation(
        id=sid,
        status=SituationStatus.DETECTED,
        member_events=[],
        severity="high",
        first_seen=TS,
        last_seen=TS,
        signature=sid,
    )


class _S:
    bus_idempotency_mode = "off"
    bus_idempotency_ttl_seconds = 86_400


# --------------------------------------------------------------- the guards ---


def test_null_guard_is_inert():
    g = NullGuard()
    assert g.claim("k") is True
    assert g.claim("k") is True  # never blocks anything
    assert g.state("k") is None


def test_memory_guard_claims_once():
    g = MemoryGuard()
    assert g.claim("k") is True
    assert g.claim("k") is False
    assert g.state("k") == "1"
    g.set_state("k", "done")
    assert g.state("k") == "done"


def test_memory_guard_honours_ttl():
    g = MemoryGuard(ttl_seconds=0)  # already expired on read
    assert g.claim("k") is True
    assert g.state("k") is None
    assert g.claim("k") is True  # the expired claim does not block a retry


def test_memory_guard_is_bounded():
    g = MemoryGuard(max_keys=3)
    for i in range(5):
        g.claim(f"k{i}")
    assert g.state("k0") is None  # evicted
    assert g.state("k4") == "1"


def test_redis_guard_claims_once():
    fakeredis = pytest.importorskip("fakeredis")
    g = RedisGuard(fakeredis.FakeStrictRedis(decode_responses=True))
    assert g.claim("k") is True
    assert g.claim("k") is False
    g.set_state("k", "done")
    assert g.state("k") == "done"


def test_make_guard_selection():
    fakeredis = pytest.importorskip("fakeredis")
    from common.bus import RedisBus

    s = _S()
    assert isinstance(make_guard(s), NullGuard)
    s.bus_idempotency_mode = "memory"
    assert isinstance(make_guard(s), MemoryGuard)
    s.bus_idempotency_mode = "redis"
    bus = RedisBus(client=fakeredis.FakeStrictRedis(decode_responses=True))
    assert isinstance(make_guard(s, bus), RedisGuard)
    # No Redis client to share (the Kafka path) must degrade, not crash.
    assert isinstance(make_guard(s, object()), MemoryGuard)


# ------------------------------------------------ iter_models with a guard ---


def _bus(**kw):
    fakeredis = pytest.importorskip("fakeredis")
    from common.bus import RedisBus

    return RedisBus(client=fakeredis.FakeStrictRedis(decode_responses=True), **kw)


def test_duplicate_of_a_completed_event_is_skipped():
    """A second copy of an event whose handler already finished must be dropped."""
    bus = _bus()
    guard = RedisGuard(bus._r)
    publish_model(bus, "t", _sit("s1"))
    duplicate = dict(bus._r.xrange("t")[0][1])  # same envelope id
    bus.publish("t", duplicate)
    publish_model(bus, "t", _sit("s2"))

    seen = []
    gen = iter_models(bus, "t", "g", Situation, guard=guard)
    for msg in gen:
        seen.append(msg.id)
        if len(seen) == 2:
            break
    gen.close()
    # The duplicate sat between them and was skipped, not yielded.
    assert seen == ["s1", "s2"]


def test_crashed_handler_is_reprocessed_not_skipped():
    """THE regression test.

    A single-phase claim (claim before the handler runs) silently converts
    at-least-once back into at-most-once: the bus correctly redelivers the event
    the handler crashed on, and the guard then discards and acks it. The claim
    must only suppress an event whose handler actually FINISHED.
    """
    bus = _bus(delivery="at_least_once", consumer_name="c1")
    guard = RedisGuard(bus._r)
    publish_model(bus, "t", _sit("s1"))
    publish_model(bus, "t", _sit("s2"))

    gen = iter_models(bus, "t", "g", Situation, guard=guard)
    with pytest.raises(RuntimeError):
        for msg in gen:
            raise RuntimeError(f"handler crashed on {msg.id}")
    gen.close()
    assert bus._r.xpending("t", "g")["pending"] == 1  # bus did its part

    restarted = type(bus)(client=bus._r, delivery="at_least_once", consumer_name="c1")
    gen2 = iter_models(restarted, "t", "g", Situation, guard=guard)
    assert next(gen2).id == "s1", "the crashed event must come back, not be swallowed"
    gen2.close()


def test_guard_lets_through_messages_without_an_event_id():
    """Backlog entries published before the envelope id existed must not vanish."""
    bus = _bus()
    guard = RedisGuard(bus._r)
    bus.publish("t", {"data": _sit("s1").model_dump_json()})  # no "id" field
    gen = iter_models(bus, "t", "g", Situation, guard=guard)
    assert next(gen).id == "s1"
    gen.close()
