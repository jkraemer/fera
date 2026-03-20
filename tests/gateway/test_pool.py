from __future__ import annotations

import asyncio

import pytest

from fera.gateway.pool import ClientPool


class FakeClient:
    """Minimal fake that tracks connect/disconnect calls."""

    def __init__(self):
        self.connected = True
        self.disconnected = False

    async def disconnect(self):
        self.connected = False
        self.disconnected = True


def make_fake_factory():
    """Return a factory and a list that collects all clients it created."""
    created: list[FakeClient] = []

    async def factory(session_name: str, sdk_session_id: str | None = None, fork_session: bool = False) -> FakeClient:
        client = FakeClient()
        created.append(client)
        return client

    return factory, created


@pytest.mark.asyncio
async def test_acquire_records_created_at_and_max_age():
    factory, _ = make_fake_factory()
    pool = ClientPool(factory=factory, max_age=100.0, max_age_jitter=20.0)
    await pool.acquire("s1")
    assert "s1" in pool._created_at
    assert "s1" in pool._max_ages
    # max age should be in [100, 120]
    assert 100.0 <= pool._max_ages["s1"] <= 120.0


@pytest.mark.asyncio
async def test_acquire_existing_does_not_reset_created_at():
    factory, _ = make_fake_factory()
    pool = ClientPool(factory=factory, max_age=100.0, max_age_jitter=0.0)
    await pool.acquire("s1")
    original_ts = pool._created_at["s1"]
    await pool.acquire("s1")  # reuse
    assert pool._created_at["s1"] == original_ts


@pytest.mark.asyncio
async def test_release_cleans_up_age_tracking():
    factory, _ = make_fake_factory()
    pool = ClientPool(factory=factory, max_age=100.0, max_age_jitter=0.0)
    await pool.acquire("s1")
    assert "s1" in pool._created_at
    assert "s1" in pool._max_ages
    await pool.release("s1")
    assert "s1" not in pool._created_at
    assert "s1" not in pool._max_ages


@pytest.mark.asyncio
async def test_max_age_defaults():
    factory, _ = make_fake_factory()
    pool = ClientPool(factory=factory)
    assert pool._max_age == 36000
    assert pool._max_age_jitter == 7200


@pytest.mark.asyncio
async def test_acquire_creates_client_on_first_access():
    factory, created = make_fake_factory()
    pool = ClientPool(factory=factory)
    client = await pool.acquire("default")
    assert len(created) == 1
    assert client is created[0]


@pytest.mark.asyncio
async def test_acquire_reuses_client_on_second_access():
    factory, created = make_fake_factory()
    pool = ClientPool(factory=factory)
    first = await pool.acquire("default")
    second = await pool.acquire("default")
    assert first is second
    assert len(created) == 1


@pytest.mark.asyncio
async def test_acquire_creates_separate_clients_per_session():
    factory, created = make_fake_factory()
    pool = ClientPool(factory=factory)
    a = await pool.acquire("session-a")
    b = await pool.acquire("session-b")
    assert a is not b
    assert len(created) == 2


@pytest.mark.asyncio
async def test_acquire_passes_sdk_session_id_to_factory():
    received_ids: list[str | None] = []

    async def factory(session_name: str, sdk_session_id=None, fork_session=False):
        received_ids.append(sdk_session_id)
        return FakeClient()

    pool = ClientPool(factory=factory)
    await pool.acquire("s1", sdk_session_id="abc-123")
    assert received_ids == ["abc-123"]


@pytest.mark.asyncio
async def test_evicts_lru_when_pool_is_full():
    factory, created = make_fake_factory()
    pool = ClientPool(factory=factory, max_clients=2)
    a = await pool.acquire("a")
    await pool.acquire("b")

    # Pool is full (2/2). Acquiring "c" should evict "a" (least recently used).
    c = await pool.acquire("c")
    assert len(created) == 3
    assert a.disconnected
    assert c.connected
    assert pool.size == 2


@pytest.mark.asyncio
async def test_evicts_lru_not_mru():
    factory, created = make_fake_factory()
    pool = ClientPool(factory=factory, max_clients=2)
    a = await pool.acquire("a")
    b = await pool.acquire("b")

    # Touch "a" so "b" becomes LRU
    await pool.acquire("a")

    # Acquiring "c" should evict "b" (LRU), not "a" (MRU)
    await pool.acquire("c")
    assert not a.disconnected
    assert b.disconnected


@pytest.mark.asyncio
async def test_release_removes_client():
    factory, created = make_fake_factory()
    pool = ClientPool(factory=factory)
    await pool.acquire("s1")
    assert pool.size == 1

    await pool.release("s1")
    assert pool.size == 0
    assert created[0].disconnected


@pytest.mark.asyncio
async def test_release_nonexistent_is_noop():
    factory, _ = make_fake_factory()
    pool = ClientPool(factory=factory)
    await pool.release("nope")  # should not raise


@pytest.mark.asyncio
async def test_acquire_after_release_creates_new_client():
    factory, created = make_fake_factory()
    pool = ClientPool(factory=factory)
    first = await pool.acquire("s1")
    await pool.release("s1")
    second = await pool.acquire("s1")
    assert first is not second
    assert len(created) == 2


@pytest.mark.asyncio
async def test_shutdown_disconnects_all():
    factory, created = make_fake_factory()
    pool = ClientPool(factory=factory)
    await pool.acquire("a")
    await pool.acquire("b")

    await pool.shutdown()
    assert all(c.disconnected for c in created)
    assert pool.size == 0


@pytest.mark.asyncio
async def test_idle_reaper_disconnects_expired_clients():
    factory, created = make_fake_factory()
    # Very short timeout for testing
    pool = ClientPool(factory=factory, idle_timeout=0.05)
    await pool.acquire("s1")
    pool.start_reaper()

    # Wait for the client to become idle and get reaped
    await asyncio.sleep(0.2)

    assert pool.size == 0
    assert created[0].disconnected
    pool.stop_reaper()


@pytest.mark.asyncio
async def test_idle_reaper_keeps_recently_used():
    factory, created = make_fake_factory()
    pool = ClientPool(factory=factory, idle_timeout=0.3)
    await pool.acquire("s1")
    pool.start_reaper()

    # Re-acquire before timeout to keep it alive
    await asyncio.sleep(0.1)
    await pool.acquire("s1")
    await asyncio.sleep(0.1)
    await pool.acquire("s1")
    await asyncio.sleep(0.1)

    # Should still be alive — we kept touching it
    assert pool.size == 1
    assert not created[0].disconnected
    pool.stop_reaper()


@pytest.mark.asyncio
async def test_evict_skips_active_session():
    factory, created = make_fake_factory()
    pool = ClientPool(factory=factory, max_clients=2)
    a = await pool.acquire("a")
    b = await pool.acquire("b")

    # Mark "a" as active (mid-turn). "a" is LRU, but should be skipped.
    pool.mark_active("a")

    await pool.acquire("c")
    assert not a.disconnected  # protected by active status
    assert b.disconnected  # evicted instead
    assert pool.size == 2


@pytest.mark.asyncio
async def test_evict_exceeds_max_when_all_active():
    factory, created = make_fake_factory()
    pool = ClientPool(factory=factory, max_clients=2)
    await pool.acquire("a")
    await pool.acquire("b")

    pool.mark_active("a")
    pool.mark_active("b")

    # All clients active — soft cap, pool grows beyond max_clients
    c = await pool.acquire("c")
    assert pool.size == 3
    assert not created[0].disconnected
    assert not created[1].disconnected
    assert c.connected


@pytest.mark.asyncio
async def test_reaper_skips_active_session():
    factory, created = make_fake_factory()
    pool = ClientPool(factory=factory, idle_timeout=0.05)
    await pool.acquire("s1")
    pool.mark_active("s1")
    pool.start_reaper()

    # Wait well past idle timeout
    await asyncio.sleep(0.2)

    # Should still be alive — active sessions are protected
    assert pool.size == 1
    assert not created[0].disconnected
    pool.stop_reaper()


@pytest.mark.asyncio
async def test_mark_idle_allows_eviction():
    factory, created = make_fake_factory()
    pool = ClientPool(factory=factory, max_clients=2)
    a = await pool.acquire("a")
    await pool.acquire("b")

    pool.mark_active("a")
    pool.mark_idle("a")

    # "a" is LRU and no longer active — should be evicted normally
    await pool.acquire("c")
    assert a.disconnected
    assert pool.size == 2


@pytest.mark.asyncio
async def test_mark_idle_nonexistent_is_noop():
    factory, _ = make_fake_factory()
    pool = ClientPool(factory=factory)
    pool.mark_idle("nope")  # should not raise


@pytest.mark.asyncio
async def test_has_client_returns_true_for_existing():
    factory, _ = make_fake_factory()
    pool = ClientPool(factory=factory)
    await pool.acquire("s1")
    assert pool.has_client("s1") is True


@pytest.mark.asyncio
async def test_has_client_returns_false_for_missing():
    factory, _ = make_fake_factory()
    pool = ClientPool(factory=factory)
    assert pool.has_client("nope") is False


@pytest.mark.asyncio
async def test_has_client_returns_false_after_release():
    factory, _ = make_fake_factory()
    pool = ClientPool(factory=factory)
    await pool.acquire("s1")
    await pool.release("s1")
    assert pool.has_client("s1") is False


@pytest.mark.asyncio
async def test_max_clients_zero_disables_eviction():
    factory, created = make_fake_factory()
    pool = ClientPool(factory=factory, max_clients=0)
    clients = []
    for i in range(20):
        clients.append(await pool.acquire(f"s{i}"))
    assert pool.size == 20
    assert not any(c.disconnected for c in created)


@pytest.mark.asyncio
async def test_idle_timeout_zero_disables_idle_reaping():
    """idle_timeout=0 disables idle reaping but age rotation still works."""
    factory, created = make_fake_factory()
    pool = ClientPool(factory=factory, idle_timeout=0, max_age=9999, max_age_jitter=0.0)
    await pool.acquire("s1")
    pool.start_reaper()
    await asyncio.sleep(0.15)
    # Client should still be alive — idle reaping is disabled and max_age hasn't expired
    assert pool.size == 1
    assert not created[0].disconnected
    pool.stop_reaper()


@pytest.mark.asyncio
async def test_negative_idle_timeout_disables_idle_reaping():
    factory, created = make_fake_factory()
    pool = ClientPool(factory=factory, idle_timeout=-1, max_age=9999, max_age_jitter=0.0)
    await pool.acquire("s1")
    pool.start_reaper()
    await asyncio.sleep(0.15)
    assert pool.size == 1
    assert not created[0].disconnected
    pool.stop_reaper()


@pytest.mark.asyncio
async def test_both_timeouts_zero_disables_reaper():
    """Reaper doesn't start when both idle_timeout and max_age are disabled."""
    factory, created = make_fake_factory()
    pool = ClientPool(factory=factory, idle_timeout=0, max_age=0, max_age_jitter=0.0)
    await pool.acquire("s1")
    pool.start_reaper()
    assert pool._reaper_task is None  # reaper should not have started
    assert pool.size == 1


@pytest.mark.asyncio
async def test_disconnect_runs_in_worker_task():
    """Connect and disconnect both run in the worker task, avoiding cross-task errors."""
    connect_task_ids: list[int] = []
    disconnect_task_ids: list[int] = []

    class TaskTrackingClient:
        def __init__(self):
            connect_task_ids.append(id(asyncio.current_task()))
            self.disconnected = False
            self.connected = True

        async def disconnect(self):
            disconnect_task_ids.append(id(asyncio.current_task()))
            self.disconnected = True
            self.connected = False

    async def factory(session_name, sdk_session_id=None, fork_session=False):
        return TaskTrackingClient()

    pool = ClientPool(factory=factory)
    await pool.acquire("s1")
    await pool.release("s1")

    assert len(connect_task_ids) == 1
    assert len(disconnect_task_ids) == 1
    # Both operations ran in the same task (the worker)
    assert connect_task_ids[0] == disconnect_task_ids[0]
    await pool.shutdown()


@pytest.mark.asyncio
async def test_multiple_acquire_release_cycles_through_worker():
    """Multiple sequential acquire+release cycles work through the worker."""
    factory, created = make_fake_factory()
    pool = ClientPool(factory=factory)

    for i in range(5):
        client = await pool.acquire(f"s{i}")
        assert client is created[i]
        await pool.release(f"s{i}")
        assert created[i].disconnected

    assert pool.size == 0
    assert len(created) == 5
    await pool.shutdown()


@pytest.mark.asyncio
async def test_shutdown_stops_worker_task():
    """Shutdown cancels the worker task."""
    factory, _ = make_fake_factory()
    pool = ClientPool(factory=factory)
    await pool.acquire("s1")
    assert pool._worker_task is not None
    worker = pool._worker_task

    await pool.shutdown()
    assert worker.done()
    assert pool._worker_task is None


@pytest.mark.asyncio
async def test_cancel_scope_error_triggers_transport_close():
    """When disconnect raises cancel scope RuntimeError, transport.close() is called directly."""
    transport_closed = False

    class FakeTransport:
        async def close(self):
            nonlocal transport_closed
            transport_closed = True

    class FakeQuery:
        transport = FakeTransport()

    class CancelScopeClient:
        _query = FakeQuery()
        disconnected = False
        connected = True

        async def disconnect(self):
            raise RuntimeError("Attempted to exit cancel scope in a different task")

    async def factory(session_name, sdk_session_id=None, fork_session=False):
        return CancelScopeClient()

    pool = ClientPool(factory=factory)
    await pool.acquire("s1")
    await pool.release("s1")

    assert transport_closed, "transport.close() should be called as fallback"
    assert pool.size == 0
    await pool.shutdown()


@pytest.mark.asyncio
async def test_reaper_rotates_aged_client():
    factory, created = make_fake_factory()
    pool = ClientPool(
        factory=factory,
        idle_timeout=9999,   # idle timeout won't trigger
        max_age=0.05,        # very short max age for testing
        max_age_jitter=0.0,
    )
    await pool.acquire("s1")
    pool.start_reaper()

    await asyncio.sleep(0.2)

    assert pool.size == 0
    assert created[0].disconnected
    pool.stop_reaper()


@pytest.mark.asyncio
async def test_reaper_age_rotation_skips_active_session():
    factory, created = make_fake_factory()
    pool = ClientPool(
        factory=factory,
        idle_timeout=9999,
        max_age=0.05,
        max_age_jitter=0.0,
    )
    await pool.acquire("s1")
    pool.mark_active("s1")
    pool.start_reaper()

    await asyncio.sleep(0.2)

    assert pool.size == 1
    assert not created[0].disconnected
    pool.stop_reaper()


@pytest.mark.asyncio
async def test_max_age_zero_disables_rotation():
    factory, created = make_fake_factory()
    pool = ClientPool(
        factory=factory,
        idle_timeout=9999,
        max_age=0,
        max_age_jitter=0.0,
    )
    await pool.acquire("s1")
    pool.start_reaper()

    await asyncio.sleep(0.15)

    assert pool.size == 1
    assert not created[0].disconnected
    pool.stop_reaper()


@pytest.mark.asyncio
async def test_reaper_starts_with_zero_idle_timeout_and_positive_max_age():
    """Age rotation works even when idle timeout is disabled."""
    factory, created = make_fake_factory()
    pool = ClientPool(
        factory=factory,
        idle_timeout=0,
        max_age=0.05,
        max_age_jitter=0.0,
    )
    await pool.acquire("s1")
    pool.start_reaper()

    await asyncio.sleep(0.2)

    assert pool.size == 0
    assert created[0].disconnected
    pool.stop_reaper()


@pytest.mark.asyncio
async def test_reaper_handles_session_both_idle_and_aged():
    """No KeyError when a session is both idle-expired and age-expired."""
    factory, created = make_fake_factory()
    pool = ClientPool(
        factory=factory,
        idle_timeout=0.05,
        max_age=0.05,
        max_age_jitter=0.0,
    )
    await pool.acquire("s1")
    pool.start_reaper()

    await asyncio.sleep(0.2)

    assert pool.size == 0
    assert created[0].disconnected
    pool.stop_reaper()
