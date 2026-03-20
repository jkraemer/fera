import json

import pytest
from fera.gateway.transcript import TranscriptLogger


@pytest.mark.asyncio
async def test_record_user_message(tmp_path):
    logger = TranscriptLogger(tmp_path)
    event = {
        "event": "user.message", "session": "default",
        "data": {"text": "hello", "source": "web"},
    }
    await logger.record_event(event)

    entries = logger.load("default")
    assert len(entries) == 1
    assert entries[0]["type"] == "user"
    assert entries[0]["text"] == "hello"
    assert entries[0]["source"] == "web"
    assert "ts" in entries[0]


@pytest.mark.asyncio
async def test_record_agent_text(tmp_path):
    logger = TranscriptLogger(tmp_path)
    event = {
        "event": "agent.text", "session": "default",
        "turn_source": "telegram",
        "data": {"text": "reply"},
    }
    await logger.record_event(event)

    entries = logger.load("default")
    assert entries[0]["type"] == "agent"
    assert entries[0]["text"] == "reply"
    assert entries[0]["turn_source"] == "telegram"


@pytest.mark.asyncio
async def test_record_tool_use(tmp_path):
    logger = TranscriptLogger(tmp_path)
    event = {
        "event": "agent.tool_use", "session": "s1",
        "data": {"id": "t1", "name": "Bash", "input": {"command": "ls"}},
    }
    await logger.record_event(event)

    entries = logger.load("s1")
    assert entries[0]["type"] == "tool_use"
    assert entries[0]["name"] == "Bash"
    assert entries[0]["input"] == {"command": "ls"}
    assert "ts" in entries[0]


@pytest.mark.asyncio
async def test_record_tool_result(tmp_path):
    logger = TranscriptLogger(tmp_path)
    event = {
        "event": "agent.tool_result", "session": "s1",
        "data": {"tool_use_id": "t1", "content": "file.txt", "is_error": False},
    }
    await logger.record_event(event)

    entries = logger.load("s1")
    assert entries[0]["type"] == "tool_result"
    assert entries[0]["tool_use_id"] == "t1"
    assert entries[0]["is_error"] is False
    assert "ts" in entries[0]


@pytest.mark.asyncio
async def test_record_done(tmp_path):
    logger = TranscriptLogger(tmp_path)
    event = {
        "event": "agent.done", "session": "s1",
        "data": {"duration_ms": 500, "model": "claude-opus-4-6", "input_tokens": 10, "output_tokens": 5},
    }
    await logger.record_event(event)

    entries = logger.load("s1")
    assert entries[0]["type"] == "done"
    assert entries[0]["duration_ms"] == 500
    assert entries[0]["model"] == "claude-opus-4-6"
    assert "ts" in entries[0]


@pytest.mark.asyncio
async def test_record_error(tmp_path):
    logger = TranscriptLogger(tmp_path)
    event = {
        "event": "agent.error", "session": "s1",
        "data": {"error": "oops"},
    }
    await logger.record_event(event)

    entries = logger.load("s1")
    assert entries[0]["type"] == "error"
    assert entries[0]["error"] == "oops"
    assert "ts" in entries[0]


@pytest.mark.asyncio
async def test_skips_system_session(tmp_path):
    logger = TranscriptLogger(tmp_path)
    event = {"event": "user.message", "session": "$system", "data": {"text": "x", "source": "web"}}
    await logger.record_event(event)
    assert not (tmp_path / "$system.jsonl").exists()


@pytest.mark.asyncio
async def test_skips_irrelevant_event_type(tmp_path):
    logger = TranscriptLogger(tmp_path)
    event = {"event": "log.entry", "session": "default", "data": {}}
    await logger.record_event(event)
    assert not (tmp_path / "default.jsonl").exists()


def test_load_returns_empty_for_nonexistent_session(tmp_path):
    logger = TranscriptLogger(tmp_path)
    assert logger.load("nosuchsession") == []


@pytest.mark.asyncio
async def test_load_limit_returns_last_n_entries(tmp_path):
    logger = TranscriptLogger(tmp_path)
    for i in range(10):
        await logger.record_event({
            "event": "user.message", "session": "s",
            "data": {"text": str(i), "source": "web"},
        })

    entries = logger.load("s", limit=3)
    assert len(entries) == 3
    assert entries[-1]["text"] == "9"


@pytest.mark.asyncio
async def test_entries_for_different_sessions_are_separate(tmp_path):
    logger = TranscriptLogger(tmp_path)
    await logger.record_event({"event": "user.message", "session": "a", "data": {"text": "for a", "source": "web"}})
    await logger.record_event({"event": "user.message", "session": "b", "data": {"text": "for b", "source": "web"}})

    assert logger.load("a")[0]["text"] == "for a"
    assert logger.load("b")[0]["text"] == "for b"


@pytest.mark.asyncio
async def test_composite_session_stored_in_agent_subdirectory(tmp_path):
    """Composite session IDs create agent subdirectories."""
    logger = TranscriptLogger(tmp_path)
    event = {
        "event": "user.message", "session": "forge/coding-1",
        "data": {"text": "hello", "source": "web"},
    }
    await logger.record_event(event)

    # File is at tmp_path/forge/coding-1.jsonl
    assert (tmp_path / "forge" / "coding-1.jsonl").exists()
    entries = logger.load("forge/coding-1")
    assert len(entries) == 1
    assert entries[0]["text"] == "hello"


@pytest.mark.asyncio
async def test_same_name_different_agents_have_separate_transcripts(tmp_path):
    logger = TranscriptLogger(tmp_path)
    await logger.record_event({
        "event": "user.message", "session": "main/default",
        "data": {"text": "from main", "source": "web"},
    })
    await logger.record_event({
        "event": "user.message", "session": "forge/default",
        "data": {"text": "from forge", "source": "web"},
    })

    assert logger.load("main/default")[0]["text"] == "from main"
    assert logger.load("forge/default")[0]["text"] == "from forge"
    # Files are separate
    assert (tmp_path / "main" / "default.jsonl").exists()
    assert (tmp_path / "forge" / "default.jsonl").exists()


def test_load_aggregates_rotated_files_in_chronological_order(tmp_path):
    """load() includes rotated siblings before the current file, in sorted order."""
    logger = TranscriptLogger(tmp_path)

    # Write two rotated files and the current file manually
    rotated1 = tmp_path / "mysession-20260222T073000.jsonl"
    rotated2 = tmp_path / "mysession-20260222T083000.jsonl"
    current = tmp_path / "mysession.jsonl"

    rotated1.write_text(json.dumps({"type": "user", "text": "first"}) + "\n", encoding="utf-8")
    rotated2.write_text(json.dumps({"type": "user", "text": "second"}) + "\n", encoding="utf-8")
    current.write_text(json.dumps({"type": "user", "text": "third"}) + "\n", encoding="utf-8")

    entries = logger.load("mysession")

    assert len(entries) == 3
    assert entries[0]["text"] == "first"
    assert entries[1]["text"] == "second"
    assert entries[2]["text"] == "third"


def test_load_respects_limit_across_rotated_and_current(tmp_path):
    """limit is applied to the combined set of entries from all files."""
    logger = TranscriptLogger(tmp_path)

    rotated = tmp_path / "s-20260222T060000.jsonl"
    current = tmp_path / "s.jsonl"

    # 3 entries in the rotated file, 2 in current
    rotated_lines = "\n".join(
        json.dumps({"type": "user", "text": str(i)}) for i in range(3)
    ) + "\n"
    rotated.write_text(rotated_lines, encoding="utf-8")
    current_lines = "\n".join(
        json.dumps({"type": "user", "text": str(i)}) for i in range(3, 5)
    ) + "\n"
    current.write_text(current_lines, encoding="utf-8")

    entries = logger.load("s", limit=3)

    # Should return the last 3 of 5 total entries
    assert len(entries) == 3
    assert entries[0]["text"] == "2"
    assert entries[1]["text"] == "3"
    assert entries[2]["text"] == "4"


def test_load_rotated_only_no_current(tmp_path):
    """load() works when there is no current file, only rotated siblings."""
    logger = TranscriptLogger(tmp_path)

    rotated = tmp_path / "gone-20260222T070000.jsonl"
    rotated.write_text(json.dumps({"type": "user", "text": "old"}) + "\n", encoding="utf-8")

    entries = logger.load("gone")

    assert len(entries) == 1
    assert entries[0]["text"] == "old"


def test_transcript_path_is_public(tmp_path):
    """transcript_path() is a public method (not underscore-prefixed)."""
    logger = TranscriptLogger(tmp_path)
    path = logger.transcript_path("main/default")
    assert path == tmp_path / "main" / "default.jsonl"


def test_load_does_not_pick_up_rotated_files_from_prefixed_session(tmp_path):
    """load('alex') must not include rotated files from 'alex-extra'."""
    logger = TranscriptLogger(tmp_path)

    # alex has a rotated file and a current file
    (tmp_path / "alex-20260222T073000.jsonl").write_text(
        json.dumps({"type": "user", "text": "alex-rotated"}) + "\n", encoding="utf-8"
    )
    (tmp_path / "alex.jsonl").write_text(
        json.dumps({"type": "user", "text": "alex-current"}) + "\n", encoding="utf-8"
    )

    # alex-extra also has a rotated file that shares the "alex-" prefix
    (tmp_path / "alex-extra-20260222T080000.jsonl").write_text(
        json.dumps({"type": "user", "text": "alex-extra-rotated"}) + "\n", encoding="utf-8"
    )

    entries = logger.load("alex")

    texts = [e["text"] for e in entries]
    assert "alex-extra-rotated" not in texts
    assert "alex-rotated" in texts
    assert "alex-current" in texts
    assert len(entries) == 2
