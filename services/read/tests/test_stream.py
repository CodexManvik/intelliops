import asyncio
from typing import ClassVar

from services.read.projection import ReadModel


def test_publish_is_noop_when_no_loop_bound():
    # The existing sync tests call apply_* with no loop; publish must not raise.
    m = ReadModel()
    m.publish({"type": "changed"})  # no loop bound → silent no-op


def test_subscribe_receives_published_event():
    async def run():
        m = ReadModel()
        m.bind_loop(asyncio.get_running_loop())
        q = m.subscribe()
        m.publish({"type": "changed"})
        # publish marshals via call_soon_threadsafe; let the loop run the callback
        await asyncio.sleep(0)
        event = await asyncio.wait_for(q.get(), timeout=1.0)
        assert event == {"type": "changed"}
        m.unsubscribe(q)
        assert q not in m._subscribers

    asyncio.run(run())


def test_full_queue_drops_oldest():
    async def run():
        m = ReadModel()
        m.bind_loop(asyncio.get_running_loop())
        q = m.subscribe(maxsize=1)
        m.publish({"n": 1})
        m.publish({"n": 2})
        await asyncio.sleep(0)
        got = await asyncio.wait_for(q.get(), timeout=1.0)
        assert got == {"n": 2}  # oldest dropped, newest kept

    asyncio.run(run())


def test_stream_authorized_off_mode(monkeypatch):
    from common.config import Settings
    from services.read import app as read_app

    monkeypatch.setattr(read_app, "get_settings", lambda: Settings(auth_mode="off"))

    class Req:  # minimal stub
        query_params: ClassVar[dict] = {}

    assert read_app._stream_authorized(Req()) is True


def test_stream_authorized_token_mode_requires_match(monkeypatch):
    from common.config import Settings
    from services.read import app as read_app

    monkeypatch.setattr(
        read_app,
        "get_settings",
        lambda: Settings(auth_mode="token", auth_token="secret"),
    )

    class Req:
        def __init__(self, tok):
            self.query_params = {"token": tok}

    assert read_app._stream_authorized(Req("secret")) is True
    assert read_app._stream_authorized(Req("wrong")) is False
    assert read_app._stream_authorized(Req("")) is False
