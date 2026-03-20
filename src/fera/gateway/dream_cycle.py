"""Dream cycle scheduler — nightly memory synthesis for each agent."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path

from fera.config import local_date, local_now, workspace_dir
from fera.logger import get_logger

log = logging.getLogger(__name__)


class DreamCycleScheduler:
    """Nightly per-agent memory write, synthesis, and MEMORY.md update.

    At the configured wall-clock time each day, runs a three-phase cycle for
    each configured agent:
      Phase 1: Archive transcript + clear for each session with active SDK state
      Phase 2: Synthesis of day's timeline files into summary.md
      Phase 3: Selective update of MEMORY.md
    """

    def __init__(
        self,
        *,
        config: dict,
        runner,
        sessions,
        memory_writer,
        lanes,
        fera_home: Path,
        transcript_logger=None,
    ):
        self._config = config
        self._runner = runner
        self._sessions = sessions
        self._memory_writer = memory_writer
        self._lanes = lanes
        self._fera_home = fera_home
        self._transcript_logger = transcript_logger
        self._task: asyncio.Task | None = None

    async def tick(self) -> None:
        """Run the dream cycle for all configured agents."""
        tz = self._config.get("timezone")
        target_date = local_date(tz)
        for agent in self._config.get("agents", []):
            try:
                await self._run_agent_cycle(agent, target_date)
            except Exception:
                log.exception("Dream cycle error for agent %s", agent)

    async def _run_agent_cycle(self, agent: str, target_date: date) -> None:
        ws = workspace_dir(agent, self._fera_home)

        # Phase 1: archive transcript + clear per active session
        for session_info in self._sessions.sessions_for_agent(agent):
            sdk_session_id = session_info.get("sdk_session_id")
            if not sdk_session_id:
                continue
            session_id = session_info["id"]
            await self._process_session(session_id, ws, target_date)

        # Phase 2: synthesis
        try:
            await self._memory_writer.write_synthesis(ws, target_date)
        except Exception:
            log.exception(
                "Dream cycle synthesis failed for agent %s — skipping MEMORY.md update",
                agent,
            )
            return

        # Phase 3: MEMORY.md update (only if synthesis succeeded)
        try:
            await self._memory_writer.update_memory_md(ws, target_date)
        except Exception:
            log.exception("Dream cycle MEMORY.md update failed for agent %s", agent)

    async def _process_session(
        self, session_id: str, workspace: Path, target_date: date,
    ) -> None:
        """Archive transcript and optionally clear the session."""
        if self._transcript_logger:
            transcript_path = str(self._transcript_logger.transcript_path(session_id))
            try:
                await self._memory_writer.archive_session(
                    workspace, transcript_path, target_date,
                )
            except Exception:
                log.exception("Archive failed for session %s", session_id)

        if self._lanes.is_locked(session_id):
            log.info(
                "Session %s busy — archived transcript but skipping clear",
                session_id,
            )
            return

        await self._runner.clear_session(session_id)
        log.info("Dream cycle: cleared session %s", session_id)

    async def _loop(self) -> None:
        while True:
            await self._sleep_until_next_tick()
            try:
                if logger := get_logger():
                    await logger.log(
                        "dream_cycle.start",
                        agents=self._config.get("agents", []),
                    )
                await self.tick()
                if logger := get_logger():
                    await logger.log("dream_cycle.done")
                log.info("Dream cycle complete")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Dream cycle loop error")

    async def _sleep_until_next_tick(self) -> None:
        h, m = self._config["time"].split(":")
        target = time(int(h), int(m))
        tz_name = self._config.get("timezone")
        now = local_now(tz_name)
        tz = now.tzinfo
        next_tick = datetime.combine(now.date(), target, tzinfo=tz)
        if next_tick <= now:
            next_tick = datetime.combine(now.date() + timedelta(days=1), target, tzinfo=tz)
        await asyncio.sleep((next_tick - now).total_seconds())

    def start(self) -> None:
        if not self._config.get("enabled"):
            log.info("Dream cycle disabled in config")
            return
        self._task = asyncio.create_task(self._loop())
        self._task.add_done_callback(self._on_task_done)
        log.info(
            "Dream cycle started: time=%s, agents=%s",
            self._config["time"],
            self._config.get("agents", []),
        )

    def _on_task_done(self, task: asyncio.Task) -> None:
        """Restart the dream cycle loop if it died unexpectedly."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            log.error("Dream cycle task died unexpectedly, restarting", exc_info=exc)
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
