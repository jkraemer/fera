import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from fera.adapters.base import ChannelAdapter, AdapterContext, AdapterStatus, format_tool_summary
from fera.adapters.bus import EventBus


def test_adapter_status_fields():
    status = AdapterStatus(connected=True, detail="polling")
    assert status.connected is True
    assert status.detail == "polling"


def test_channel_adapter_cannot_be_instantiated():
    with pytest.raises(TypeError):
        ChannelAdapter()


def test_channel_adapter_subclass_must_implement_all():
    class Incomplete(ChannelAdapter):
        @property
        def name(self):
            return "test"
    with pytest.raises(TypeError):
        Incomplete()


@pytest.mark.asyncio
async def test_adapter_context_subscribe_wraps_bus():
    bus = EventBus()
    runner = AsyncMock()
    sessions = AsyncMock()
    ctx = AdapterContext(bus=bus, runner=runner, sessions=sessions)

    received = []
    async def cb(event):
        received.append(event)

    ctx.subscribe("main/s1", cb)
    await bus.publish({"type": "event", "session": "main/s1", "event": "agent.text"})
    assert len(received) == 1

    ctx.unsubscribe("main/s1", cb)
    await bus.publish({"type": "event", "session": "main/s1", "event": "agent.done"})
    assert len(received) == 1


def test_adapter_context_list_sessions_scoped_to_agent():
    bus = EventBus()
    runner = MagicMock()
    sessions = MagicMock()
    sessions.sessions_for_agent.return_value = [{"name": "default", "agent": "main"}]
    ctx = AdapterContext(bus=bus, runner=runner, sessions=sessions, agent_name="main")
    result = ctx.list_sessions()
    assert result == [{"name": "default", "agent": "main"}]
    sessions.sessions_for_agent.assert_called_once_with("main")


@pytest.mark.asyncio
async def test_send_message_normalises_bare_session_to_composite():
    """send_message("default", ...) must publish with session="main/default"."""
    bus = EventBus()
    runner = MagicMock()
    async def empty_run_turn(*a, **kw):
        return
        yield
    runner.run_turn = empty_run_turn
    sessions = MagicMock()
    sessions.get_or_create.return_value = {"id": "main/default", "name": "default", "agent": "main"}
    ctx = AdapterContext(bus=bus, runner=runner, sessions=sessions)

    received = []
    async def cb(event):
        received.append(event)
    bus.subscribe("*", cb)

    await ctx.send_message("default", "hi")

    assert received[0]["session"] == "main/default"


@pytest.mark.asyncio
async def test_send_message_publishes_user_message_event_first():
    bus = EventBus()
    runner = MagicMock()
    async def empty_run_turn(*a, **kw):
        return
        yield  # makes it an async generator
    runner.run_turn = empty_run_turn
    sessions = MagicMock()
    sessions.get_or_create.return_value = {"id": "main/default", "name": "default", "agent": "main"}
    ctx = AdapterContext(bus=bus, runner=runner, sessions=sessions)

    received = []
    async def cb(event):
        received.append(event)
    bus.subscribe("*", cb)

    await ctx.send_message("default", "hello from telegram")

    assert received[0]["event"] == "user.message"
    assert received[0]["session"] == "main/default"
    assert received[0]["data"]["text"] == "hello from telegram"
    assert received[0]["data"]["source"] == ""


@pytest.mark.asyncio
async def test_send_message_passes_source_in_event():
    bus = EventBus()
    runner = MagicMock()
    async def empty_run_turn(*a, **kw):
        return
        yield
    runner.run_turn = empty_run_turn
    sessions = MagicMock()
    sessions.get_or_create.return_value = {"id": "main/default", "name": "default", "agent": "main"}
    ctx = AdapterContext(bus=bus, runner=runner, sessions=sessions)

    received = []
    async def cb(event):
        received.append(event)
    bus.subscribe("*", cb)

    await ctx.send_message("default", "hi", source="telegram")

    assert received[0]["data"]["source"] == "telegram"


@pytest.mark.asyncio
async def test_send_message_tracks_last_inbound_adapter(tmp_path):
    """send_message records which adapter sent the message on the session."""
    from fera.gateway.sessions import SessionManager

    bus = EventBus()
    sessions = SessionManager(tmp_path / "sessions.json", fera_home=tmp_path)
    runner = MagicMock()

    async def empty_run_turn(*a, **kw):
        return
        yield

    runner.run_turn = empty_run_turn

    ctx = AdapterContext(bus=bus, runner=runner, sessions=sessions, agent_name="main")
    await ctx.send_message("dm-alex", "hello", source="telegram")

    info = sessions.get("main/dm-alex")
    assert info["last_inbound_adapter"] == "telegram"


@pytest.mark.asyncio
async def test_send_message_does_not_track_adapter_without_source(tmp_path):
    """send_message without source does not set last_inbound_adapter."""
    from fera.gateway.sessions import SessionManager

    bus = EventBus()
    sessions = SessionManager(tmp_path / "sessions.json", fera_home=tmp_path)
    runner = MagicMock()

    async def empty_run_turn(*a, **kw):
        return
        yield

    runner.run_turn = empty_run_turn

    ctx = AdapterContext(bus=bus, runner=runner, sessions=sessions, agent_name="main")
    await ctx.send_message("dm-alex", "hello")

    info = sessions.get("main/dm-alex")
    assert "last_inbound_adapter" not in info


@pytest.mark.asyncio
async def test_send_message_forwards_source_to_run_turn():
    """send_message passes source kwarg through to run_turn."""
    bus = EventBus()
    captured = {}

    async def capturing_run_turn(session, text, source="", fork_from=None):
        captured["source"] = source
        captured["session"] = session
        return
        yield  # make it an async generator

    runner = MagicMock()
    runner.run_turn = capturing_run_turn
    sessions = MagicMock()
    sessions.get_or_create.return_value = {"id": "main/default", "name": "default", "agent": "main"}
    ctx = AdapterContext(bus=bus, runner=runner, sessions=sessions)

    await ctx.send_message("default", "hi", source="telegram")

    assert captured["source"] == "telegram"
    assert captured["session"] == "main/default"  # bare "default" is normalized


def test_adapter_context_session_stats():
    from fera.gateway.stats import SessionStats
    stats = SessionStats()
    stats.record_turn("main/default", {"input_tokens": 5000, "output_tokens": 200})
    bus = EventBus()
    runner = MagicMock()
    sessions = MagicMock()
    ctx = AdapterContext(bus=bus, runner=runner, sessions=sessions, stats=stats)
    result = ctx.session_stats("default")
    assert result["turns"] == 1
    assert result["total_input_tokens"] == 5000


def test_adapter_context_session_stats_without_stats():
    bus = EventBus()
    runner = MagicMock()
    sessions = MagicMock()
    ctx = AdapterContext(bus=bus, runner=runner, sessions=sessions)
    result = ctx.session_stats("default")
    assert result == {}


# --- agent_name qualification ---


def test_context_qualify_session_bare_name():
    """Bare session name is prefixed with agent_name."""
    bus = EventBus()
    ctx = AdapterContext(bus=bus, runner=MagicMock(), sessions=MagicMock(), agent_name="forge")
    assert ctx._qualify_session("work") == "forge/work"
    assert ctx._qualify_session("default") == "forge/default"


def test_context_qualify_session_composite_passthrough():
    """Composite session IDs are not modified."""
    bus = EventBus()
    ctx = AdapterContext(bus=bus, runner=MagicMock(), sessions=MagicMock(), agent_name="forge")
    assert ctx._qualify_session("main/other") == "main/other"
    assert ctx._qualify_session("forge/work") == "forge/work"


@pytest.mark.asyncio
async def test_context_subscribe_qualifies_bare_with_agent_name():
    """subscribe("work") with agent_name="forge" routes events for "forge/work"."""
    bus = EventBus()
    ctx = AdapterContext(bus=bus, runner=AsyncMock(), sessions=AsyncMock(), agent_name="forge")

    received = []
    async def cb(event):
        received.append(event)

    ctx.subscribe("work", cb)
    await bus.publish({"session": "forge/work", "event": "agent.text"})
    assert len(received) == 1

    ctx.unsubscribe("work", cb)
    await bus.publish({"session": "forge/work", "event": "agent.done"})
    assert len(received) == 1


@pytest.mark.asyncio
async def test_context_send_message_uses_adapter_agent_for_bare_session():
    """send_message("work") with agent_name="forge" publishes session="forge/work"."""
    bus = EventBus()
    runner = MagicMock()
    async def empty_run_turn(*a, **kw):
        return
        yield
    runner.run_turn = empty_run_turn
    sessions = MagicMock()
    sessions.get_or_create.return_value = {"id": "forge/work", "name": "work", "agent": "forge"}
    ctx = AdapterContext(bus=bus, runner=runner, sessions=sessions, agent_name="forge")

    received = []
    async def cb(event):
        received.append(event)
    bus.subscribe("*", cb)

    await ctx.send_message("work", "hi")

    assert received[0]["session"] == "forge/work"
    sessions.get_or_create.assert_called_once_with("forge/work")


def test_channel_adapter_agent_name_defaults_to_main():
    """ChannelAdapter.agent_name defaults to DEFAULT_AGENT."""
    from fera.config import DEFAULT_AGENT

    class ConcreteAdapter(ChannelAdapter):
        @property
        def name(self): return "test"
        async def start(self, context): pass
        async def stop(self): pass
        def status(self): return AdapterStatus(True, "ok")

    adapter = ConcreteAdapter()
    assert adapter.agent_name == DEFAULT_AGENT


def test_telegram_adapter_stores_agent_name(tmp_path):
    """TelegramAdapter stores agent_name and exposes it."""
    from fera.adapters.telegram import TelegramAdapter
    tg = TelegramAdapter(
        bot_token="tok", allowed_users={"alex": 1}, agent_name="forge"
    )
    assert tg.agent_name == "forge"


def test_mattermost_adapter_stores_agent_name():
    """MattermostAdapter stores agent_name and exposes it."""
    from fera.adapters.mattermost import MattermostAdapter
    mm = MattermostAdapter(
        url="https://mm.example.com", bot_token="tok",
        allowed_users={"alex": "alex_mm"},
        agent_name="forge"
    )
    assert mm.agent_name == "forge"


# --- clear_session ---


@pytest.mark.asyncio
async def test_clear_session_calls_archive_session(tmp_path):
    """clear_session delegates to memory_writer.archive_session()."""
    from fera.gateway.transcript import TranscriptLogger

    transcript_logger = TranscriptLogger(tmp_path / "transcripts")
    transcript_path = transcript_logger.transcript_path("main/default")
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text("{}")

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    sessions = MagicMock()
    sessions.get.return_value = {
        "id": "main/default", "name": "default", "agent": "main",
        "workspace_dir": str(workspace),
    }

    runner = AsyncMock()
    memory_writer = MagicMock()
    memory_writer.archive_session = AsyncMock(return_value="MEMORY_SAVED: memory/timeline/2026-02-25/001.md")
    memory_writer.tz = None

    bus = EventBus()
    ctx = AdapterContext(
        bus=bus, runner=runner, sessions=sessions,
        memory_writer=memory_writer, transcript_logger=transcript_logger,
    )

    result = await ctx.clear_session("default")

    memory_writer.archive_session.assert_awaited_once()
    call_args = memory_writer.archive_session.call_args[0]
    assert call_args[0] == workspace
    assert call_args[1] == str(transcript_path)
    runner.clear_session.assert_awaited_once_with("main/default")
    assert "MEMORY_SAVED" in result


@pytest.mark.asyncio
async def test_clear_session_still_clears_if_archive_raises(tmp_path):
    """If archive_session raises, clear_session is still called and status reflects failure."""
    from fera.gateway.transcript import TranscriptLogger

    transcript_logger = TranscriptLogger(tmp_path / "transcripts")

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    sessions = MagicMock()
    sessions.get.return_value = {
        "id": "main/default", "name": "default", "agent": "main",
        "workspace_dir": str(workspace),
    }

    runner = AsyncMock()
    memory_writer = MagicMock()
    memory_writer.archive_session = AsyncMock(side_effect=RuntimeError("archive boom"))
    memory_writer.tz = None

    bus = EventBus()
    ctx = AdapterContext(
        bus=bus, runner=runner, sessions=sessions,
        memory_writer=memory_writer, transcript_logger=transcript_logger,
    )

    result = await ctx.clear_session("default")

    runner.clear_session.assert_awaited_once_with("main/default")
    assert "failed" in result.lower()
    assert "archive boom" in result


@pytest.mark.asyncio
async def test_clear_session_skips_archive_when_no_memory_writer():
    """clear_session works without memory_writer — returns empty status."""
    sessions = MagicMock()
    sessions.get.return_value = {
        "id": "main/default",
        "name": "default",
        "agent": "main",
        "workspace_dir": "/tmp/workspace",
    }
    runner = AsyncMock()
    bus = EventBus()
    ctx = AdapterContext(bus=bus, runner=runner, sessions=sessions)

    result = await ctx.clear_session("default")

    runner.clear_session.assert_awaited_once_with("main/default")
    assert result == ""


@pytest.mark.asyncio
async def test_clear_session_skips_archive_when_no_transcript_logger():
    """clear_session skips archive when no transcript_logger — returns empty status."""
    sessions = MagicMock()
    sessions.get.return_value = {
        "id": "main/default", "name": "default", "agent": "main",
        "workspace_dir": "/tmp/workspace",
    }
    runner = AsyncMock()
    memory_writer = MagicMock()
    memory_writer.archive_session = AsyncMock()

    bus = EventBus()
    ctx = AdapterContext(
        bus=bus, runner=runner, sessions=sessions,
        memory_writer=memory_writer,
        # No transcript_logger!
    )

    result = await ctx.clear_session("default")

    memory_writer.archive_session.assert_not_called()
    runner.clear_session.assert_awaited_once_with("main/default")
    assert result == ""


@pytest.mark.asyncio
async def test_clear_session_returns_no_transcript_when_archive_returns_none(tmp_path):
    """clear_session reports 'no transcript' when archive_session returns None."""
    from fera.gateway.transcript import TranscriptLogger

    transcript_logger = TranscriptLogger(tmp_path / "transcripts")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    sessions = MagicMock()
    sessions.get.return_value = {
        "id": "main/default", "name": "default", "agent": "main",
        "workspace_dir": str(workspace),
    }

    runner = AsyncMock()
    memory_writer = MagicMock()
    memory_writer.archive_session = AsyncMock(return_value=None)
    memory_writer.tz = None

    bus = EventBus()
    ctx = AdapterContext(
        bus=bus, runner=runner, sessions=sessions,
        memory_writer=memory_writer, transcript_logger=transcript_logger,
    )

    result = await ctx.clear_session("default")
    assert "no transcript" in result.lower()


@pytest.mark.asyncio
async def test_clear_session_qualifies_bare_session_name(tmp_path):
    """clear_session qualifies a bare session name using agent_name."""
    from fera.gateway.transcript import TranscriptLogger

    transcript_logger = TranscriptLogger(tmp_path / "transcripts")

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    sessions = MagicMock()
    sessions.get.return_value = {
        "id": "forge/work", "name": "work", "agent": "forge",
        "workspace_dir": str(workspace),
    }

    memory_writer = MagicMock()
    memory_writer.archive_session = AsyncMock()
    memory_writer.tz = None

    runner = AsyncMock()
    bus = EventBus()
    ctx = AdapterContext(
        bus=bus, runner=runner, sessions=sessions, agent_name="forge",
        memory_writer=memory_writer, transcript_logger=transcript_logger,
    )

    await ctx.clear_session("work")

    sessions.get.assert_called_once_with("forge/work")
    runner.clear_session.assert_awaited_once_with("forge/work")


# --- format_tool_summary ---


class TestFormatToolSummary:
    def test_bash_extracts_command(self):
        result = format_tool_summary("Bash", {"command": "grep -r 'foo' /path", "description": "search"})
        assert result == "> \U0001f5a5\ufe0f Bash \u00b7 grep -r 'foo' /path"

    def test_read_extracts_file_path(self):
        result = format_tool_summary("Read", {"file_path": "/home/fera/config.json"})
        assert result == "> \U0001f4d6 Read \u00b7 /home/fera/config.json"

    def test_write_extracts_file_path(self):
        result = format_tool_summary("Write", {"file_path": "/tmp/out.txt", "content": "data"})
        assert result == "> \u270f\ufe0f Write \u00b7 /tmp/out.txt"

    def test_edit_extracts_file_path(self):
        result = format_tool_summary("Edit", {"file_path": "/tmp/out.txt", "old_string": "a", "new_string": "b"})
        assert result == "> \u270f\ufe0f Edit \u00b7 /tmp/out.txt"

    def test_websearch_extracts_query(self):
        result = format_tool_summary("WebSearch", {"query": "python async patterns"})
        assert result == "> \U0001f50d WebSearch \u00b7 python async patterns"

    def test_webfetch_extracts_url(self):
        result = format_tool_summary("WebFetch", {"url": "https://example.com/page"})
        assert result == "> \U0001f310 WebFetch \u00b7 https://example.com/page"

    def test_grep_extracts_pattern(self):
        result = format_tool_summary("Grep", {"pattern": "TODO", "path": "/src"})
        assert result == "> \U0001f50e Grep \u00b7 TODO"

    def test_glob_extracts_pattern(self):
        result = format_tool_summary("Glob", {"pattern": "**/*.py"})
        assert result == "> \U0001f50e Glob \u00b7 **/*.py"

    def test_skill_extracts_skill(self):
        result = format_tool_summary("Skill", {"skill": "brainstorming"})
        assert result == "> \U0001f527 Skill \u00b7 brainstorming"

    def test_task_extracts_description(self):
        result = format_tool_summary("Task", {"description": "Explore codebase", "prompt": "..."})
        assert result == "> \u2705 Task \u00b7 Explore codebase"

    def test_unknown_tool_uses_first_string_value(self):
        result = format_tool_summary("CustomTool", {"config": "some_value", "count": 5})
        assert result == "> \U0001f527 CustomTool \u00b7 some_value"

    def test_empty_input(self):
        result = format_tool_summary("TodoWrite", {})
        assert result == "> \U0001f527 TodoWrite"

    def test_none_input(self):
        result = format_tool_summary("TodoWrite", None)
        assert result == "> \U0001f527 TodoWrite"

    def test_truncates_long_input(self):
        long_cmd = "x" * 200
        result = format_tool_summary("Bash", {"command": long_cmd})
        assert len(result) < 110
        assert result.endswith("\u2026")

    def test_bash_uses_first_line_only(self):
        result = format_tool_summary("Bash", {"command": "echo hello\necho world\necho done"})
        assert result == "> \U0001f5a5\ufe0f Bash \u00b7 echo hello"


# --- answer_question ---


@pytest.mark.asyncio
async def test_adapter_context_answer_question():
    """answer_question delegates to runner with qualified session."""
    bus = EventBus()
    runner = AsyncMock()
    sessions = MagicMock()

    ctx = AdapterContext(bus=bus, runner=runner, sessions=sessions, agent_name="main")
    await ctx.answer_question("dm-alex", "main/dm-alex:q1", {"Q?": "A"})

    runner.answer_question.assert_awaited_once_with("main/dm-alex", "main/dm-alex:q1", {"Q?": "A"})
