import asyncio

import pytest

from fera.gateway.lanes import LaneManager


@pytest.mark.asyncio
async def test_lane_serializes_access():
    mgr = LaneManager()
    order = []

    async def task(name, delay):
        async with mgr.acquire("session-1"):
            order.append(f"{name}-start")
            await asyncio.sleep(delay)
            order.append(f"{name}-end")

    t1 = asyncio.create_task(task("first", 0.1))
    await asyncio.sleep(0.01)  # Ensure first starts first
    t2 = asyncio.create_task(task("second", 0.0))
    await asyncio.gather(t1, t2)

    assert order.index("first-end") < order.index("second-start")


@pytest.mark.asyncio
async def test_different_lanes_run_parallel():
    mgr = LaneManager()
    order = []

    async def task(session, name, delay):
        async with mgr.acquire(session):
            order.append(f"{name}-start")
            await asyncio.sleep(delay)
            order.append(f"{name}-end")

    t1 = asyncio.create_task(task("session-a", "a", 0.1))
    await asyncio.sleep(0.01)
    t2 = asyncio.create_task(task("session-b", "b", 0.0))
    await asyncio.gather(t1, t2)

    # b should start before a ends (parallel)
    assert order.index("b-start") < order.index("a-end")


@pytest.mark.asyncio
async def test_lane_releases_on_exception():
    mgr = LaneManager()

    with pytest.raises(ValueError):
        async with mgr.acquire("session-1"):
            raise ValueError("oops")

    # Should be able to acquire again
    async with mgr.acquire("session-1"):
        pass  # No deadlock


@pytest.mark.asyncio
async def test_is_locked_false_when_free():
    mgr = LaneManager()
    assert mgr.is_locked("session-1") is False


@pytest.mark.asyncio
async def test_is_locked_true_when_held():
    mgr = LaneManager()
    acquired = asyncio.Event()

    async def holder():
        async with mgr.acquire("session-1"):
            acquired.set()
            await asyncio.sleep(0.5)

    task = asyncio.create_task(holder())
    await acquired.wait()
    assert mgr.is_locked("session-1") is True
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_is_locked_false_after_release():
    mgr = LaneManager()
    async with mgr.acquire("session-1"):
        pass
    assert mgr.is_locked("session-1") is False


# --- Message queue ---


def test_enqueue_adds_message():
    mgr = LaneManager()
    mgr.enqueue("s1", "hello", "mattermost")
    assert mgr.drain_queue("s1") == [("hello", "mattermost")]


def test_enqueue_preserves_order():
    mgr = LaneManager()
    mgr.enqueue("s1", "first", "mm")
    mgr.enqueue("s1", "second", "mm")
    mgr.enqueue("s1", "third", "tg")
    assert mgr.drain_queue("s1") == [
        ("first", "mm"),
        ("second", "mm"),
        ("third", "tg"),
    ]


def test_drain_clears_queue():
    mgr = LaneManager()
    mgr.enqueue("s1", "msg", "src")
    mgr.drain_queue("s1")
    assert mgr.drain_queue("s1") == []


def test_drain_empty_returns_empty():
    mgr = LaneManager()
    assert mgr.drain_queue("unknown-session") == []


def test_enqueue_isolates_sessions():
    mgr = LaneManager()
    mgr.enqueue("s1", "for-s1", "mm")
    mgr.enqueue("s2", "for-s2", "tg")
    assert mgr.drain_queue("s1") == [("for-s1", "mm")]
    assert mgr.drain_queue("s2") == [("for-s2", "tg")]
