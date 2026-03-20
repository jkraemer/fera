from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_RECORDED_EVENTS = frozenset({
    "user.message",
    "agent.text",
    "agent.tool_use",
    "agent.tool_result",
    "agent.done",
    "agent.error",
    "agent.compact",
})


class TranscriptLogger:
    """Records conversational events to per-session JSONL transcript files."""

    def __init__(self, transcripts_dir: Path):
        self._dir = Path(transcripts_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def transcript_path(self, session_id: str) -> Path:
        """Return the transcript file path for a session ID.

        Composite IDs ("agent/name") → dir/agent/name.jsonl
        Bare names ("name") → dir/name.jsonl (backward compat for existing data)
        """
        if "/" in session_id:
            agent, _, name = session_id.partition("/")
            return self._dir / agent / f"{name}.jsonl"
        return self._dir / f"{session_id}.jsonl"

    async def record_event(self, event: dict) -> None:
        """Bus subscriber — writes relevant events to the session transcript."""
        event_type = event.get("event", "")
        session = event.get("session", "")
        if not session or session == "$system" or event_type not in _RECORDED_EVENTS:
            return

        data = event.get("data", {})
        ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds")

        if event_type == "user.message":
            entry = {
                "ts": ts, "type": "user",
                "text": data.get("text", ""),
                "source": data.get("source", ""),
            }
        elif event_type == "agent.text":
            entry = {
                "ts": ts, "type": "agent",
                "text": data.get("text", ""),
                "turn_source": event.get("turn_source", ""),
            }
        elif event_type == "agent.tool_use":
            entry = {
                "ts": ts, "type": "tool_use",
                "id": data.get("id", ""),
                "name": data.get("name", ""),
                "input": data.get("input"),
            }
        elif event_type == "agent.tool_result":
            entry = {
                "ts": ts, "type": "tool_result",
                "tool_use_id": data.get("tool_use_id", ""),
                "content": data.get("content"),
                "is_error": data.get("is_error", False),
            }
        elif event_type == "agent.done":
            entry = {
                "ts": ts, "type": "done",
                "duration_ms": data.get("duration_ms"),
                "duration_api_ms": data.get("duration_api_ms"),
                "model": data.get("model"),
                "input_tokens": data.get("input_tokens"),
                "output_tokens": data.get("output_tokens"),
                "cache_creation_input_tokens": data.get("cache_creation_input_tokens"),
                "cache_read_input_tokens": data.get("cache_read_input_tokens"),
                "cost_usd": data.get("cost_usd"),
                "num_turns": data.get("num_turns"),
            }
        elif event_type == "agent.compact":
            entry = {
                "ts": ts, "type": "compact",
                "trigger": data.get("trigger"),
                "pre_tokens": data.get("pre_tokens"),
            }
        elif event_type == "agent.error":
            entry = {
                "ts": ts, "type": "error",
                "error": data.get("error", ""),
            }
        else:
            return

        try:
            await asyncio.to_thread(self._write, session, entry)
        except Exception:
            log.exception("TranscriptLogger write error for session %s", session)

    def _write(self, session: str, entry: dict) -> None:
        path = self.transcript_path(session)
        if not path.resolve().is_relative_to(self._dir.resolve()):
            log.warning("Rejected unsafe session name: %s", session)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    async def load_async(self, session: str, limit: int = 500) -> list[dict]:
        """Async wrapper around load for use in async contexts."""
        return await asyncio.to_thread(self.load, session, limit)

    def load(self, session: str, limit: int = 500) -> list[dict]:
        """Return the last `limit` transcript entries for the session.

        Aggregates rotated siblings (e.g. mysession-20260222T073000.jsonl)
        in chronological order before the current file.
        """
        path = self.transcript_path(session)
        if not path.resolve().is_relative_to(self._dir.resolve()):
            log.warning("Rejected unsafe session name: %s", session)
            return []

        siblings = sorted(path.parent.glob(
            f"{path.stem}-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9].jsonl"
        ))
        all_files = siblings + ([path] if path.exists() else [])

        entries = []
        for f in all_files:
            if not f.resolve().is_relative_to(self._dir.resolve()):
                log.warning("Skipping file outside transcripts dir: %s", f)
                continue
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        log.warning("Skipping malformed line in transcript for session %s", session)

        return entries[-limit:]
