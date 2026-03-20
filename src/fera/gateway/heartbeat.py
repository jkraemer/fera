"""Heartbeat scheduler — periodic proactive agent turns."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from fera.logger import get_logger

log = logging.getLogger(__name__)

INBOX_SUBDIR = Path("inbox") / "heartbeat"

HEARTBEAT_INSTRUCTION = (
    "Review your workspace HEARTBEAT.md and process any pending tasks listed there. "
    "If nothing requires the user's attention, reply with just 'HEARTBEAT_OK'. "
    "If something needs attention, report it."
)


def is_active_hours(active_hours: str, *, now: time | None = None, tz: str | None = None) -> bool:
    """Check if current local time is within the active hours window.

    Format: "HH:MM-HH:MM" (start inclusive, end exclusive).
    Handles midnight wrapping (e.g. "22:00-06:00").
    If tz is provided (IANA timezone name), the current time is resolved in
    that timezone; otherwise the system local time is used.
    """
    start_s, end_s = active_hours.split("-")
    start = time.fromisoformat(start_s)
    end = time.fromisoformat(end_s)
    if now is None:
        if tz:
            now = datetime.now(ZoneInfo(tz)).time()
        else:
            now = datetime.now().time()

    if start <= end:
        return start <= now < end
    # Wraps midnight
    return now >= start or now < end


def has_heartbeat_content(workspace: Path) -> bool:
    """Check if HEARTBEAT.md has actionable (non-comment, non-blank) content."""
    hb = workspace / "HEARTBEAT.md"
    if not hb.is_file():
        return False
    for line in hb.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return True
    return False


def is_heartbeat_ok(events: list[dict]) -> bool:
    """Check if buffered heartbeat events indicate a silent response.

    After ``translate_message`` strips silent markers and drops empty text
    blocks, a heartbeat is considered silent when no ``agent.text`` events
    remain.
    """
    return not any(
        e.get("event") == "agent.text" and e.get("data", {}).get("text")
        for e in events
    )


def archive_inbox_files(workspace: Path, files: list[Path]) -> None:
    """Move processed inbox files to inbox/heartbeat/.processed/ with timestamp prefix."""
    if not files:
        return
    processed = workspace / INBOX_SUBDIR / ".processed"
    processed.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    for f in files:
        f.rename(processed / f"{stamp}_{f.name}")


def _format_local_time(tz: str) -> str:
    """Format current time in the given IANA timezone for the heartbeat prompt."""
    now = datetime.now(ZoneInfo(tz))
    # e.g. "Saturday, February 28, 19:00"
    formatted = now.strftime("%A, %B %-d, %H:%M")
    return f"In your user's time zone it is now {formatted}."


def build_instruction(has_heartbeat: bool, inbox_files: list[Path], *, tz: str | None = None) -> str:
    """Build the heartbeat turn instruction based on available content."""
    parts = []

    if tz:
        parts.append(_format_local_time(tz))

    if has_heartbeat:
        parts.append(HEARTBEAT_INSTRUCTION)

    if inbox_files:
        names = ", ".join(f.name for f in inbox_files)
        parts.append(
            f"You have {len(inbox_files)} file(s) in your inbox "
            f"(workspace/{INBOX_SUBDIR}/): {names}. "
            "Process them with the heartbeat-inbox skill."
        )

    if not has_heartbeat and inbox_files:
        parts.insert(1 if tz else 0, "You are performing a periodic check.")

    return " ".join(parts)


def list_inbox_files(workspace: Path) -> list[Path]:
    """List non-hidden files in workspace/inbox/heartbeat/ sorted by name."""
    inbox = workspace / INBOX_SUBDIR
    if not inbox.is_dir():
        return []
    return sorted(
        p for p in inbox.iterdir()
        if p.is_file() and not p.name.startswith(".")
    )


class HeartbeatScheduler:
    """Periodically triggers heartbeat turns on the agent's default session."""

    def __init__(
        self,
        *,
        config: dict,
        runner,
        bus,
        lanes,
        workspace: Path,
        sessions=None,
    ):
        self._config = config
        self._runner = runner
        self._bus = bus
        self._lanes = lanes
        self._workspace = workspace
        self._sessions = sessions
        self._task: asyncio.Task | None = None

    async def tick(self) -> str:
        """Run one heartbeat cycle. Returns a status string for logging."""
        session = self._config["session"]

        if not is_active_hours(self._config["active_hours"], tz=self._config.get("timezone")):
            return "skipped:inactive_hours"

        inbox_files = list_inbox_files(self._workspace)
        has_heartbeat = has_heartbeat_content(self._workspace)

        if not inbox_files and not has_heartbeat:
            return "skipped:no_content"

        if self._lanes.is_locked(session):
            return "skipped:session_busy"

        instruction = build_instruction(has_heartbeat, inbox_files, tz=self._config.get("timezone"))

        events = []
        try:
            async for event in self._runner.run_turn(
                session, instruction, source="heartbeat",
            ):
                events.append(event)
        except Exception:
            log.exception("Heartbeat turn failed for session %s", session)
            return "error"

        # Archive inbox files only after a successful turn
        if inbox_files:
            archive_inbox_files(self._workspace, inbox_files)

        if is_heartbeat_ok(events):
            text_count = sum(1 for e in events if e.get("event") == "agent.text")
            log.info("Heartbeat suppressed (%d text events)", text_count)
            return "ok"

        # Determine target adapter for delivery
        target = None
        if self._sessions:
            session_info = self._sessions.get(session)
            if session_info:
                target = session_info.get("last_inbound_adapter")

        # Suppress tool use/result events — heartbeat turns are internal;
        # only the final agent text output should be visible in adapters.
        _TOOL_EVENT_TYPES = {"agent.tool_use", "agent.tool_result"}
        for event in events:
            if event.get("event") in _TOOL_EVENT_TYPES:
                continue
            if target:
                event["target_adapter"] = target
            await self._bus.publish(event)
        return "alert"

    async def _loop(self) -> None:
        interval = self._config["interval_minutes"] * 60
        while True:
            await asyncio.sleep(interval)
            try:
                result = await self.tick()
                if logger := get_logger():
                    await logger.log("heartbeat.tick", result=result,
                                     session=self._config["session"])
                log.info("Heartbeat tick: %s", result)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Heartbeat loop error")

    def start(self) -> None:
        if not self._config.get("enabled"):
            log.info("Heartbeat disabled in config")
            return
        self._task = asyncio.create_task(self._loop())
        self._task.add_done_callback(self._on_task_done)
        log.info(
            "Heartbeat started: every %d min, session=%s, hours=%s",
            self._config["interval_minutes"],
            self._config["session"],
            self._config["active_hours"],
        )

    def _on_task_done(self, task: asyncio.Task) -> None:
        """Restart the heartbeat loop if it died unexpectedly."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            log.error("Heartbeat task died unexpectedly, restarting", exc_info=exc)
            self._task = asyncio.create_task(self._loop())
            self._task.add_done_callback(self._on_task_done)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
