from __future__ import annotations

import logging
from collections import defaultdict
from typing import Awaitable, Callable

log = logging.getLogger(__name__)

Callback = Callable[[dict], Awaitable[None]]


class EventBus:
    """Internal pub/sub for session-scoped events."""

    def __init__(self):
        self._subscribers: dict[str, list[Callback]] = defaultdict(list)

    def subscribe(self, session: str, callback: Callback) -> None:
        self._subscribers[session].append(callback)

    def unsubscribe(self, session: str, callback: Callback) -> None:
        subs = self._subscribers.get(session)
        if subs:
            try:
                subs.remove(callback)
            except ValueError:
                pass

    async def publish(self, event: dict) -> None:
        session = event.get("session", "")
        targets = list(self._subscribers.get(session, []))
        targets += list(self._subscribers.get("*", []))
        for cb in targets:
            try:
                await cb(event)
            except Exception:
                log.exception("EventBus callback error")
