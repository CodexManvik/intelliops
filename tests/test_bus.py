import pytest

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


def _take(iterator, n):
    out = []
    for item in iterator:
        out.append(item)
        if len(out) >= n:
            break
    return out
