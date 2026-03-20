# tests/gateway/test_memory_writer.py
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from claude_agent_sdk.types import ResultMessage

from fera.gateway.memory_writer import MemoryWriter


def _make_result(result: str | None = None) -> ResultMessage:
    return ResultMessage(
        subtype="result", duration_ms=0, duration_api_ms=0,
        is_error=False, num_turns=1, session_id="test",
        result=result,
    )


class _FakeAssistantMessage:
    """Mimics claude_agent_sdk.types.AssistantMessage with text content."""
    def __init__(self, text: str):
        self.content = [_FakeTextBlock(text)]


class _FakeTextBlock:
    def __init__(self, text: str):
        self.text = text


class TestPreCompactHook:
    @pytest.mark.asyncio
    async def test_hook_calls_archive_session(self, tmp_path):
        mw = MemoryWriter()
        mw.archive_session = AsyncMock()

        hook = mw.make_pre_compact_hook()
        hook_input = {
            "session_id": "sdk-sess-1",
            "transcript_path": "/tmp/transcript.jsonl",
            "cwd": str(tmp_path),
            "hook_event_name": "PreCompact",
        }
        result = await hook(hook_input, None, {"signal": None})

        mw.archive_session.assert_awaited_once()
        call_args = mw.archive_session.await_args
        assert call_args[0][0] == tmp_path  # workspace
        assert call_args[0][1] == "/tmp/transcript.jsonl"  # transcript_path
        assert isinstance(call_args[0][2], date)  # target_date
        assert result == {}

    @pytest.mark.asyncio
    async def test_hook_uses_local_date_with_configured_timezone(self, tmp_path):
        """Hook passes local_date(tz) to archive_session."""
        mw = MemoryWriter(tz="Asia/Singapore")
        mw.archive_session = AsyncMock()

        fixed_date = date(2026, 2, 22)
        with patch("fera.gateway.memory_writer.local_date", return_value=fixed_date) as mock_local_date:
            hook = mw.make_pre_compact_hook()
            hook_input = {
                "session_id": "sdk-sess-1",
                "transcript_path": "/tmp/t.jsonl",
                "cwd": str(tmp_path),
                "hook_event_name": "PreCompact",
            }
            await hook(hook_input, None, {"signal": None})
            mock_local_date.assert_called_with("Asia/Singapore")

        call_args = mw.archive_session.await_args
        assert call_args[0][2] == fixed_date

    @pytest.mark.asyncio
    async def test_hook_uses_transcript_path_override(self, tmp_path):
        mw = MemoryWriter()
        mw.archive_session = AsyncMock()

        fera_path = "/var/fera/data/transcripts/main/default.jsonl"
        hook = mw.make_pre_compact_hook(transcript_path=fera_path)
        hook_input = {
            "session_id": "sdk-sess-1",
            "transcript_path": "/tmp/sdk-internal-transcript.jsonl",
            "cwd": str(tmp_path),
        }
        await hook(hook_input, None, {"signal": None})

        call_args = mw.archive_session.await_args
        assert call_args[0][1] == fera_path

    @pytest.mark.asyncio
    async def test_hook_returns_empty_dict_on_write_error(self, tmp_path):
        mw = MemoryWriter()
        mw.archive_session = AsyncMock(side_effect=RuntimeError("write failed"))

        hook = mw.make_pre_compact_hook()
        hook_input = {
            "session_id": "sdk-sess-1",
            "transcript_path": "/tmp/t.jsonl",
            "cwd": str(tmp_path),
            "hook_event_name": "PreCompact",
        }
        # Must not raise — errors are swallowed so compaction proceeds
        result = await hook(hook_input, None, {"signal": None})
        assert result == {}

    @pytest.mark.asyncio
    async def test_hook_calls_on_compact_for_auto_trigger(self, tmp_path):
        mw = MemoryWriter()
        mw.archive_session = AsyncMock()
        on_compact = AsyncMock()

        hook = mw.make_pre_compact_hook(on_compact=on_compact)
        hook_input = {
            "session_id": "sdk-sess-1",
            "transcript_path": "/tmp/t.jsonl",
            "cwd": str(tmp_path),
            "hook_event_name": "PreCompact",
            "trigger": "auto",
        }
        await hook(hook_input, None, {"signal": None})

        on_compact.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_hook_skips_on_compact_for_manual_trigger(self, tmp_path):
        mw = MemoryWriter()
        mw.archive_session = AsyncMock()
        on_compact = AsyncMock()

        hook = mw.make_pre_compact_hook(on_compact=on_compact)
        hook_input = {
            "session_id": "sdk-sess-1",
            "transcript_path": "/tmp/t.jsonl",
            "cwd": str(tmp_path),
            "hook_event_name": "PreCompact",
            "trigger": "manual",
        }
        await hook(hook_input, None, {"signal": None})

        on_compact.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_hook_works_without_on_compact(self, tmp_path):
        """Backward compat: no on_compact means no crash, archive still called."""
        mw = MemoryWriter()
        mw.archive_session = AsyncMock()

        hook = mw.make_pre_compact_hook()
        hook_input = {
            "session_id": "sdk-sess-1",
            "transcript_path": "/tmp/t.jsonl",
            "cwd": str(tmp_path),
            "hook_event_name": "PreCompact",
            "trigger": "auto",
        }
        result = await hook(hook_input, None, {"signal": None})

        mw.archive_session.assert_awaited_once()
        assert result == {}

    @pytest.mark.asyncio
    async def test_hook_swallows_on_compact_error(self, tmp_path):
        """on_compact failure must not block archiving."""
        mw = MemoryWriter()
        mw.archive_session = AsyncMock()
        on_compact = AsyncMock(side_effect=RuntimeError("notification failed"))

        hook = mw.make_pre_compact_hook(on_compact=on_compact)
        hook_input = {
            "session_id": "sdk-sess-1",
            "transcript_path": "/tmp/t.jsonl",
            "cwd": str(tmp_path),
            "hook_event_name": "PreCompact",
            "trigger": "auto",
        }
        result = await hook(hook_input, None, {"signal": None})

        on_compact.assert_awaited_once()
        mw.archive_session.assert_awaited_once()
        assert result == {}

    @pytest.mark.asyncio
    async def test_on_compact_fires_before_archive(self, tmp_path):
        """on_compact must fire before archive_session."""
        mw = MemoryWriter()
        calls = []

        async def track_compact():
            calls.append("on_compact")

        async def track_archive(workspace, transcript_path, target_date):
            calls.append("archive")

        mw.archive_session = track_archive

        hook = mw.make_pre_compact_hook(on_compact=track_compact)
        hook_input = {
            "session_id": "sdk-sess-1",
            "transcript_path": "/tmp/t.jsonl",
            "cwd": str(tmp_path),
            "hook_event_name": "PreCompact",
            "trigger": "auto",
        }
        await hook(hook_input, None, {"signal": None})

        assert calls == ["on_compact", "archive"]


class TestWriteMethods:
    """Tests that write methods call _spawn_archivist_turn with correct args."""

    @pytest.mark.asyncio
    async def test_write_jit_memory_passes_date_in_prompt(self, tmp_path):
        mw = MemoryWriter()
        mw._spawn_archivist_turn = AsyncMock(return_value="MEMORY_SAVED: memory/timeline/2026-02-21/001.md")
        target = date(2026, 2, 21)
        await mw.write_jit_memory(tmp_path, "/tmp/transcript.jsonl", target)

        mw._spawn_archivist_turn.assert_awaited_once()
        prompt = mw._spawn_archivist_turn.await_args[0][1]
        assert "2026-02-21" in prompt
        assert "/tmp/transcript.jsonl" in prompt

    @pytest.mark.asyncio
    async def test_write_jit_memory_returns_archivist_result(self, tmp_path):
        mw = MemoryWriter()
        mw._spawn_archivist_turn = AsyncMock(return_value="MEMORY_SAVED: memory/timeline/2026-02-21/001.md")
        target = date(2026, 2, 21)
        result = await mw.write_jit_memory(tmp_path, "/tmp/transcript.jsonl", target)
        assert result == "MEMORY_SAVED: memory/timeline/2026-02-21/001.md"

    @pytest.mark.asyncio
    async def test_write_jit_memory_returns_silent_reply(self, tmp_path):
        mw = MemoryWriter()
        mw._spawn_archivist_turn = AsyncMock(return_value="SILENT_REPLY")
        target = date(2026, 2, 21)
        result = await mw.write_jit_memory(tmp_path, "/tmp/transcript.jsonl", target)
        assert result == "SILENT_REPLY"

    @pytest.mark.asyncio
    async def test_write_jit_memory_creates_timeline_directory(self, tmp_path):
        """The timeline directory must exist before the archivist runs,
        because the archivist has no Bash tool to mkdir."""
        mw = MemoryWriter()
        mw._spawn_archivist_turn = AsyncMock(return_value="MEMORY_SAVED: memory/timeline/2026-02-27/001.md")
        target = date(2026, 2, 27)
        await mw.write_jit_memory(tmp_path, "/tmp/transcript.jsonl", target)

        timeline_dir = tmp_path / "memory" / "timeline" / "2026-02-27"
        assert timeline_dir.is_dir()

    @pytest.mark.asyncio
    async def test_write_synthesis_passes_date_in_prompt(self, tmp_path):
        mw = MemoryWriter()
        mw._spawn_archivist_turn = AsyncMock(return_value="SILENT_REPLY")
        target = date(2026, 2, 21)
        await mw.write_synthesis(tmp_path, target)

        mw._spawn_archivist_turn.assert_awaited_once()
        prompt = mw._spawn_archivist_turn.await_args[0][1]
        assert "2026-02-21" in prompt

    @pytest.mark.asyncio
    async def test_update_memory_md_passes_date_in_prompt(self, tmp_path):
        mw = MemoryWriter()
        mw._spawn_archivist_turn = AsyncMock(return_value="SILENT_REPLY")
        target = date(2026, 2, 21)
        await mw.update_memory_md(tmp_path, target)

        mw._spawn_archivist_turn.assert_awaited_once()
        prompt = mw._spawn_archivist_turn.await_args[0][1]
        assert "2026-02-21" in prompt


class TestArchivistToolset:
    """Tests that _spawn_archivist_turn uses the correct tools."""

    @pytest.mark.asyncio
    async def test_spawn_archivist_turn_uses_read_glob_write(self, tmp_path):
        mw = MemoryWriter()
        captured = {}

        class FakeOptions:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        async def fake_query(*, prompt, options=None):
            return
            yield

        with patch("fera.gateway.memory_writer.ClaudeAgentOptions", FakeOptions), \
             patch("fera.gateway.memory_writer.query", fake_query):
            await mw._spawn_archivist_turn(tmp_path, "test prompt")

        assert captured["allowed_tools"] == ["Read", "Glob", "Write"]

    def test_system_prompt_references_correct_tools(self):
        from fera.gateway.memory_writer import _ARCHIVIST_SYSTEM_PROMPT
        assert "Read" in _ARCHIVIST_SYSTEM_PROMPT
        assert "Glob" in _ARCHIVIST_SYSTEM_PROMPT
        assert "Write" in _ARCHIVIST_SYSTEM_PROMPT
        assert "Bash" not in _ARCHIVIST_SYSTEM_PROMPT

    def test_jit_prompt_does_not_reference_bash_commands(self):
        from fera.gateway.memory_writer import _JIT_PROMPT_TEMPLATE
        assert "Bash" not in _JIT_PROMPT_TEMPLATE
        assert "mkdir" not in _JIT_PROMPT_TEMPLATE
        assert "ls " not in _JIT_PROMPT_TEMPLATE


class TestArchivistModel:
    """Tests that _spawn_archivist_turn passes model to ClaudeAgentOptions."""

    @pytest.mark.asyncio
    async def test_spawn_archivist_turn_passes_model(self, tmp_path):
        mw = MemoryWriter(model="claude-sonnet-4-6")
        captured = {}

        class FakeOptions:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        async def fake_query(*, prompt, options=None):
            return
            yield  # make it an async generator

        with patch("fera.gateway.memory_writer.ClaudeAgentOptions", FakeOptions), \
             patch("fera.gateway.memory_writer.query", fake_query):
            await mw._spawn_archivist_turn(tmp_path, "test prompt")

        assert captured["model"] == "claude-sonnet-4-6"

    @pytest.mark.asyncio
    async def test_spawn_archivist_turn_no_model_by_default(self, tmp_path):
        mw = MemoryWriter()
        captured = {}

        class FakeOptions:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        async def fake_query(*, prompt, options=None):
            return
            yield  # make it an async generator

        with patch("fera.gateway.memory_writer.ClaudeAgentOptions", FakeOptions), \
             patch("fera.gateway.memory_writer.query", fake_query):
            await mw._spawn_archivist_turn(tmp_path, "test prompt")

        assert captured.get("model") is None


class TestSpawnArchivistResult:
    """Tests that _spawn_archivist_turn returns ResultMessage.result, not intermediate text."""

    @pytest.mark.asyncio
    async def test_returns_result_message_result_not_intermediate_text(self, tmp_path):
        """The archivist emits intermediate AssistantMessages during tool use.
        Only the ResultMessage.result should be returned."""
        mw = MemoryWriter()

        async def fake_query(*, prompt, options=None):
            # Intermediate reasoning (tool-use turns)
            yield _FakeAssistantMessage("I'll read the transcript now.")
            yield _FakeAssistantMessage("Let me write the memory file.")
            # Final result
            yield _make_result(result="MEMORY_SAVED: memory/timeline/2026-02-27/001.md")

        with patch("fera.gateway.memory_writer.ClaudeAgentOptions", lambda **kw: None), \
             patch("fera.gateway.memory_writer.query", fake_query):
            result = await mw._spawn_archivist_turn(tmp_path, "test prompt")

        assert result == "MEMORY_SAVED: memory/timeline/2026-02-27/001.md"

    @pytest.mark.asyncio
    async def test_returns_silent_reply_from_result_message(self, tmp_path):
        mw = MemoryWriter()

        async def fake_query(*, prompt, options=None):
            yield _FakeAssistantMessage("Nothing worth saving here.")
            yield _make_result(result="SILENT_REPLY")

        with patch("fera.gateway.memory_writer.ClaudeAgentOptions", lambda **kw: None), \
             patch("fera.gateway.memory_writer.query", fake_query):
            result = await mw._spawn_archivist_turn(tmp_path, "test prompt")

        assert result == "SILENT_REPLY"

    @pytest.mark.asyncio
    async def test_returns_empty_string_when_result_is_none(self, tmp_path):
        mw = MemoryWriter()

        async def fake_query(*, prompt, options=None):
            yield _make_result(result=None)

        with patch("fera.gateway.memory_writer.ClaudeAgentOptions", lambda **kw: None), \
             patch("fera.gateway.memory_writer.query", fake_query):
            result = await mw._spawn_archivist_turn(tmp_path, "test prompt")

        assert result == ""

    @pytest.mark.asyncio
    async def test_returns_empty_string_when_no_result_message(self, tmp_path):
        """If query yields no ResultMessage at all, return empty string."""
        mw = MemoryWriter()

        async def fake_query(*, prompt, options=None):
            yield _FakeAssistantMessage("Some reasoning text")

        with patch("fera.gateway.memory_writer.ClaudeAgentOptions", lambda **kw: None), \
             patch("fera.gateway.memory_writer.query", fake_query):
            result = await mw._spawn_archivist_turn(tmp_path, "test prompt")

        assert result == ""


class TestRotateTranscriptFile:
    def test_renames_existing_file_with_timestamp(self, tmp_path):
        transcript = tmp_path / "dm-alex.jsonl"
        transcript.write_text("data")

        mw = MemoryWriter()
        mw.rotate_transcript_file(str(transcript))

        assert not transcript.exists()
        rotated = list(tmp_path.glob("dm-alex-*.jsonl"))
        assert len(rotated) == 1
        # Timestamp suffix matches YYYYMMDDTHHmmSS
        match = re.match(r"dm-alex-(\d{8}T\d{6})\.jsonl", rotated[0].name)
        assert match is not None

    def test_no_op_when_file_does_not_exist(self, tmp_path):
        transcript = tmp_path / "nonexistent.jsonl"
        mw = MemoryWriter()
        # Must not raise
        mw.rotate_transcript_file(str(transcript))
        assert list(tmp_path.glob("*.jsonl")) == []

    def test_rotated_file_preserves_content(self, tmp_path):
        transcript = tmp_path / "session.jsonl"
        transcript.write_text('{"type":"user"}\n')

        mw = MemoryWriter()
        mw.rotate_transcript_file(str(transcript))

        rotated = list(tmp_path.glob("session-*.jsonl"))[0]
        assert rotated.read_text() == '{"type":"user"}\n'

    def test_rotate_returns_renamed_path(self, tmp_path):
        transcript = tmp_path / "default.jsonl"
        transcript.write_text("data")

        mw = MemoryWriter()
        rotated = mw.rotate_transcript_file(str(transcript))

        assert rotated is not None
        assert Path(rotated).exists()
        assert "default-" in rotated

    def test_rotate_returns_none_when_file_missing(self, tmp_path):
        mw = MemoryWriter()
        result = mw.rotate_transcript_file(str(tmp_path / "missing.jsonl"))
        assert result is None


class TestArchiveSession:
    """Tests for the unified archive_session method."""

    @pytest.mark.asyncio
    async def test_archive_rotates_first_then_writes_from_rotated(self, tmp_path):
        """archive_session rotates the transcript, then passes rotated path to write_jit_memory."""
        transcript = tmp_path / "default.jsonl"
        transcript.write_text("data")

        mw = MemoryWriter()
        # Track call order
        calls = []
        original_rotate = mw.rotate_transcript_file

        def tracking_rotate(path_str):
            calls.append(("rotate", path_str))
            return original_rotate(path_str)

        async def tracking_write(workspace, transcript_path, target_date):
            calls.append(("write", transcript_path))
            return "MEMORY_SAVED: memory/timeline/2026-02-23/001.md"

        mw.rotate_transcript_file = tracking_rotate
        mw.write_jit_memory = tracking_write

        target = date(2026, 2, 23)
        await mw.archive_session(tmp_path, str(transcript), target)

        assert len(calls) == 2
        assert calls[0][0] == "rotate"
        assert calls[1][0] == "write"
        # write_jit_memory receives the rotated path, not the original
        assert calls[1][1] != str(transcript)
        assert "default-" in calls[1][1]

    @pytest.mark.asyncio
    async def test_archive_returns_archivist_result(self, tmp_path):
        """archive_session returns the archivist result string."""
        transcript = tmp_path / "default.jsonl"
        transcript.write_text("data")

        mw = MemoryWriter()
        mw.write_jit_memory = AsyncMock(return_value="MEMORY_SAVED: memory/timeline/2026-02-23/001.md")

        target = date(2026, 2, 23)
        result = await mw.archive_session(tmp_path, str(transcript), target)
        assert result == "MEMORY_SAVED: memory/timeline/2026-02-23/001.md"

    @pytest.mark.asyncio
    async def test_archive_skips_when_transcript_missing(self, tmp_path):
        """archive_session returns None when the transcript file doesn't exist."""
        mw = MemoryWriter()
        mw.write_jit_memory = AsyncMock()

        target = date(2026, 2, 23)
        result = await mw.archive_session(tmp_path, str(tmp_path / "missing.jsonl"), target)

        mw.write_jit_memory.assert_not_called()
        assert result is None

    @pytest.mark.asyncio
    async def test_archive_write_failure_logs_and_raises(self, tmp_path):
        """When write_jit_memory fails, archive_session logs and re-raises.

        The transcript has already been rotated (rotate-first), so the caller
        can decide how to handle the failure.
        """
        transcript = tmp_path / "default.jsonl"
        transcript.write_text("data")

        mw = MemoryWriter()
        mw.write_jit_memory = AsyncMock(side_effect=RuntimeError("archivist failed"))

        target = date(2026, 2, 23)
        with pytest.raises(RuntimeError, match="archivist failed"):
            await mw.archive_session(tmp_path, str(transcript), target)

        # Transcript was still rotated (rotate-first)
        assert not transcript.exists()
        rotated = list(tmp_path.glob("default-*.jsonl"))
        assert len(rotated) == 1


