def test_redisbus_accepts_custom_consumer_name():
    import fakeredis

    from common.bus import RedisBus

    client = fakeredis.FakeStrictRedis(decode_responses=True)
    bus = RedisBus(client=client, consumer_name="correlation-1")
    bus.publish("t", {"data": "x"})
    got = next(iter(bus.consume("t", group="g")))
    assert got == {"data": "x"}


def test_make_bus_passes_consumer_name():
    from common.bus import make_bus
    from common.config import Settings

    bus = make_bus(Settings(redis_url="redis://localhost:6379"), consumer_name="c-2")
    assert bus._consumer == "c-2"
