import asyncio

import pytest

from fera.adapters.bus import EventBus


@pytest.mark.asyncio
async def test_subscribe_receives_matching_session():
    bus = EventBus()
    received = []
    async def cb(event):
        received.append(event)
    bus.subscribe("s1", cb)
    await bus.publish({"type": "event", "session": "s1", "event": "agent.text"})
    assert len(received) == 1
    assert received[0]["session"] == "s1"


@pytest.mark.asyncio
async def test_subscribe_ignores_other_session():
    bus = EventBus()
    received = []
    async def cb(event):
        received.append(event)
    bus.subscribe("s1", cb)
    await bus.publish({"type": "event", "session": "s2", "event": "agent.text"})
    assert len(received) == 0


@pytest.mark.asyncio
async def test_wildcard_receives_all_sessions():
    bus = EventBus()
    received = []
    async def cb(event):
        received.append(event)
    bus.subscribe("*", cb)
    await bus.publish({"type": "event", "session": "s1", "event": "agent.text"})
    await bus.publish({"type": "event", "session": "s2", "event": "agent.done"})
    assert len(received) == 2


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery():
    bus = EventBus()
    received = []
    async def cb(event):
        received.append(event)
    bus.subscribe("s1", cb)
    await bus.publish({"type": "event", "session": "s1", "event": "agent.text"})
    assert len(received) == 1
    bus.unsubscribe("s1", cb)
    await bus.publish({"type": "event", "session": "s1", "event": "agent.done"})
    assert len(received) == 1


@pytest.mark.asyncio
async def test_multiple_subscribers_same_session():
    bus = EventBus()
    r1, r2 = [], []
    async def cb1(event):
        r1.append(event)
    async def cb2(event):
        r2.append(event)
    bus.subscribe("s1", cb1)
    bus.subscribe("s1", cb2)
    await bus.publish({"type": "event", "session": "s1", "event": "agent.text"})
    assert len(r1) == 1
    assert len(r2) == 1


@pytest.mark.asyncio
async def test_failing_callback_does_not_break_others():
    bus = EventBus()
    received = []
    async def bad_cb(event):
        raise RuntimeError("boom")
    async def good_cb(event):
        received.append(event)
    bus.subscribe("s1", bad_cb)
    bus.subscribe("s1", good_cb)
    await bus.publish({"type": "event", "session": "s1", "event": "agent.text"})
    assert len(received) == 1
