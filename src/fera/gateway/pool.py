"""Client pool for managing persistent SDK client lifecycles."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

ClientFactory = Callable[..., Awaitable[Any]]


@dataclass
class _WorkerCommand:
    """Command object for the lifecycle worker queue."""

    op: str
    session: str = ""
    client: Any = None
    sdk_session_id: str | None = None
    fork_session: bool = False
    future: asyncio.Future = field(default_factory=lambda: asyncio.get_event_loop().create_future())


async def _force_close_transport(client: Any) -> None:
    """Force-close a client's subprocess when graceful disconnect fails.

    When ``client.disconnect()`` raises a cancel-scope RuntimeError, the
    SDK's ``transport.close()`` never runs.  This function calls it
    directly, bypassing the anyio task group that can't cross task
    boundaries.  The transport's own close method suppresses internal
    cancel-scope issues and properly terminates the subprocess.
    """
    try:
        query = getattr(client, "_query", None)
        if query is None:
            return
        transport = getattr(query, "transport", None)
        if transport is None:
            return
        await transport.close()
    except Exception:
        log.debug("Force-close transport failed", exc_info=True)


class ClientPool:
    """Manages a pool of persistent SDK clients, one per session.

    All SDK lifecycle operations (connect/disconnect) are routed through a
    single dedicated worker task so that anyio cancel scopes created during
    connect are always closed in the same asyncio task.
    """

    def __init__(
        self,
        *,
        factory: ClientFactory,
        max_clients: int = 5,
        idle_timeout: float = 1800.0,
        max_age: float = 36000.0,
        max_age_jitter: float = 7200.0,
    ):
        self._factory = factory
        self._max_clients = max_clients
        self._idle_timeout = idle_timeout
        self._max_age = max_age
        self._max_age_jitter = max_age_jitter
        self._clients: dict[str, Any] = {}
        self._last_used: dict[str, float] = {}
        self._created_at: dict[str, float] = {}
        self._max_ages: dict[str, float] = {}
        self._active: set[str] = set()
        self._reaper_task: asyncio.Task | None = None
        candidates = [30.0]
        if idle_timeout > 0:
            candidates.append(idle_timeout / 2)
        if max_age > 0:
            candidates.append(max_age / 2)
        self._reaper_interval: float = min(candidates)
        self._queue: asyncio.Queue[_WorkerCommand] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None

    def _ensure_worker(self) -> None:
        """Start the lifecycle worker if not already running."""
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop())

    async def _worker_loop(self) -> None:
        """Process lifecycle commands sequentially in a single task."""
        try:
            while True:
                cmd = await self._queue.get()
                try:
                    if cmd.op == "connect":
                        client = await self._factory(
                            cmd.session, sdk_session_id=cmd.sdk_session_id,
                            fork_session=cmd.fork_session,
                        )
                        cmd.future.set_result(client)
                    elif cmd.op == "disconnect":
                        try:
                            await cmd.client.disconnect()
                        except RuntimeError as e:
                            if "cancel scope" in str(e):
                                log.warning(
                                    "Cross-task cancel scope for %s, "
                                    "closing transport directly",
                                    cmd.session,
                                )
                                await _force_close_transport(cmd.client)
                            else:
                                log.warning(
                                    "Failed to disconnect %s",
                                    cmd.session, exc_info=True,
                                )
                        except Exception:
                            log.warning(
                                "Failed to disconnect %s", cmd.session, exc_info=True,
                            )
                        cmd.future.set_result(None)
                except Exception as exc:
                    if not cmd.future.done():
                        cmd.future.set_exception(exc)
        except asyncio.CancelledError:
            pass

    @property
    def size(self) -> int:
        """Number of active clients in the pool."""
        return len(self._clients)

    def has_client(self, session_name: str) -> bool:
        """Check whether a pooled client exists for the given session."""
        return session_name in self._clients

    async def acquire(
        self, session_name: str, sdk_session_id: str | None = None,
        fork_session: bool = False,
    ) -> Any:
        """Get or create a client for a session."""
        if session_name in self._clients:
            self._last_used[session_name] = time.monotonic()
            return self._clients[session_name]

        if self._max_clients > 0 and len(self._clients) >= self._max_clients:
            await self._evict_lru()

        self._ensure_worker()
        cmd = _WorkerCommand(
            op="connect", session=session_name,
            sdk_session_id=sdk_session_id, fork_session=fork_session,
        )
        await self._queue.put(cmd)
        client = await cmd.future

        self._clients[session_name] = client
        self._last_used[session_name] = time.monotonic()
        self._created_at[session_name] = time.monotonic()
        self._max_ages[session_name] = self._max_age + random.uniform(0, self._max_age_jitter)
        log.info("Created pooled client for session %s (pool size: %d)", session_name, self.size)
        return client

    def mark_active(self, session_name: str) -> None:
        """Mark a session as mid-turn (protected from eviction and reaping)."""
        self._active.add(session_name)

    def mark_idle(self, session_name: str) -> None:
        """Mark a session as no longer mid-turn."""
        self._active.discard(session_name)

    async def _evict_lru(self) -> None:
        """Disconnect and remove the least-recently-used idle client."""
        eligible = {k: v for k, v in self._last_used.items() if k not in self._active}
        if not eligible:
            return
        lru_session = min(eligible, key=lambda k: eligible[k])
        log.info("Evicting LRU session %s to make room", lru_session)
        await self._disconnect(lru_session)

    async def release(self, session_name: str) -> None:
        """Remove and disconnect a client (e.g. after a subprocess crash)."""
        await self._disconnect(session_name)

    def start_reaper(self) -> None:
        """Start the background idle reaper task."""
        if self._idle_timeout <= 0 and self._max_age <= 0:
            return  # Reaping disabled
        if self._reaper_task is None:
            self._reaper_task = asyncio.create_task(self._reap_loop())

    def stop_reaper(self) -> None:
        """Stop the background idle reaper task."""
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            self._reaper_task = None

    async def _reap_loop(self) -> None:
        """Periodically disconnect idle clients past idle timeout or max age."""
        try:
            while True:
                await asyncio.sleep(self._reaper_interval)
                now = time.monotonic()
                if self._idle_timeout > 0:
                    expired = [
                        name
                        for name, ts in self._last_used.items()
                        if now - ts > self._idle_timeout and name not in self._active
                    ]
                    for name in expired:
                        log.info("Reaping idle session %s", name)
                        await self._disconnect(name)
                if self._max_age > 0:
                    aged = [
                        name
                        for name, created in self._created_at.items()
                        if now - created > self._max_ages.get(name, self._max_age)
                        and name not in self._active
                    ]
                    for name in aged:
                        created = self._created_at.get(name)
                        if created is None:
                            continue  # already removed by idle reaper above
                        age_hours = (now - created) / 3600
                        log.info("Rotating aged session %s (age=%.1fh)", name, age_hours)
                        await self._disconnect(name)
        except asyncio.CancelledError:
            pass

    async def shutdown(self) -> None:
        """Disconnect all clients and stop the worker. Called on gateway shutdown."""
        self.stop_reaper()
        sessions = list(self._clients.keys())
        for session_name in sessions:
            await self._disconnect(session_name)
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

    async def _disconnect(self, session_name: str) -> None:
        """Disconnect and remove a client from the pool."""
        client = self._clients.pop(session_name, None)
        self._last_used.pop(session_name, None)
        self._created_at.pop(session_name, None)
        self._max_ages.pop(session_name, None)
        self._active.discard(session_name)
        if client:
            self._ensure_worker()
            cmd = _WorkerCommand(op="disconnect", session=session_name, client=client)
            await self._queue.put(cmd)
            await cmd.future
