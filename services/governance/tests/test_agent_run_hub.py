import asyncio
from datetime import UTC, datetime

from common.contracts import TraceStep, TraceStepKind
from services.governance.agent_run_hub import AgentRunHub


def _step(
    run_id: str = "run-1", seq: int = 0, kind: TraceStepKind = TraceStepKind.MODEL_TURN
) -> TraceStep:
    return TraceStep(run_id=run_id, seq=seq, kind=kind, ts=datetime.now(UTC), text="thinking")


def test_publish_is_noop_when_no_loop_bound():
    # No loop bound (e.g. publish from a worker thread before the lifespan
    # has called bind_loop) must not raise.
    hub = AgentRunHub()
    hub.publish("run-1", _step())


def test_subscribe_receives_published_step():
    async def run():
        hub = AgentRunHub()
        hub.bind_loop(asyncio.get_running_loop())
        q = hub.subscribe("run-1")
        step = _step()
        hub.publish("run-1", step)
        # publish marshals via call_soon_threadsafe; let the loop run the callback
        await asyncio.sleep(0)
        got = await asyncio.wait_for(q.get(), timeout=1.0)
        assert got == step

    asyncio.run(run())


def test_full_queue_drops_oldest():
    async def run():
        hub = AgentRunHub()
        hub.bind_loop(asyncio.get_running_loop())
        q = hub.subscribe("run-1", maxsize=1)
        first = _step(seq=0)
        second = _step(seq=1)
        hub.publish("run-1", first)
        hub.publish("run-1", second)
        await asyncio.sleep(0)
        got = await asyncio.wait_for(q.get(), timeout=1.0)
        assert got == second  # oldest dropped, newest kept
        assert q.empty()

    asyncio.run(run())


def test_unsubscribe_stops_delivery():
    async def run():
        hub = AgentRunHub()
        hub.bind_loop(asyncio.get_running_loop())
        q = hub.subscribe("run-1")
        hub.unsubscribe("run-1", q)
        assert q not in hub._subs.get("run-1", set())

        hub.publish("run-1", _step())
        await asyncio.sleep(0)
        assert q.empty()

    asyncio.run(run())


def test_subscribers_isolated_by_run_id():
    async def run():
        hub = AgentRunHub()
        hub.bind_loop(asyncio.get_running_loop())
        q_a = hub.subscribe("run-A")
        q_b = hub.subscribe("run-B")

        step_a = _step(run_id="run-A")
        hub.publish("run-A", step_a)
        await asyncio.sleep(0)

        got = await asyncio.wait_for(q_a.get(), timeout=1.0)
        assert got == step_a
        assert q_b.empty()  # run-B's subscriber must not see run-A's publish

    asyncio.run(run())


def test_two_subscribers_same_run_both_receive():
    async def run():
        hub = AgentRunHub()
        hub.bind_loop(asyncio.get_running_loop())
        q1 = hub.subscribe("run-1")
        q2 = hub.subscribe("run-1")

        step = _step()
        hub.publish("run-1", step)
        await asyncio.sleep(0)

        got1 = await asyncio.wait_for(q1.get(), timeout=1.0)
        got2 = await asyncio.wait_for(q2.get(), timeout=1.0)
        assert got1 == step
        assert got2 == step

    asyncio.run(run())


def test_mark_ended_and_is_ended():
    hub = AgentRunHub()
    assert hub.is_ended("run-1") is False
    hub.mark_ended("run-1")
    assert hub.is_ended("run-1") is True
    # unrelated run_id is unaffected
    assert hub.is_ended("run-2") is False
