"""MemoryWriter — archivist turns for JIT and dream-cycle memory writes."""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path

from collections.abc import Awaitable, Callable

from claude_agent_sdk import ClaudeAgentOptions, query
from claude_agent_sdk.types import ResultMessage
from fera.config import local_date

log = logging.getLogger(__name__)

COMPACT_NOTIFICATION = "\U0001f9e0 Auto-compaction running \u2014 context window is being summarised."

# System prompt for all archivist turns — minimal context, file-writing focus.
_ARCHIVIST_SYSTEM_PROMPT = (
    "You are the Fera Memory Archivist. Your only job is to read conversation "
    "history and write structured memory files to the workspace. You have access "
    "to Read, Glob, and Write tools. Follow the user's instructions exactly. "
    "If nothing is worth saving, respond with SILENT_REPLY only."
)

_JIT_PROMPT_TEMPLATE = """\
Pre-compaction memory flush. Target date: {date}

The full conversation transcript is at: {transcript_path}

Instructions:
1. Read the transcript to identify durable facts worth preserving.
2. Use Glob to list {timeline_dir}/*.md to find existing files.
3. Find the highest existing number NNN in that directory.
   Write {timeline_dir}/NNN+1.md (zero-padded to 3 digits, e.g. 001.md).
   NEVER overwrite an existing file.
4. Include in the file: key decisions, technical details, work-in-progress state,
   user preferences discovered.
5. If nothing is worth preserving, respond SILENT_REPLY only.
   Otherwise respond: MEMORY_SAVED: {timeline_dir}/NNN+1.md
"""

_SYNTHESIS_PROMPT_TEMPLATE = """\
Daily synthesis for {date}.

Read all *.md files in {timeline_dir}/ (skip summary.md if it exists).
Write {timeline_dir}/summary.md with a consolidated summary covering:
- What was accomplished today
- Key decisions and their rationale
- Current state of ongoing projects
- Context that will help tomorrow's session

If no *.md files exist in {timeline_dir}/ (other than a pre-existing
summary.md), respond SILENT_REPLY only and do not create summary.md.
"""

_MEMORY_MD_PROMPT_TEMPLATE = """\
Long-term memory update for {date}.

Read {timeline_dir}/summary.md and the current {memory_md}.
Update {memory_md} only with facts that should appear in EVERY future system prompt.

Criteria — all three must be true before adding anything:
1. Omitting this fact would cause future sessions to make mistakes or miss
   critical context.
2. The fact is stable and won't change frequently.
3. It is not already captured in workspace persona or config files.

Do not add: project status, temporary state, or anything already implied
by other workspace files. Be ruthlessly selective.

If nothing merits addition, respond SILENT_REPLY only and do not modify {memory_md}.
"""


class MemoryWriter:
    """Spawns minimal archivist turns for JIT and dream-cycle memory writes."""

    def __init__(self, tz: str | None = None, model: str | None = None) -> None:
        self._tz = tz
        self._model = model

    @property
    def tz(self) -> str | None:
        return self._tz

    def rotate_transcript_file(self, path_str: str) -> str | None:
        """Rename a transcript file to archive it with a UTC timestamp suffix.

        Returns the path of the renamed file, or None if the file didn't exist.
        """
        path = Path(path_str)
        if not path.exists():
            return None
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        dest = path.with_stem(f"{path.stem}-{ts}")
        path.rename(dest)
        log.info("Rotated transcript %s", path.name)
        return str(dest)

    def make_pre_compact_hook(
        self,
        on_compact: Callable[[], Awaitable[None]] | None = None,
        transcript_path: str | None = None,
    ):
        """Return a PreCompact hook callback for use in ClaudeAgentOptions."""
        async def _hook(hook_input, matcher, context) -> dict:
            session_id = hook_input["session_id"]
            workspace = Path(hook_input["cwd"])
            effective_transcript = transcript_path or hook_input["transcript_path"]
            trigger = hook_input.get("trigger", "auto")
            target_date = local_date(self._tz)

            if on_compact and trigger == "auto":
                try:
                    await on_compact()
                except Exception:
                    log.exception(
                        "on_compact callback failed for session %s",
                        session_id,
                    )

            try:
                await self.archive_session(workspace, effective_transcript, target_date)
            except Exception:
                log.exception(
                    "JIT memory write failed for session %s (date=%s)",
                    session_id,
                    target_date,
                )
            return {}

        return _hook

    async def archive_session(
        self, workspace: Path, transcript_path: str, target_date: date,
    ) -> str | None:
        """Rotate the transcript, then write a timeline entry from it.

        Returns the archivist result string, or None if no transcript existed.

        Rotate-first ensures the transcript is frozen before the archivist reads
        it. If write_jit_memory fails, the rotated file is preserved for the
        next attempt.
        """
        rotated = self.rotate_transcript_file(transcript_path)
        if rotated is None:
            return None
        return await self.write_jit_memory(workspace, rotated, target_date)

    async def write_jit_memory(
        self, workspace: Path, transcript_path: str, target_date: date
    ) -> str:
        """Write a JIT memory file from a transcript before compaction."""
        # Ensure the timeline directory exists — the archivist has no Bash
        # tool to mkdir, so Write calls would fail for new date directories.
        timeline_dir = workspace / "memory" / "timeline" / target_date.isoformat()
        timeline_dir.mkdir(parents=True, exist_ok=True)

        prompt = _JIT_PROMPT_TEMPLATE.format(
            date=target_date.isoformat(),
            transcript_path=transcript_path,
            timeline_dir=timeline_dir,
        )
        result = await self._spawn_archivist_turn(workspace, prompt)
        log.info("JIT memory write result: %s", result[:80] if result else "(empty)")
        return result

    async def write_synthesis(self, workspace: Path, target_date: date) -> None:
        """Synthesise the day's timeline files into summary.md."""
        timeline_dir = workspace / "memory" / "timeline" / target_date.isoformat()
        prompt = _SYNTHESIS_PROMPT_TEMPLATE.format(
            date=target_date.isoformat(),
            timeline_dir=timeline_dir,
        )
        result = await self._spawn_archivist_turn(workspace, prompt)
        log.info("Synthesis result: %s", result[:80] if result else "(empty)")

    async def update_memory_md(self, workspace: Path, target_date: date) -> None:
        """Selectively update MEMORY.md with facts worth keeping permanently."""
        timeline_dir = workspace / "memory" / "timeline" / target_date.isoformat()
        memory_md = workspace / "MEMORY.md"
        prompt = _MEMORY_MD_PROMPT_TEMPLATE.format(
            date=target_date.isoformat(),
            timeline_dir=timeline_dir,
            memory_md=memory_md,
        )
        result = await self._spawn_archivist_turn(workspace, prompt)
        log.info("MEMORY.md update result: %s", result[:80] if result else "(empty)")

    async def _spawn_archivist_turn(self, workspace: Path, user_prompt: str) -> str:
        """Spawn a one-shot archivist query and return its text response.

        Uses ResultMessage.result (the final answer) rather than intermediate
        AssistantMessage text blocks, which contain tool-use reasoning that
        would leak into user-visible status messages.
        """
        options = ClaudeAgentOptions(
            model=self._model,
            system_prompt=_ARCHIVIST_SYSTEM_PROMPT,
            allowed_tools=["Read", "Glob", "Write"],
            permission_mode="bypassPermissions",
            cwd=str(workspace),
            stderr=lambda line: log.warning("archivist stderr: %s", line),
        )
        result = ""
        async for msg in query(prompt=user_prompt, options=options):
            if isinstance(msg, ResultMessage) and msg.result:
                result = msg.result.strip()
        return result
