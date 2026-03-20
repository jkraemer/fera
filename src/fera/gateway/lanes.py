from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager


class LaneManager:
    """Per-session serialization using asyncio locks."""

    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {}
        self._queues: dict[str, list[tuple[str, str]]] = {}

    def is_locked(self, session: str) -> bool:
        """Check if a session lane is currently held (non-blocking)."""
        lock = self._locks.get(session)
        return lock is not None and lock.locked()

    def enqueue(self, session: str, text: str, source: str) -> None:
        """Add a message to the session's pending queue."""
        self._queues.setdefault(session, []).append((text, source))

    def drain_queue(self, session: str) -> list[tuple[str, str]]:
        """Atomically drain and return all queued messages for a session."""
        return self._queues.pop(session, [])

    @asynccontextmanager
    async def acquire(self, session: str):
        """Acquire exclusive access to a session's lane."""
        if session not in self._locks:
            self._locks[session] = asyncio.Lock()
        async with self._locks[session]:
            yield
