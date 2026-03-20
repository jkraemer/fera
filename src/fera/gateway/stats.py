from __future__ import annotations

import time
from typing import Any

_DEFAULT_CONTEXT_SIZE = 200_000


class SessionStats:
    """In-memory accumulator for per-session statistics."""

    def __init__(self):
        self._sessions: dict[str, dict[str, Any]] = {}

    def _ensure(self, session: str) -> dict[str, Any]:
        if session not in self._sessions:
            self._sessions[session] = {
                "turns": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "model": None,
                "last_cost_usd": None,
                "total_cost_usd": 0.0,
                "last_duration_ms": None,
                "compactions": 0,
                "last_active": None,
                "context_pct": None,
            }
        return self._sessions[session]

    def record_turn(self, session: str, data: dict) -> None:
        s = self._ensure(session)
        input_t = data.get("input_tokens") or 0
        output_t = data.get("output_tokens") or 0
        cache_creation = data.get("cache_creation_input_tokens") or 0
        cache_read = data.get("cache_read_input_tokens") or 0

        s["turns"] += 1
        s["total_input_tokens"] += input_t
        s["total_output_tokens"] += output_t
        s["model"] = data.get("model") or s["model"]
        s["last_cost_usd"] = data.get("cost_usd")
        s["total_cost_usd"] += data.get("cost_usd") or 0
        s["last_duration_ms"] = data.get("duration_ms")
        s["last_active"] = time.time()

        # Context % based on sum of all input token types
        context_used = input_t + cache_creation + cache_read
        if context_used > 0:
            s["context_pct"] = round(context_used / _DEFAULT_CONTEXT_SIZE * 100, 1)

    def record_compact(self, session: str, pre_tokens: int | None = None) -> None:
        s = self._ensure(session)
        s["compactions"] += 1

    def get(self, session: str) -> dict[str, Any]:
        return dict(self._ensure(session))

    def get_all(self) -> dict[str, dict[str, Any]]:
        return {name: dict(data) for name, data in self._sessions.items()}
