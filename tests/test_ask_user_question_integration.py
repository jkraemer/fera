"""Full round-trip integration test for AskUserQuestion support."""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fera.adapters.bus import EventBus
from fera.gateway.runner import AgentRunner
from fera.gateway.lanes import LaneManager
from fera.gateway.sessions import SessionManager


@pytest.mark.asyncio
async def test_ask_user_question_round_trip(tmp_path):
    """Complete flow: can_use_tool -> event -> answer -> Future resolved."""
    sessions = SessionManager(tmp_path / "sessions.json", fera_home=tmp_path)
    sessions.create("default", agent="main")

    bus = EventBus()
    runner = AgentRunner(sessions, LaneManager(), fera_home=tmp_path, bus=bus)

    # Subscribe to capture events
    received = []
    bus.subscribe("main/default", lambda e: received.append(e))

    # Build the can_use_tool callback for this session
    callback = runner._build_can_use_tool("main/default")

    # Simulate AskUserQuestion input
    questions_input = {
        "questions": [
            {
                "question": "Which DB?",
                "header": "DB",
                "options": [
                    {"label": "Postgres", "description": "SQL"},
                    {"label": "SQLite", "description": "Embedded"},
                ],
                "multiSelect": False,
            }
        ]
    }

    # Start the callback (it will await the Future)
    task = asyncio.create_task(callback("AskUserQuestion", questions_input, None))
    await asyncio.sleep(0.05)  # let event publish

    # Verify event was published
    assert len(received) == 1
    event = received[0]
    assert event["event"] == "agent.user_question"
    assert event["data"]["questions"] == questions_input["questions"]
    question_id = event["data"]["question_id"]

    # Resolve via answer_question (as an adapter would)
    await runner.answer_question("main/default", question_id, {"Which DB?": "Postgres"})

    # Wait for result
    result = await asyncio.wait_for(task, timeout=2.0)

    # Verify the result
    assert result.updated_input["answers"] == {"Which DB?": "Postgres"}
    assert result.updated_input["questions"] == questions_input["questions"]
    # Pending questions should be cleaned up
    assert question_id not in runner._pending_questions


@pytest.mark.asyncio
async def test_ask_user_question_cancelled_on_clear(tmp_path):
    """Clearing a session cancels pending questions."""
    sessions_file = tmp_path / "data" / "sessions.json"
    sessions_file.parent.mkdir(parents=True)
    (tmp_path / "agents" / "main" / "workspace").mkdir(parents=True)
    sessions = SessionManager(sessions_file, fera_home=tmp_path)
    sessions.create("default", agent="main")

    bus = EventBus()
    runner = AgentRunner(sessions=sessions, lanes=LaneManager(), bus=bus)

    callback = runner._build_can_use_tool("main/default")

    task = asyncio.create_task(callback("AskUserQuestion", {"questions": []}, None))
    await asyncio.sleep(0.05)

    # Clear the session
    await runner.clear_session("main/default")

    # Result should be a deny
    result = await asyncio.wait_for(task, timeout=2.0)
    assert result.message == "Question cancelled (session cleared)"
    assert len(runner._pending_questions) == 0


@pytest.mark.asyncio
async def test_non_ask_tools_auto_approved(tmp_path):
    """Non-AskUserQuestion tools are auto-approved without events."""
    sessions = SessionManager(tmp_path / "sessions.json", fera_home=tmp_path)
    bus = EventBus()
    runner = AgentRunner(sessions, LaneManager(), fera_home=tmp_path, bus=bus)

    received = []
    bus.subscribe("main/default", lambda e: received.append(e))

    callback = runner._build_can_use_tool("main/default")

    result = await callback("Bash", {"command": "ls"}, None)
    assert result.updated_input == {"command": "ls"}
    assert len(received) == 0  # no events published
