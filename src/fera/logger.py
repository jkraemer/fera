from __future__ import annotations

import json
import logging
from collections.abc import Callable, Coroutine
from datetime import date, datetime, timezone
from pathlib import Path
from typing import IO

log = logging.getLogger(__name__)

_logger: EventLogger | None = None


def init_logger(log_dir: Path) -> EventLogger:
    global _logger
    _logger = EventLogger(log_dir)
    return _logger


def get_logger() -> EventLogger | None:
    return _logger


class EventLogger:
    def __init__(self, log_dir: Path):
        self._log_dir = Path(log_dir)
        self._current_date: date | None = None
        self._current_file: "IO[str] | None" = None
        self._broadcast: Callable[[dict], Coroutine] | None = None

    def set_broadcast(self, callback: Callable[[dict], Coroutine]) -> None:
        self._broadcast = callback

    def close(self) -> None:
        if self._current_file:
            self._current_file.close()
            self._current_file = None

    async def log(
        self,
        event: str,
        *,
        level: str = "info",
        session: str | None = None,
        **data,
    ) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": level,
            "event": event,
            "session": session,
            "data": data,
        }
        self._write(entry)
        if self._broadcast:
            try:
                await self._broadcast(entry)
            except Exception:
                log.exception("EventLogger broadcast error")

    def _write(self, entry: dict) -> None:
        today = date.today()
        if today != self._current_date:
            if self._current_file:
                self._current_file.close()
            path = (
                self._log_dir
                / str(today.year)
                / f"{today.month:02d}"
                / f"{today}.jsonl"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            self._current_file = path.open("a", encoding="utf-8")
            self._current_date = today
        try:
            self._current_file.write(json.dumps(entry) + "\n")
            self._current_file.flush()
        except Exception:
            log.exception("EventLogger write error")
