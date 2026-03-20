from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

from fera.adapters.bus import EventBus
from fera.config import DEFAULT_AGENT, local_date
from fera.gateway.protocol import make_event

if TYPE_CHECKING:
    from fera.gateway.memory_writer import MemoryWriter
    from fera.gateway.runner import AgentRunner
    from fera.gateway.sessions import SessionManager
    from fera.gateway.stats import SessionStats
    from fera.gateway.transcript import TranscriptLogger

log = logging.getLogger(__name__)

# Tool name -> input key to extract for display summaries.
_TOOL_INPUT_KEYS: dict[str, str] = {
    "Bash": "command",
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "WebSearch": "query",
    "WebFetch": "url",
    "Grep": "pattern",
    "Glob": "pattern",
    "Skill": "skill",
    "Task": "description",
}

_TOOL_SUMMARY_MAX = 80

# Tool name -> icon for display. Unknown tools fall back to 🔧.
_TOOL_ICONS: dict[str, str] = {
    "Bash": "\U0001f5a5\ufe0f",  # 🖥️
    "Read": "\U0001f4d6",       # 📖
    "Write": "\u270f\ufe0f",    # ✏️
    "Edit": "\u270f\ufe0f",     # ✏️
    "WebSearch": "\U0001f50d",  # 🔍
    "WebFetch": "\U0001f310",   # 🌐
    "Grep": "\U0001f50e",       # 🔎
    "Glob": "\U0001f50e",       # 🔎
    "Task": "\u2705",            # ✅
}

_DEFAULT_TOOL_ICON = "\U0001f527"  # 🔧


def format_tool_summary(name: str, tool_input: dict | None) -> str:
    """Format a tool call as a blockquote line for display in chat.

    Returns a string like ``> 💻 Bash · grep -r 'foo' /path``.
    """
    icon = _TOOL_ICONS.get(name, _DEFAULT_TOOL_ICON)

    if not tool_input:
        return f"> {icon} {name}"

    key = _TOOL_INPUT_KEYS.get(name)
    if key and key in tool_input:
        value = str(tool_input[key])
    else:
        # Fallback: first string value in the input dict
        value = ""
        for v in tool_input.values():
            if isinstance(v, str) and v:
                value = v
                break

    if not value:
        return f"> {icon} {name}"

    # Bash: use first line only (commands can be multi-line)
    if name == "Bash" and "\n" in value:
        value = value.split("\n", 1)[0]

    # Truncate
    if len(value) > _TOOL_SUMMARY_MAX:
        value = value[: _TOOL_SUMMARY_MAX - 1] + "…"

    return f"> {icon} {name} · {value}"


@dataclass
class AdapterStatus:
    connected: bool
    detail: str


class ChannelAdapter(ABC):
    """Base class for all channel adapters."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def start(self, context: AdapterContext) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    def status(self) -> AdapterStatus: ...

    @property
    def agent_name(self) -> str:
        """The agent this adapter belongs to. Subclasses override to set a specific agent."""
        return DEFAULT_AGENT


class AdapterContext:
    """Interface provided to adapters by the gateway."""

    def __init__(
        self,
        bus: EventBus,
        runner: AgentRunner,
        sessions: SessionManager,
        stats: SessionStats | None = None,
        agent_name: str = DEFAULT_AGENT,
        *,
        memory_writer: MemoryWriter | None = None,
        transcript_logger: TranscriptLogger | None = None,
        fera_home: Path | None = None,
    ):
        self._bus = bus
        self._runner = runner
        self._sessions = sessions
        self._stats = stats
        self._agent_name = agent_name
        self._memory_writer = memory_writer
        self._transcript_logger = transcript_logger
        self._fera_home = fera_home

    def _qualify_session(self, session: str) -> str:
        """Qualify a bare session name with this adapter's agent name."""
        if "/" not in session:
            return f"{self._agent_name}/{session}"
        return session

    def subscribe(self, session: str, callback: Callable[[dict], Awaitable[None]]) -> None:
        self._bus.subscribe(self._qualify_session(session), callback)

    def unsubscribe(self, session: str, callback: Callable) -> None:
        self._bus.unsubscribe(self._qualify_session(session), callback)

    def list_sessions(self) -> list[dict]:
        return self._sessions.sessions_for_agent(self._agent_name)

    def session_stats(self, session: str) -> dict:
        if self._stats:
            return self._stats.get(self._qualify_session(session))
        return {}

    def load_models(self) -> dict[str, str]:
        """Load available model aliases from models.json."""
        from fera.config import load_models
        return load_models(self._fera_home)

    async def set_model(self, session: str, model: str) -> None:
        """Switch model on a live pooled session."""
        await self._runner.set_model(self._qualify_session(session), model)

    def session_exists(self, session: str) -> bool:
        """Check whether a session already exists (has been created before)."""
        return self._sessions.get(self._qualify_session(session)) is not None

    async def send_message(
        self, session: str, text: str, source: str = "",
        fork_from: str | None = None,
    ) -> None:
        """Trigger an agent turn and publish resulting events on the bus.

        When *fork_from* is set, the first turn of the new session will fork
        the conversation history of the named parent session (SDK fork).
        """
        session_info = self._sessions.get_or_create(self._qualify_session(session))
        session = session_info["id"]
        qualified_fork = self._qualify_session(fork_from) if fork_from else None
        if source:
            self._sessions.set_last_inbound_adapter(session, source)
        await self._bus.publish(make_event("user.message", session=session, data={"text": text, "source": source}))
        try:
            async for event in self._runner.run_turn(
                session, text, source=source, fork_from=qualified_fork,
            ):
                await self._bus.publish(event)
        except Exception as e:
            error_event = make_event(
                "agent.error", session=session, data={"error": str(e)},
            )
            await self._bus.publish(error_event)

    async def answer_question(self, session: str, question_id: str, answers: dict) -> None:
        """Resolve a pending AskUserQuestion with user answers."""
        await self._runner.answer_question(self._qualify_session(session), question_id, answers)

    def is_session_busy(self, session: str) -> bool:
        """Check if a turn is currently running for this session."""
        return self._runner.active_session(self._qualify_session(session))

    async def interrupt_session(self, session: str) -> bool:
        """Interrupt the active agent turn for a session.

        Returns True if there was an active turn to interrupt.
        """
        qualified = self._qualify_session(session)
        if not self._runner.active_session(qualified):
            return False
        await self._runner.interrupt(qualified)
        return True

    async def clear_session(self, session: str) -> str:
        """Archive session memory (if configured) and clear the agent session.

        Returns a human-readable status string describing what happened.
        """
        qualified = self._qualify_session(session)
        status = ""
        if self._memory_writer and self._transcript_logger:
            session_info = self._sessions.get(qualified)
            if session_info:
                workspace = Path(session_info["workspace_dir"])
                transcript_path = self._transcript_logger.transcript_path(qualified)
                try:
                    result = await self._memory_writer.archive_session(
                        workspace, str(transcript_path),
                        local_date(self._memory_writer.tz),
                    )
                    if result is None:
                        status = "No transcript to archive"
                    else:
                        status = f"Archived ({result})"
                except Exception as exc:
                    log.exception("Memory archive failed for %s; clearing anyway", qualified)
                    status = f"Archive failed: {exc}"
        await self._runner.clear_session(qualified)
        return status
