import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from claude_agent_sdk.types import ResultMessage as _ResultMessage

from fera.gateway.lanes import LaneManager
from fera.gateway.pool import ClientPool
from fera.gateway.protocol import make_event
from fera.gateway.runner import AgentRunner, translate_message, _truncate_user_message, MAX_USER_MESSAGE_BYTES
from fera.gateway.sessions import SessionManager


def _sdk_result(session_id="sdk-123", model="claude-opus-4-6"):
    """Create a real ResultMessage for use in FakeClient generators."""
    return _ResultMessage(
        subtype="result", duration_ms=0, duration_api_ms=0,
        is_error=False, num_turns=1, session_id=session_id,
    )


def _make_assistant_message(content_blocks):
    """Create a mock-like AssistantMessage for testing translation."""

    class FakeBlock:
        pass

    class FakeTextBlock(FakeBlock):
        def __init__(self, text):
            self.text = text

    class FakeToolUseBlock(FakeBlock):
        def __init__(self, id, name, input):
            self.id = id
            self.name = name
            self.input = input

    class FakeToolResultBlock(FakeBlock):
        def __init__(self, tool_use_id, content, is_error=False):
            self.tool_use_id = tool_use_id
            self.content = content
            self.is_error = is_error

    class FakeAssistantMessage:
        def __init__(self, content):
            self.content = content
            self.model = "claude-opus-4-6"
            self.parent_tool_use_id = None
            self.error = None

    block_map = {
        "text": FakeTextBlock,
        "tool_use": FakeToolUseBlock,
        "tool_result": FakeToolResultBlock,
    }

    blocks = []
    for spec in content_blocks:
        kind = spec.pop("type")
        blocks.append(block_map[kind](**spec))

    return FakeAssistantMessage(blocks)


def _make_result_message(session_id="default", is_error=False):
    class FakeResultMessage:
        def __init__(self):
            self.subtype = "result"
            self.duration_ms = 1000
            self.duration_api_ms = 800
            self.is_error = is_error
            self.num_turns = 1
            self.session_id = session_id
            self.total_cost_usd = 0.01
            self.model = "claude-opus-4-6"
            self.usage = {"input_tokens": 10, "output_tokens": 5}
            self.result = None

    return FakeResultMessage()


def test_translate_text_block():
    msg = _make_assistant_message([{"type": "text", "text": "Hello there"}])
    events = translate_message(msg, session="default")
    assert len(events) == 1
    assert events[0]["event"] == "agent.text"
    assert events[0]["data"]["text"] == "Hello there"


def test_translate_tool_use_block():
    msg = _make_assistant_message([
        {"type": "tool_use", "id": "t1", "name": "memory_search", "input": {"query": "test"}}
    ])
    events = translate_message(msg, session="default")
    assert len(events) == 1
    assert events[0]["event"] == "agent.tool_use"
    assert events[0]["data"]["name"] == "memory_search"


def test_translate_tool_result_block():
    msg = _make_assistant_message([
        {"type": "tool_result", "tool_use_id": "t1", "content": "found stuff"}
    ])
    events = translate_message(msg, session="default")
    assert len(events) == 1
    assert events[0]["event"] == "agent.tool_result"


def test_translate_multiple_blocks():
    msg = _make_assistant_message([
        {"type": "text", "text": "Let me search"},
        {"type": "tool_use", "id": "t1", "name": "search", "input": {}},
    ])
    events = translate_message(msg, session="default")
    assert len(events) == 2
    assert events[0]["event"] == "agent.text"
    assert events[1]["event"] == "agent.tool_use"


def test_translate_result_message():
    msg = _make_result_message(session_id="default")
    events = translate_message(msg, session="default")
    assert len(events) == 1
    assert events[0]["event"] == "agent.done"


def test_translate_result_error():
    msg = _make_result_message(session_id="default", is_error=True)
    events = translate_message(msg, session="default")
    assert len(events) == 1
    assert events[0]["event"] == "agent.error"


def test_translate_result_message_includes_metadata():
    msg = _make_result_message(session_id="default")
    events = translate_message(msg, session="default")
    assert events[0]["event"] == "agent.done"
    assert events[0]["data"]["duration_ms"] == 1000  # from FakeResultMessage fixture
    assert events[0]["data"]["model"] == "claude-opus-4-6"
    assert events[0]["data"]["input_tokens"] == 10
    assert events[0]["data"]["output_tokens"] == 5


def test_translate_result_message_includes_cost_and_turns():
    msg = _make_result_message()
    events = translate_message(msg, session="test")
    done = events[0]
    assert done["data"]["num_turns"] == 1
    assert done["data"]["cost_usd"] == 0.01
    assert done["data"]["duration_api_ms"] == 800


def test_translate_text_includes_model():
    msg = _make_assistant_message([{"type": "text", "text": "hello"}])
    events = translate_message(msg, session="test")
    assert events[0]["data"].get("model") == "claude-opus-4-6"


def test_translate_system_message_compact_boundary():
    class FakeSystemMessage:
        def __init__(self):
            self.subtype = "compact_boundary"
            self.data = {
                "compactMetadata": {"trigger": "auto", "preTokens": 167000},
            }
    msg = FakeSystemMessage()
    events = translate_message(msg, session="test")
    assert len(events) == 1
    assert events[0]["event"] == "agent.compact"
    assert events[0]["data"]["pre_tokens"] == 167000
    assert events[0]["data"]["trigger"] == "auto"


def test_translate_system_message_unknown_subtype():
    class FakeSystemMessage:
        def __init__(self):
            self.subtype = "other_thing"
            self.data = {}
    msg = FakeSystemMessage()
    events = translate_message(msg, session="test")
    assert len(events) == 0


def test_translate_result_error_has_no_metadata():
    msg = _make_result_message(session_id="default", is_error=True)
    events = translate_message(msg, session="default")
    assert events[0]["event"] == "agent.error"
    assert "duration_ms" not in events[0]["data"]
    assert "model" not in events[0]["data"]


def test_translate_user_message_ignored():
    """UserMessage with text content should NOT produce agent.text events (#1379)."""
    from claude_agent_sdk.types import UserMessage, TextBlock

    msg = UserMessage(content=[TextBlock(text="Investigate the codebase")])
    events = translate_message(msg, session="default")
    assert events == []


# --- strip_silent_suffix integration in translate_message ---


def test_translate_message_strips_heartbeat_suffix():
    """Text with HEARTBEAT_OK suffix should have it stripped."""
    msg = _make_assistant_message([{"type": "text", "text": "Good morning briefing\nHEARTBEAT_OK"}])
    events = translate_message(msg, session="default")
    assert len(events) == 1
    assert events[0]["event"] == "agent.text"
    assert events[0]["data"]["text"] == "Good morning briefing"


def test_translate_message_drops_pure_silent_marker():
    """A text block containing just 'HEARTBEAT_OK' should produce no event."""
    msg = _make_assistant_message([{"type": "text", "text": "HEARTBEAT_OK"}])
    events = translate_message(msg, session="default")
    assert events == []


def test_translate_message_drops_silent_exact_variants():
    """'(HEARTBEAT_OK)' and '(silent)' also produce no event."""
    for marker in ("(HEARTBEAT_OK)", "(silent)"):
        msg = _make_assistant_message([{"type": "text", "text": marker}])
        events = translate_message(msg, session="default")
        assert events == [], f"Expected no events for {marker!r}, got {events}"


def test_translate_message_preserves_normal_text():
    """Normal text without any silent markers passes through unchanged."""
    msg = _make_assistant_message([{"type": "text", "text": "Hello there"}])
    events = translate_message(msg, session="default")
    assert len(events) == 1
    assert events[0]["data"]["text"] == "Hello there"


# --- _drain_response sub-agent handling ---


@pytest.mark.asyncio
async def test_drain_response_yields_text_after_subagent_result(tmp_path):
    """Main agent text after a sub-agent ResultMessage must not be lost."""
    from pathlib import Path
    from claude_agent_sdk.types import (
        AssistantMessage, UserMessage, ResultMessage,
        TextBlock, ToolUseBlock, ToolResultBlock,
    )

    messages = [
        # 1. Main agent decides to use Task tool
        AssistantMessage(
            content=[
                TextBlock(text="Let me explore the codebase."),
                ToolUseBlock(id="task_1", name="Task", input={"prompt": "explore"}),
            ],
            model="claude-opus-4-6",
        ),
        # 2. Sub-agent prompt (UserMessage — skipped by translate_message)
        UserMessage(
            content=[TextBlock(text="Explore the codebase")],
            parent_tool_use_id="task_1",
        ),
        # 3. Sub-agent response
        AssistantMessage(
            content=[TextBlock(text="Found interesting patterns.")],
            model="claude-opus-4-6",
            parent_tool_use_id="task_1",
        ),
        # 4. Sub-agent ResultMessage — must NOT stop the drain
        ResultMessage(
            subtype="result", duration_ms=500, duration_api_ms=400,
            is_error=False, num_turns=1, session_id="sub-session",
        ),
        # 5. Tool result back to main agent
        UserMessage(
            content=[ToolResultBlock(tool_use_id="task_1", content="Patterns found.")],
        ),
        # 6. Main agent's final text — THIS MUST BE YIELDED
        AssistantMessage(
            content=[TextBlock(text="Which approach do you prefer?")],
            model="claude-opus-4-6",
        ),
        # 7. Main agent ResultMessage — this ends the turn
        ResultMessage(
            subtype="result", duration_ms=2000, duration_api_ms=1800,
            is_error=False, num_turns=3, session_id="main-session",
        ),
    ]

    class FakeClient:
        async def receive_messages(self):
            for msg in messages:
                yield msg

        async def receive_response(self):
            for msg in messages:
                yield msg
                if isinstance(msg, ResultMessage):
                    return

    sessions = SessionManager(tmp_path / "sessions.json")
    sessions.get_or_create("test/session")
    runner = AgentRunner(sessions, LaneManager(), fera_home=tmp_path)

    events = []
    async for event in runner._drain_response(FakeClient(), "test/session"):
        events.append(event)

    event_types = [e["event"] for e in events]
    text_events = [e for e in events if e["event"] == "agent.text"]
    text_contents = [e["data"]["text"] for e in text_events]

    # The main agent's final text MUST be present
    assert "Which approach do you prefer?" in text_contents, (
        f"Main agent's final text lost! Got texts: {text_contents}"
    )
    # The turn should end with agent.done from the main ResultMessage
    assert event_types[-1] == "agent.done"
    # Only the top-level ResultMessage should produce agent.done (sub-agent's is suppressed)
    assert event_types.count("agent.done") == 1


@pytest.mark.asyncio
async def test_drain_response_stops_at_toplevel_result(tmp_path):
    """Without sub-agents, drain stops at the first ResultMessage as before."""
    from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

    messages = [
        AssistantMessage(content=[TextBlock(text="Hello")], model="claude-opus-4-6"),
        ResultMessage(
            subtype="result", duration_ms=100, duration_api_ms=80,
            is_error=False, num_turns=1, session_id="sess-1",
        ),
    ]

    class FakeClient:
        async def receive_messages(self):
            for msg in messages:
                yield msg

        async def receive_response(self):
            for msg in messages:
                yield msg
                if isinstance(msg, ResultMessage):
                    return

    sessions = SessionManager(tmp_path / "sessions.json")
    sessions.get_or_create("test/session")
    runner = AgentRunner(sessions, LaneManager(), fera_home=tmp_path)

    events = []
    async for event in runner._drain_response(FakeClient(), "test/session"):
        events.append(event)

    assert len(events) == 2
    assert events[0]["event"] == "agent.text"
    assert events[1]["event"] == "agent.done"


# --- AgentRunner interrupt support ---

def _make_runner(tmp_path, fera_home=None, **kwargs):
    sessions = SessionManager(tmp_path / "sessions.json")
    lanes = LaneManager()
    return AgentRunner(sessions, lanes, fera_home=fera_home or tmp_path, **kwargs)


def test_runner_has_no_active_client_initially(tmp_path):
    runner = _make_runner(tmp_path)
    assert runner.active_session("default") is False


def test_runner_interrupt_noop_when_not_active(tmp_path):
    runner = _make_runner(tmp_path)
    # Should not raise — interrupting a non-active session is a no-op
    asyncio.run(runner.interrupt("default"))


def test_interrupt_all_calls_interrupt_on_each_active_client(tmp_path):
    runner = _make_runner(tmp_path)
    calls = []

    class FakeClient:
        def __init__(self, name):
            self.name = name

        async def interrupt(self):
            calls.append(self.name)

    runner._active_clients["a"] = FakeClient("a")
    runner._active_clients["b"] = FakeClient("b")
    asyncio.run(runner.interrupt_all())
    assert sorted(calls) == ["a", "b"]


def test_interrupt_all_noop_when_no_active_clients(tmp_path):
    runner = _make_runner(tmp_path)
    asyncio.run(runner.interrupt_all())  # should not raise


# --- AgentRunner pool support ---

def test_runner_accepts_pool(tmp_path):
    sessions = SessionManager(tmp_path / "sessions.json")
    lanes = LaneManager()

    async def fake_factory(session_name: str, sdk_session_id=None):
        return None

    pool = ClientPool(factory=fake_factory)
    runner = AgentRunner(sessions, lanes, pool=pool)
    assert runner._pool is pool


def test_runner_without_pool_has_none(tmp_path):
    runner = _make_runner(tmp_path)
    assert runner._pool is None


# --- AgentRunner global MCP servers ---

def test_runner_defaults_global_mcp_servers_to_empty(tmp_path):
    runner = _make_runner(tmp_path)
    assert runner._global_mcp_servers == {}


def test_runner_stores_global_mcp_servers(tmp_path):
    servers = {"my_tool": {"type": "sse", "url": "https://example.com/sse"}}
    runner = _make_runner(tmp_path, global_mcp_servers=servers)
    assert runner._global_mcp_servers == servers


# --- Stale session retry ---

@pytest.mark.asyncio
async def test_run_turn_retries_on_stale_session(tmp_path):
    """When resume fails with ProcessError, clear session ID and retry."""
    from claude_agent_sdk._errors import ProcessError

    sessions = SessionManager(tmp_path / "sessions.json")
    sessions.create("default")
    sessions.set_sdk_session_id("main/default", "stale-session-id")
    lanes = LaneManager()
    runner = AgentRunner(sessions, lanes)

    call_count = 0

    async def fake_run_turn_ephemeral(session_name, text, sdk_session_id, prompt_mode="full", model=None, allowed_tools=None, agents=None, fork_session=False):
        nonlocal call_count
        call_count += 1
        if sdk_session_id == "stale-session-id":
            raise ProcessError("Command failed with exit code 1", exit_code=1)
        # Second call (without session ID) succeeds
        yield make_event("agent.done", session="default", data={})

    with patch.object(runner, "_run_turn_ephemeral", side_effect=fake_run_turn_ephemeral):
        events = []
        async for event in runner.run_turn("default", "hello"):
            events.append(event)

    assert call_count == 2
    assert events[-1]["event"] == "agent.done"
    # Session ID should have been cleared
    assert sessions.get("default").get("sdk_session_id") is None


@pytest.mark.asyncio
async def test_run_turn_no_retry_without_session_id(tmp_path):
    """ProcessError without a stored session ID should not retry."""
    from claude_agent_sdk._errors import ProcessError

    sessions = SessionManager(tmp_path / "sessions.json")
    sessions.create("default")
    lanes = LaneManager()
    runner = AgentRunner(sessions, lanes)

    async def fake_run_turn_ephemeral(session_name, text, sdk_session_id, prompt_mode="full", model=None, allowed_tools=None, agents=None, fork_session=False):
        raise ProcessError("Command failed with exit code 1", exit_code=1)
        yield  # make it a generator  # noqa: unreachable

    with patch.object(runner, "_run_turn_ephemeral", side_effect=fake_run_turn_ephemeral):
        with pytest.raises(ProcessError):
            async for _ in runner.run_turn("default", "hello"):
                pass


@pytest.mark.asyncio
async def test_run_turn_logs_turn_started_and_completed(tmp_path):
    """run_turn emits turn.started and turn.completed log entries."""
    import fera.logger as logger_mod
    from fera.logger import init_logger
    from fera.gateway.protocol import make_event

    logger_mod._logger = None  # clean entry state
    logger = init_logger(tmp_path / "logs")
    logged = []

    async def cb(entry):
        logged.append(entry)

    logger.set_broadcast(cb)
    try:
        runner = _make_runner(tmp_path)

        done_event = make_event("agent.done", session="main", data={
            "duration_ms": 500, "model": "claude-opus-4-6",
            "input_tokens": 100, "output_tokens": 50,
        })

        async def fake_ephemeral(session, text, sdk_session_id, prompt_mode="full", model=None, allowed_tools=None, agents=None, fork_session=False):
            yield done_event

        with patch.object(runner, "_run_turn_ephemeral", side_effect=fake_ephemeral):
            async for _ in runner.run_turn("main", "hello", source="web"):
                pass

        events_logged = [e["event"] for e in logged]
        assert "turn.started" in events_logged
        assert "turn.completed" in events_logged

        started = next(e for e in logged if e["event"] == "turn.started")
        assert started["session"] == "main"
        assert started["data"]["source"] == "web"

        completed = next(e for e in logged if e["event"] == "turn.completed")
        assert completed["data"]["duration_ms"] == 500
        assert completed["data"]["model"] == "claude-opus-4-6"
    finally:
        logger_mod._logger = None  # clean up singleton


@pytest.mark.asyncio
async def test_run_turn_logs_tool_call_and_result(tmp_path):
    import fera.logger as logger_mod
    from fera.logger import init_logger
    from fera.gateway.protocol import make_event

    logger_mod._logger = None  # clean entry state
    logger = init_logger(tmp_path / "logs")
    logged = []

    async def cb(entry):
        logged.append(entry)

    logger.set_broadcast(cb)
    try:
        runner = _make_runner(tmp_path)

        tool_use_event = make_event("agent.tool_use", session="main", data={
            "id": "t1", "name": "Bash", "input": {"command": "ls"},
        })
        tool_result_event = make_event("agent.tool_result", session="main", data={
            "tool_use_id": "t1", "content": "file.txt", "is_error": False,
        })
        done_event = make_event("agent.done", session="main", data={})

        async def fake_ephemeral(session, text, sdk_session_id, prompt_mode="full", model=None, allowed_tools=None, agents=None, fork_session=False):
            yield tool_use_event
            yield tool_result_event
            yield done_event

        with patch.object(runner, "_run_turn_ephemeral", side_effect=fake_ephemeral):
            async for _ in runner.run_turn("main", "hello"):
                pass

        events_logged = [e["event"] for e in logged]
        assert "tool.call" in events_logged
        assert "tool.result" in events_logged

        tool_call = next(e for e in logged if e["event"] == "tool.call")
        assert tool_call["data"]["tool_name"] == "Bash"
        assert "input_size" in tool_call["data"]

        tool_result = next(e for e in logged if e["event"] == "tool.result")
        assert tool_result["data"]["tool_name"] == "Bash"
        assert tool_result["data"]["is_error"] is False
    finally:
        logger_mod._logger = None


@pytest.mark.asyncio
async def test_run_turn_logs_turn_error(tmp_path):
    import fera.logger as logger_mod
    from fera.logger import init_logger

    logger_mod._logger = None  # clean entry state
    logger = init_logger(tmp_path / "logs")
    logged = []

    async def cb(entry):
        logged.append(entry)

    logger.set_broadcast(cb)
    try:
        runner = _make_runner(tmp_path)

        async def failing_ephemeral(session, text, sdk_session_id, prompt_mode="full", model=None, allowed_tools=None, agents=None, fork_session=False):
            raise RuntimeError("boom")
            yield  # make it a generator  # noqa: unreachable

        with patch.object(runner, "_run_turn_ephemeral", side_effect=failing_ephemeral):
            with pytest.raises(RuntimeError):
                async for _ in runner.run_turn("main", "hello"):
                    pass

        events_logged = [e["event"] for e in logged]
        assert "turn.started" in events_logged
        assert "turn.error" in events_logged

        error_entry = next(e for e in logged if e["event"] == "turn.error")
        assert error_entry["data"]["error"] == "boom"
        assert error_entry["level"] == "error"
    finally:
        logger_mod._logger = None


@pytest.mark.asyncio
async def test_run_turn_stamps_turn_source_on_events(tmp_path):
    """All events yielded by run_turn carry turn_source when source is provided."""
    runner = _make_runner(tmp_path)

    text_event = make_event("agent.text", session="default", data={"text": "hi"})
    done_event = make_event("agent.done", session="default", data={})

    async def fake_ephemeral(session, text, sdk_session_id, prompt_mode="full", model=None, allowed_tools=None, agents=None, fork_session=False):
        yield text_event
        yield done_event

    with patch.object(runner, "_run_turn_ephemeral", side_effect=fake_ephemeral):
        events = []
        async for event in runner.run_turn("default", "hello", source="telegram"):
            events.append(event)

    assert all(e.get("turn_source") == "telegram" for e in events)


@pytest.mark.asyncio
async def test_run_turn_no_turn_source_when_source_empty(tmp_path):
    """Events have no turn_source field when source is not provided."""
    runner = _make_runner(tmp_path)

    done_event = make_event("agent.done", session="default", data={})

    async def fake_ephemeral(session, text, sdk_session_id, prompt_mode="full", model=None, allowed_tools=None, agents=None, fork_session=False):
        yield done_event

    with patch.object(runner, "_run_turn_ephemeral", side_effect=fake_ephemeral):
        events = []
        async for event in runner.run_turn("default", "hello"):
            events.append(event)

    assert "turn_source" not in events[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name, tool_input, expected_key, expected_value", [
    ("WebFetch", {"url": "https://example.com", "prompt": "summarize"}, "url", "https://example.com"),
    ("WebSearch", {"query": "python asyncio"}, "query", "python asyncio"),
    ("Read", {"file_path": "/home/user/code/main.py"}, "path", "/home/user/code/main.py"),
    ("Write", {"file_path": "/tmp/output.txt", "content": "hello"}, "path", "/tmp/output.txt"),
    ("Skill", {"skill": "knowledge-ingest"}, "skill", "knowledge-ingest"),
])
async def test_run_turn_logs_web_tool_params(
    tmp_path, tool_name, tool_input, expected_key, expected_value,
):
    """WebFetch logs url, WebSearch logs query in tool.call events."""
    import fera.logger as logger_mod
    from fera.logger import init_logger

    logger_mod._logger = None
    logger = init_logger(tmp_path / "logs")
    logged = []

    async def cb(entry):
        logged.append(entry)

    logger.set_broadcast(cb)
    try:
        runner = _make_runner(tmp_path)

        tool_use_event = make_event("agent.tool_use", session="main", data={
            "id": "t1", "name": tool_name, "input": tool_input,
        })
        done_event = make_event("agent.done", session="main", data={})

        async def fake_ephemeral(session, text, sdk_session_id, prompt_mode="full", model=None, allowed_tools=None, agents=None, fork_session=False):
            yield tool_use_event
            yield done_event

        with patch.object(runner, "_run_turn_ephemeral", side_effect=fake_ephemeral):
            async for _ in runner.run_turn("main", "hello"):
                pass

        tool_call = next(e for e in logged if e["event"] == "tool.call")
        assert tool_call["data"][expected_key] == expected_value
    finally:
        logger_mod._logger = None


@pytest.mark.asyncio
async def test_ephemeral_runner_routes_to_session_agent(tmp_path, monkeypatch):
    """_run_turn_ephemeral uses workspace_dir from the session record."""
    sessions = SessionManager(tmp_path / "sessions.json", fera_home=tmp_path)
    sessions.create("coding-1", agent="forge")

    captured = {}

    # Stub out SystemPromptBuilder so no workspace files are needed
    class FakeBuilder:
        def __init__(self, path): pass
        def build(self, mode, canary_token=None): return "system prompt"

    monkeypatch.setattr("fera.prompt.SystemPromptBuilder", FakeBuilder)

    # Stub ClaudeAgentOptions and ClaudeSDKClient to avoid spawning a subprocess
    class FakeOptions:
        def __init__(self, **kwargs):
            captured["cwd"] = kwargs.get("cwd")

    class FakeClient:
        def __init__(self, options): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def query(self, text): pass
        async def receive_messages(self):
            yield _sdk_result()

    monkeypatch.setattr("fera.gateway.runner.ClaudeAgentOptions", FakeOptions)
    monkeypatch.setattr("fera.gateway.runner.ClaudeSDKClient", FakeClient)

    runner = AgentRunner(sessions, LaneManager())
    events = []
    async for event in runner.run_turn("forge/coding-1", "hello"):
        events.append(event)

    expected_ws = str(tmp_path / "agents" / "forge" / "workspace")
    assert captured.get("cwd") == expected_ws


def test_run_turn_ephemeral_accepts_prompt_mode(tmp_path):
    """run_turn signature accepts prompt_mode parameter."""
    import inspect
    from fera.gateway.runner import AgentRunner

    sig = inspect.signature(AgentRunner.run_turn)
    assert "prompt_mode" in sig.parameters
    assert sig.parameters["prompt_mode"].default == "full"


# --- AgentRunner clear_session ---

@pytest.mark.asyncio
async def test_clear_session_removes_sdk_session_id(tmp_path):
    from fera.gateway.runner import AgentRunner
    from fera.gateway.sessions import SessionManager
    from fera.gateway.lanes import LaneManager

    sessions_file = tmp_path / "data" / "sessions.json"
    sessions_file.parent.mkdir(parents=True)
    (tmp_path / "agents" / "main" / "workspace").mkdir(parents=True)
    sessions = SessionManager(sessions_file, fera_home=tmp_path)
    sessions.create("default", agent="main")
    sessions.set_sdk_session_id("main/default", "sdk-123")

    runner = AgentRunner(sessions=sessions, lanes=LaneManager())
    await runner.clear_session("main/default")

    info = sessions.get("main/default")
    assert info.get("sdk_session_id") is None


def test_runner_accepts_memory_writer():
    from fera.gateway.runner import AgentRunner
    from fera.gateway.sessions import SessionManager
    from fera.gateway.lanes import LaneManager
    from fera.gateway.memory_writer import MemoryWriter
    from unittest.mock import MagicMock

    # Should not raise
    runner = AgentRunner(
        sessions=MagicMock(spec=SessionManager),
        lanes=LaneManager(),
        memory_writer=MemoryWriter(),
    )
    assert runner._memory_writer is not None


@pytest.mark.asyncio
async def test_ephemeral_run_registers_pre_compact_hook(tmp_path, monkeypatch):
    """Verify PreCompact hook is wired into ClaudeAgentOptions for ephemeral turns."""
    from fera.gateway.memory_writer import MemoryWriter

    sessions = SessionManager(tmp_path / "sessions.json", fera_home=tmp_path)
    sessions.create("default", agent="main")

    captured = {}

    class FakeBuilder:
        def __init__(self, path): pass
        def build(self, mode, canary_token=None): return "system prompt"

    monkeypatch.setattr("fera.prompt.SystemPromptBuilder", FakeBuilder)

    class FakeOptions:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs
            self.hooks = None
            self.resume = None

    class FakeHookMatcher:
        def __init__(self, **kwargs):
            captured["hook_matcher_kwargs"] = kwargs

    class FakeClient:
        def __init__(self, options):
            captured["options"] = options

        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

        async def query(self, text): pass

        async def receive_messages(self):
            yield _sdk_result(session_id="sdk-abc")

    monkeypatch.setattr("fera.gateway.runner.ClaudeAgentOptions", FakeOptions)
    monkeypatch.setattr("fera.gateway.runner.ClaudeSDKClient", FakeClient)
    # HookMatcher is imported locally inside _run_turn_ephemeral, so patch the module.
    monkeypatch.setattr("claude_agent_sdk.HookMatcher", FakeHookMatcher)

    memory_writer = MemoryWriter()
    runner = AgentRunner(sessions, LaneManager(), memory_writer=memory_writer)

    async for _ in runner.run_turn("main/default", "hello"):
        pass

    options = captured["options"]
    assert options.hooks is not None, "hooks was not set on ClaudeAgentOptions"
    assert "PreCompact" in options.hooks, "PreCompact key missing from hooks"
    hooks_list = options.hooks["PreCompact"]
    assert len(hooks_list) == 1, "Expected exactly one HookMatcher in PreCompact list"


@pytest.mark.asyncio
async def test_ephemeral_hook_publishes_compact_notification(tmp_path, monkeypatch):
    """When runner has a bus, the PreCompact hook publishes an agent.text notification."""
    from fera.adapters.bus import EventBus
    from fera.gateway.memory_writer import MemoryWriter

    sessions = SessionManager(tmp_path / "sessions.json", fera_home=tmp_path)
    sessions.create("default", agent="main")

    captured = {}
    all_hook_matchers = []

    class FakeBuilder:
        def __init__(self, path): pass
        def build(self, mode, canary_token=None): return "system prompt"

    monkeypatch.setattr("fera.prompt.SystemPromptBuilder", FakeBuilder)

    class FakeOptions:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs
            self.hooks = None
            self.resume = None

    class FakeHookMatcher:
        def __init__(self, **kwargs):
            all_hook_matchers.append(kwargs)

    class FakeClient:
        def __init__(self, options):
            captured["options"] = options

        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

        async def query(self, text): pass

        async def receive_messages(self):
            yield _sdk_result(session_id="sdk-abc")

    monkeypatch.setattr("fera.gateway.runner.ClaudeAgentOptions", FakeOptions)
    monkeypatch.setattr("fera.gateway.runner.ClaudeSDKClient", FakeClient)
    monkeypatch.setattr("claude_agent_sdk.HookMatcher", FakeHookMatcher)

    bus = EventBus()
    received = []
    bus.subscribe("main/default", lambda event: received.append(event))

    memory_writer = MemoryWriter()
    memory_writer.archive_session = AsyncMock()
    runner = AgentRunner(sessions, LaneManager(), memory_writer=memory_writer, bus=bus)

    async for _ in runner.run_turn("main/default", "hello"):
        pass

    # Extract the compact hook function (first HookMatcher created)
    hook_fn = all_hook_matchers[0]["hooks"][0]

    # Fire the hook with trigger="auto"
    await hook_fn(
        {"session_id": "sdk-abc", "cwd": str(tmp_path), "transcript_path": "/tmp/t.jsonl",
         "hook_event_name": "PreCompact", "trigger": "auto"},
        None, {"signal": None},
    )

    assert len(received) == 1
    event = received[0]
    assert event["event"] == "agent.text"
    assert event["session"] == "main/default"
    assert "turn_source" not in event
    assert "compaction" in event["data"]["text"].lower() or "compact" in event["data"]["text"].lower()


@pytest.mark.asyncio
async def test_ephemeral_hook_skips_notification_without_bus(tmp_path, monkeypatch):
    """When runner has no bus, the PreCompact hook still works but doesn't notify."""
    from fera.gateway.memory_writer import MemoryWriter

    sessions = SessionManager(tmp_path / "sessions.json", fera_home=tmp_path)
    sessions.create("default", agent="main")

    captured = {}
    all_hook_matchers = []

    class FakeBuilder:
        def __init__(self, path): pass
        def build(self, mode, canary_token=None): return "system prompt"

    monkeypatch.setattr("fera.prompt.SystemPromptBuilder", FakeBuilder)

    class FakeOptions:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs
            self.hooks = None
            self.resume = None

    class FakeHookMatcher:
        def __init__(self, **kwargs):
            all_hook_matchers.append(kwargs)

    class FakeClient:
        def __init__(self, options):
            captured["options"] = options

        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

        async def query(self, text): pass

        async def receive_messages(self):
            yield _sdk_result(session_id="sdk-abc")

    monkeypatch.setattr("fera.gateway.runner.ClaudeAgentOptions", FakeOptions)
    monkeypatch.setattr("fera.gateway.runner.ClaudeSDKClient", FakeClient)
    monkeypatch.setattr("claude_agent_sdk.HookMatcher", FakeHookMatcher)

    memory_writer = MemoryWriter()
    memory_writer.archive_session = AsyncMock()
    runner = AgentRunner(sessions, LaneManager(), memory_writer=memory_writer)

    async for _ in runner.run_turn("main/default", "hello"):
        pass

    # Extract the compact hook function (first HookMatcher created) and invoke
    hook_fn = all_hook_matchers[0]["hooks"][0]
    result = await hook_fn(
        {"session_id": "sdk-abc", "cwd": str(tmp_path), "transcript_path": "/tmp/t.jsonl",
         "hook_event_name": "PreCompact", "trigger": "auto"},
        None, {"signal": None},
    )
    assert result == {}
    memory_writer.archive_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_clear_session_releases_pool_client(tmp_path):
    from unittest.mock import AsyncMock, MagicMock
    from fera.gateway.runner import AgentRunner
    from fera.gateway.sessions import SessionManager
    from fera.gateway.lanes import LaneManager

    sessions_file = tmp_path / "data" / "sessions.json"
    sessions_file.parent.mkdir(parents=True)
    (tmp_path / "agents" / "main" / "workspace").mkdir(parents=True)
    sessions = SessionManager(sessions_file, fera_home=tmp_path)
    sessions.create("default", agent="main")
    sessions.set_sdk_session_id("main/default", "sdk-123")

    pool = MagicMock()
    pool.release = AsyncMock()

    runner = AgentRunner(sessions=sessions, lanes=LaneManager(), pool=pool)
    await runner.clear_session("main/default")

    pool.release.assert_awaited_once_with("main/default")


def test_run_turn_accepts_model_parameter(tmp_path):
    """run_turn signature accepts model parameter."""
    import inspect
    from fera.gateway.runner import AgentRunner
    sig = inspect.signature(AgentRunner.run_turn)
    assert "model" in sig.parameters
    assert sig.parameters["model"].default is None


@pytest.mark.asyncio
async def test_ephemeral_runner_passes_model_to_options(tmp_path, monkeypatch):
    """_run_turn_ephemeral passes model= to ClaudeAgentOptions."""
    sessions = SessionManager(tmp_path / "sessions.json", fera_home=tmp_path)
    sessions.create("default", agent="main")

    captured = {}

    class FakeBuilder:
        def __init__(self, path): pass
        def build(self, mode, canary_token=None): return "system prompt"

    monkeypatch.setattr("fera.prompt.SystemPromptBuilder", FakeBuilder)

    class FakeOptions:
        def __init__(self, **kwargs):
            captured["model"] = kwargs.get("model")
            self.hooks = None
            self.resume = None

    class FakeClient:
        def __init__(self, options): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def query(self, text): pass
        async def receive_messages(self):
            yield _sdk_result()

    monkeypatch.setattr("fera.gateway.runner.ClaudeAgentOptions", FakeOptions)
    monkeypatch.setattr("fera.gateway.runner.ClaudeSDKClient", FakeClient)

    runner = AgentRunner(sessions, LaneManager(), fera_home=tmp_path)
    async for _ in runner.run_turn("main/default", "hello", model="claude-sonnet-4-6"):
        pass

    assert captured["model"] == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_ephemeral_runner_reads_model_from_agent_config(tmp_path, monkeypatch):
    """_run_turn_ephemeral reads model from agent config when no override given."""
    agent_dir = tmp_path / "agents" / "main"
    agent_dir.mkdir(parents=True)
    (agent_dir / "config.json").write_text('{"model": "haiku"}')
    (tmp_path / "models.json").write_text(
        '{"models": {"haiku": "claude-haiku-4-5-20251001"}}'
    )

    sessions = SessionManager(tmp_path / "sessions.json", fera_home=tmp_path)
    sessions.create("default", agent="main")

    captured = {}

    class FakeBuilder:
        def __init__(self, path): pass
        def build(self, mode, canary_token=None): return "system prompt"

    monkeypatch.setattr("fera.prompt.SystemPromptBuilder", FakeBuilder)

    class FakeOptions:
        def __init__(self, **kwargs):
            captured["model"] = kwargs.get("model")
            self.hooks = None
            self.resume = None

    class FakeClient:
        def __init__(self, options): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def query(self, text): pass
        async def receive_messages(self):
            yield _sdk_result()

    monkeypatch.setattr("fera.gateway.runner.ClaudeAgentOptions", FakeOptions)
    monkeypatch.setattr("fera.gateway.runner.ClaudeSDKClient", FakeClient)

    runner = AgentRunner(sessions, LaneManager(), fera_home=tmp_path)
    async for _ in runner.run_turn("main/default", "hello"):
        pass

    assert captured["model"] == "claude-haiku-4-5-20251001"


@pytest.mark.asyncio
async def test_ephemeral_runner_explicit_model_overrides_agent_config(tmp_path, monkeypatch):
    """Explicit model param to run_turn overrides agent config model."""
    agent_dir = tmp_path / "agents" / "main"
    agent_dir.mkdir(parents=True)
    (agent_dir / "config.json").write_text('{"model": "haiku"}')
    (tmp_path / "models.json").write_text(
        '{"models": {"haiku": "claude-haiku-4-5-20251001", "opus": "claude-opus-4-6"}}'
    )

    sessions = SessionManager(tmp_path / "sessions.json", fera_home=tmp_path)
    sessions.create("default", agent="main")

    captured = {}

    class FakeBuilder:
        def __init__(self, path): pass
        def build(self, mode, canary_token=None): return "system prompt"

    monkeypatch.setattr("fera.prompt.SystemPromptBuilder", FakeBuilder)

    class FakeOptions:
        def __init__(self, **kwargs):
            captured["model"] = kwargs.get("model")
            self.hooks = None
            self.resume = None

    class FakeClient:
        def __init__(self, options): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def query(self, text): pass
        async def receive_messages(self):
            yield _sdk_result()

    monkeypatch.setattr("fera.gateway.runner.ClaudeAgentOptions", FakeOptions)
    monkeypatch.setattr("fera.gateway.runner.ClaudeSDKClient", FakeClient)

    runner = AgentRunner(sessions, LaneManager(), fera_home=tmp_path)
    async for _ in runner.run_turn("main/default", "hello", model="opus"):
        pass

    assert captured["model"] == "claude-opus-4-6"


@pytest.mark.asyncio
async def test_set_model_calls_client_set_model(tmp_path):
    """set_model calls set_model on the active pooled client."""
    runner = _make_runner(tmp_path)

    mock_client = AsyncMock()
    runner._active_clients["main/default"] = mock_client

    await runner.set_model("main/default", "claude-opus-4-6")
    mock_client.set_model.assert_awaited_once_with("claude-opus-4-6")


@pytest.mark.asyncio
async def test_set_model_resolves_alias(tmp_path):
    """set_model resolves aliases via models.json."""
    (tmp_path / "models.json").write_text(
        '{"models": {"opus": "claude-opus-4-6"}}'
    )
    runner = _make_runner(tmp_path, fera_home=tmp_path)

    mock_client = AsyncMock()
    runner._active_clients["main/default"] = mock_client

    await runner.set_model("main/default", "opus")
    mock_client.set_model.assert_awaited_once_with("claude-opus-4-6")


@pytest.mark.asyncio
async def test_set_model_stores_override_when_no_active_client(tmp_path):
    """set_model stores model override when no client exists yet."""
    runner = _make_runner(tmp_path)
    await runner.set_model("main/default", "claude-opus-4-6")
    assert runner._session_models["main/default"] == "claude-opus-4-6"


@pytest.mark.asyncio
async def test_ephemeral_runner_uses_agent_allowed_tools(tmp_path, monkeypatch):
    """When agent config has allowed_tools, ephemeral runner passes them to ClaudeAgentOptions."""
    agent_dir = tmp_path / "agents" / "main"
    agent_dir.mkdir(parents=True)
    (agent_dir / "config.json").write_text(json.dumps({"allowed_tools": ["Read", "Glob"]}))

    sessions = SessionManager(tmp_path / "sessions.json", fera_home=tmp_path)
    sessions.create("default", agent="main")

    captured = {}

    class FakeBuilder:
        def __init__(self, path): pass
        def build(self, mode, canary_token=None): return "system prompt"

    monkeypatch.setattr("fera.prompt.SystemPromptBuilder", FakeBuilder)

    class FakeOptions:
        def __init__(self, **kwargs):
            captured["allowed_tools"] = kwargs.get("allowed_tools")
            self.hooks = None
            self.resume = None

    class FakeClient:
        def __init__(self, options): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def query(self, text): pass
        async def receive_messages(self):
            yield _sdk_result()

    monkeypatch.setattr("fera.gateway.runner.ClaudeAgentOptions", FakeOptions)
    monkeypatch.setattr("fera.gateway.runner.ClaudeSDKClient", FakeClient)

    runner = AgentRunner(sessions, LaneManager(), fera_home=tmp_path)
    async for _ in runner.run_turn("main/default", "hello"):
        pass

    assert captured["allowed_tools"] == ["Read", "Glob"]


@pytest.mark.asyncio
async def test_deactivate_session_releases_pool_but_keeps_sdk_session_id(tmp_path):
    from unittest.mock import AsyncMock, MagicMock
    from fera.gateway.runner import AgentRunner
    from fera.gateway.sessions import SessionManager
    from fera.gateway.lanes import LaneManager

    sessions_file = tmp_path / "data" / "sessions.json"
    sessions_file.parent.mkdir(parents=True)
    (tmp_path / "agents" / "main" / "workspace").mkdir(parents=True)
    sessions = SessionManager(sessions_file, fera_home=tmp_path)
    sessions.create("default", agent="main")
    sessions.set_sdk_session_id("main/default", "sdk-123")

    pool = MagicMock()
    pool.release = AsyncMock()

    runner = AgentRunner(sessions=sessions, lanes=LaneManager(), pool=pool)
    await runner.deactivate_session("main/default")

    pool.release.assert_awaited_once_with("main/default")
    # sdk_session_id must be preserved (unlike clear_session)
    info = sessions.get("main/default")
    assert info["sdk_session_id"] == "sdk-123"


@pytest.mark.asyncio
async def test_ephemeral_runner_sets_bypass_permissions(tmp_path, monkeypatch):
    """_run_turn_ephemeral must set permission_mode='bypassPermissions'."""
    sessions = SessionManager(tmp_path / "sessions.json", fera_home=tmp_path)
    sessions.create("default", agent="main")

    captured = {}

    class FakeBuilder:
        def __init__(self, path): pass
        def build(self, mode, canary_token=None): return "system prompt"

    monkeypatch.setattr("fera.prompt.SystemPromptBuilder", FakeBuilder)

    class FakeOptions:
        def __init__(self, **kwargs):
            captured["permission_mode"] = kwargs.get("permission_mode")
            self.hooks = None
            self.resume = None

    class FakeClient:
        def __init__(self, options): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def query(self, text): pass
        async def receive_messages(self):
            yield _sdk_result()

    monkeypatch.setattr("fera.gateway.runner.ClaudeAgentOptions", FakeOptions)
    monkeypatch.setattr("fera.gateway.runner.ClaudeSDKClient", FakeClient)

    runner = AgentRunner(sessions, LaneManager(), fera_home=tmp_path)
    async for _ in runner.run_turn("main/default", "hello"):
        pass

    assert captured["permission_mode"] == "bypassPermissions"


@pytest.mark.asyncio
async def test_oneshot_sets_bypass_permissions(tmp_path, monkeypatch):
    """run_oneshot must set permission_mode='bypassPermissions'."""
    (tmp_path / "agents" / "main" / "workspace").mkdir(parents=True)

    captured = {}

    class FakeBuilder:
        def __init__(self, path): pass
        def build(self, mode, canary_token=None): return "system prompt"

    monkeypatch.setattr("fera.prompt.SystemPromptBuilder", FakeBuilder)

    class FakeOptions:
        def __init__(self, **kwargs):
            captured["permission_mode"] = kwargs.get("permission_mode")

    async def fake_query(prompt, options):
        yield _sdk_result()

    monkeypatch.setattr("fera.gateway.runner.ClaudeAgentOptions", FakeOptions)
    monkeypatch.setattr("fera.gateway.runner.query", fake_query)

    sessions = SessionManager(tmp_path / "sessions.json", fera_home=tmp_path)
    runner = AgentRunner(sessions, LaneManager(), fera_home=tmp_path)
    async for _ in runner.run_oneshot("hello"):
        pass

    assert captured["permission_mode"] == "bypassPermissions"


@pytest.mark.asyncio
async def test_ephemeral_runner_uses_override_allowed_tools(tmp_path, monkeypatch):
    """When allowed_tools is passed to run_turn, it overrides the default tools."""
    sessions = SessionManager(tmp_path / "sessions.json", fera_home=tmp_path)
    sessions.create("default", agent="main")

    captured = {}

    class FakeBuilder:
        def __init__(self, path): pass
        def build(self, mode, canary_token=None): return "system prompt"

    monkeypatch.setattr("fera.prompt.SystemPromptBuilder", FakeBuilder)

    class FakeOptions:
        def __init__(self, **kwargs):
            captured["allowed_tools"] = kwargs.get("allowed_tools")
            self.hooks = None
            self.resume = None

    class FakeClient:
        def __init__(self, options): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def query(self, text): pass
        async def receive_messages(self):
            yield _sdk_result()

    monkeypatch.setattr("fera.gateway.runner.ClaudeAgentOptions", FakeOptions)
    monkeypatch.setattr("fera.gateway.runner.ClaudeSDKClient", FakeClient)

    runner = AgentRunner(sessions, LaneManager(), fera_home=tmp_path)
    async for _ in runner.run_turn("main/default", "hello", allowed_tools=["Read", "Bash"]):
        pass

    assert captured["allowed_tools"] == ["Read", "Bash"]


@pytest.mark.asyncio
async def test_oneshot_uses_override_allowed_tools(tmp_path, monkeypatch):
    """When allowed_tools is passed to run_oneshot, it overrides the default tools."""
    (tmp_path / "agents" / "main" / "workspace").mkdir(parents=True)

    captured = {}

    class FakeBuilder:
        def __init__(self, path): pass
        def build(self, mode, canary_token=None): return "system prompt"

    monkeypatch.setattr("fera.prompt.SystemPromptBuilder", FakeBuilder)

    class FakeOptions:
        def __init__(self, **kwargs):
            captured["allowed_tools"] = kwargs.get("allowed_tools")

    async def fake_query(prompt, options):
        yield _sdk_result()

    monkeypatch.setattr("fera.gateway.runner.ClaudeAgentOptions", FakeOptions)
    monkeypatch.setattr("fera.gateway.runner.query", fake_query)

    sessions = SessionManager(tmp_path / "sessions.json", fera_home=tmp_path)
    runner = AgentRunner(sessions, LaneManager(), fera_home=tmp_path)
    async for _ in runner.run_oneshot("hello", allowed_tools=["WebSearch", "WebFetch"]):
        pass

    assert captured["allowed_tools"] == ["WebSearch", "WebFetch"]


# --- AskUserQuestion support ---

def test_runner_has_empty_pending_questions_initially(tmp_path):
    runner = _make_runner(tmp_path)
    assert runner._pending_questions == {}


@pytest.mark.asyncio
async def test_answer_question_resolves_future(tmp_path):
    runner = _make_runner(tmp_path)
    fut = asyncio.get_event_loop().create_future()
    runner._pending_questions["main/default:q1"] = fut

    await runner.answer_question("main/default", "main/default:q1", {"Q?": "A"})

    assert fut.done()
    assert fut.result() == {"Q?": "A"}
    assert "main/default:q1" not in runner._pending_questions


@pytest.mark.asyncio
async def test_answer_question_unknown_id_is_noop(tmp_path):
    runner = _make_runner(tmp_path)
    await runner.answer_question("main/default", "nonexistent", {"Q?": "A"})
    # should not raise


@pytest.mark.asyncio
async def test_cancel_pending_questions_cancels_for_session(tmp_path):
    runner = _make_runner(tmp_path)
    fut1 = asyncio.get_event_loop().create_future()
    fut2 = asyncio.get_event_loop().create_future()
    fut3 = asyncio.get_event_loop().create_future()
    runner._pending_questions["main/default:q1"] = fut1
    runner._pending_questions["main/default:q2"] = fut2
    runner._pending_questions["main/other:q3"] = fut3

    runner.cancel_pending_questions("main/default")

    assert fut1.cancelled()
    assert fut2.cancelled()
    assert not fut3.cancelled()
    assert "main/default:q1" not in runner._pending_questions
    assert "main/default:q2" not in runner._pending_questions
    assert "main/other:q3" in runner._pending_questions


@pytest.mark.asyncio
async def test_clear_session_cancels_pending_questions(tmp_path):
    sessions_file = tmp_path / "data" / "sessions.json"
    sessions_file.parent.mkdir(parents=True)
    (tmp_path / "agents" / "main" / "workspace").mkdir(parents=True)
    sessions = SessionManager(sessions_file, fera_home=tmp_path)
    sessions.create("default", agent="main")

    runner = AgentRunner(sessions=sessions, lanes=LaneManager())
    fut = asyncio.get_event_loop().create_future()
    runner._pending_questions["main/default:q1"] = fut

    await runner.clear_session("main/default")

    assert fut.cancelled()
    assert "main/default:q1" not in runner._pending_questions


@pytest.mark.asyncio
async def test_interrupt_cancels_pending_questions(tmp_path):
    runner = _make_runner(tmp_path)
    mock_client = AsyncMock()
    runner._active_clients["main/default"] = mock_client

    fut = asyncio.get_event_loop().create_future()
    runner._pending_questions["main/default:q1"] = fut

    await runner.interrupt("main/default")

    assert fut.cancelled()
    mock_client.interrupt.assert_awaited_once()


def test_has_pending_questions_true_when_question_exists(tmp_path):
    runner = _make_runner(tmp_path)
    # Use a sentinel instead of a real Future — _has_pending_questions only checks keys.
    runner._pending_questions["coding/dm-alex:abc-123"] = object()
    assert runner._has_pending_questions("coding/dm-alex") is True


def test_has_pending_questions_false_when_empty(tmp_path):
    runner = _make_runner(tmp_path)
    assert runner._has_pending_questions("coding/dm-alex") is False


def test_has_pending_questions_false_for_other_session(tmp_path):
    runner = _make_runner(tmp_path)
    runner._pending_questions["main/dm-alex:abc-123"] = object()
    assert runner._has_pending_questions("coding/dm-alex") is False


@pytest.mark.asyncio
async def test_build_can_use_tool_auto_approves_non_ask_tools(tmp_path):
    """Non-AskUserQuestion tools get auto-approved."""
    from fera.adapters.bus import EventBus

    runner = _make_runner(tmp_path, bus=EventBus())
    callback = runner._build_can_use_tool("main/default")

    result = await callback("Bash", {"command": "ls"}, None)
    # PermissionResultAllow has updated_input
    assert result.updated_input == {"command": "ls"}


@pytest.mark.asyncio
async def test_build_can_use_tool_publishes_event_and_awaits(tmp_path):
    """AskUserQuestion publishes agent.user_question and awaits Future."""
    from fera.adapters.bus import EventBus

    bus = EventBus()
    received = []
    bus.subscribe("main/default", lambda e: received.append(e))

    runner = _make_runner(tmp_path, bus=bus)
    callback = runner._build_can_use_tool("main/default")

    questions_input = {
        "questions": [
            {"question": "Which DB?", "header": "DB", "options": [
                {"label": "Postgres", "description": "SQL"},
                {"label": "SQLite", "description": "Embedded"},
            ], "multiSelect": False}
        ]
    }

    # Run callback in background — it will await the Future
    task = asyncio.create_task(callback("AskUserQuestion", questions_input, None))
    await asyncio.sleep(0.01)  # let the event publish

    # Verify event was published
    assert len(received) == 1
    event = received[0]
    assert event["event"] == "agent.user_question"
    assert event["data"]["questions"] == questions_input["questions"]
    question_id = event["data"]["question_id"]
    assert question_id.startswith("main/default:")

    # Resolve the Future
    await runner.answer_question("main/default", question_id, {"Which DB?": "Postgres"})
    result = await task

    assert result.updated_input["answers"] == {"Which DB?": "Postgres"}
    assert result.updated_input["questions"] == questions_input["questions"]


@pytest.mark.asyncio
async def test_build_can_use_tool_cancelled_future_returns_deny(tmp_path):
    """Cancelled Future returns PermissionResultDeny."""
    from fera.adapters.bus import EventBus

    bus = EventBus()
    received = []
    bus.subscribe("main/default", lambda e: received.append(e))

    runner = _make_runner(tmp_path, bus=bus)
    callback = runner._build_can_use_tool("main/default")

    task = asyncio.create_task(callback("AskUserQuestion", {"questions": []}, None))
    await asyncio.sleep(0.01)

    question_id = received[0]["data"]["question_id"]
    runner.cancel_pending_questions("main/default")

    result = await task
    assert result.message == "Question cancelled (session cleared)"


@pytest.mark.asyncio
async def test_ephemeral_runner_passes_can_use_tool(tmp_path, monkeypatch):
    """_run_turn_ephemeral passes can_use_tool to ClaudeAgentOptions."""
    from fera.adapters.bus import EventBus

    sessions = SessionManager(tmp_path / "sessions.json", fera_home=tmp_path)
    sessions.create("default", agent="main")

    captured = {}

    class FakeBuilder:
        def __init__(self, path): pass
        def build(self, mode, canary_token=None): return "system prompt"

    monkeypatch.setattr("fera.prompt.SystemPromptBuilder", FakeBuilder)

    class FakeOptions:
        def __init__(self, **kwargs):
            captured["can_use_tool"] = kwargs.get("can_use_tool")
            captured["hooks"] = None
            self.hooks = None
            self.resume = None

    class FakeClient:
        def __init__(self, options): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def query(self, text): pass
        async def receive_messages(self):
            yield _sdk_result()

    monkeypatch.setattr("fera.gateway.runner.ClaudeAgentOptions", FakeOptions)
    monkeypatch.setattr("fera.gateway.runner.ClaudeSDKClient", FakeClient)

    runner = AgentRunner(sessions, LaneManager(), fera_home=tmp_path, bus=EventBus())
    async for _ in runner.run_turn("main/default", "hello"):
        pass

    assert captured["can_use_tool"] is not None
    assert callable(captured["can_use_tool"])


@pytest.mark.asyncio
async def test_ephemeral_runner_has_continue_hook(tmp_path, monkeypatch):
    """_run_turn_ephemeral includes continue hook in PreToolUse."""
    sessions = SessionManager(tmp_path / "sessions.json", fera_home=tmp_path)
    sessions.create("default", agent="main")

    captured = {}

    class FakeBuilder:
        def __init__(self, path): pass
        def build(self, mode, canary_token=None): return "system prompt"

    monkeypatch.setattr("fera.prompt.SystemPromptBuilder", FakeBuilder)

    class FakeOptions:
        def __init__(self, **kwargs):
            self.hooks = None
            self.resume = None

        # Allow attribute setting so runner can do options.hooks = ...
        def __setattr__(self, name, value):
            if name == "hooks":
                captured["hooks"] = value
            object.__setattr__(self, name, value)

    class FakeClient:
        def __init__(self, options): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def query(self, text): pass
        async def receive_messages(self):
            yield _sdk_result()

    monkeypatch.setattr("fera.gateway.runner.ClaudeAgentOptions", FakeOptions)
    monkeypatch.setattr("fera.gateway.runner.ClaudeSDKClient", FakeClient)

    runner = AgentRunner(sessions, LaneManager(), fera_home=tmp_path)
    async for _ in runner.run_turn("main/default", "hello"):
        pass

    hooks = captured.get("hooks")
    assert hooks is not None
    assert "PreToolUse" in hooks


@pytest.mark.asyncio
async def test_deactivate_session_without_pool_is_noop(tmp_path):
    from fera.gateway.runner import AgentRunner
    from fera.gateway.sessions import SessionManager
    from fera.gateway.lanes import LaneManager

    sessions_file = tmp_path / "data" / "sessions.json"
    sessions_file.parent.mkdir(parents=True)
    (tmp_path / "agents" / "main" / "workspace").mkdir(parents=True)
    sessions = SessionManager(sessions_file, fera_home=tmp_path)
    sessions.create("default", agent="main")

    runner = AgentRunner(sessions=sessions, lanes=LaneManager())
    await runner.deactivate_session("main/default")  # should not raise


# --- Canary token detection in _drain_response ---


@pytest.mark.asyncio
async def test_ephemeral_runner_passes_canary_to_prompt_builder(tmp_path, monkeypatch):
    """_run_turn_ephemeral passes canary_token from session to SystemPromptBuilder.build()."""
    sessions = SessionManager(tmp_path / "sessions.json", fera_home=tmp_path)
    sessions.create("default", agent="main")

    # Get the generated canary token
    session_info = sessions.get("main/default")
    canary_token = session_info["canary_token"]

    captured = {}

    class FakeBuilder:
        def __init__(self, path): pass
        def build(self, mode, canary_token=None):
            captured["canary_token"] = canary_token
            return "system prompt"

    monkeypatch.setattr("fera.prompt.SystemPromptBuilder", FakeBuilder)

    class FakeOptions:
        def __init__(self, **kwargs):
            self.hooks = None
            self.resume = None

    class FakeClient:
        def __init__(self, options): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def query(self, text): pass
        async def receive_messages(self):
            yield _sdk_result()

    monkeypatch.setattr("fera.gateway.runner.ClaudeAgentOptions", FakeOptions)
    monkeypatch.setattr("fera.gateway.runner.ClaudeSDKClient", FakeClient)

    runner = AgentRunner(sessions, LaneManager(), fera_home=tmp_path)
    async for _ in runner.run_turn("main/default", "hello"):
        pass

    assert captured["canary_token"] is not None
    assert captured["canary_token"] == f"CANARY:{canary_token}"


@pytest.mark.asyncio
async def test_drain_response_detects_canary_in_text(tmp_path):
    """When agent output contains the canary token, emit agent.alert and stop."""
    from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

    canary = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
    canary_string = f"CANARY:{canary}"

    messages = [
        AssistantMessage(
            content=[TextBlock(text=f"Here is the token: {canary_string}")],
            model="claude-opus-4-6",
        ),
        ResultMessage(
            subtype="result", duration_ms=100, duration_api_ms=80,
            is_error=False, num_turns=1, session_id="sess-1",
        ),
    ]

    class FakeClient:
        def __init__(self):
            self.interrupted = False

        async def receive_messages(self):
            for msg in messages:
                yield msg

        async def interrupt(self):
            self.interrupted = True

    sessions = SessionManager(tmp_path / "sessions.json")
    sessions.get_or_create("test/session")
    runner = AgentRunner(sessions, LaneManager(), fera_home=tmp_path)

    client = FakeClient()
    events = []
    async for event in runner._drain_response(client, "test/session", canary_token=canary_string):
        events.append(event)

    event_types = [e["event"] for e in events]
    assert "agent.alert" in event_types
    alert = next(e for e in events if e["event"] == "agent.alert")
    assert alert["data"]["severity"] == "critical"
    assert client.interrupted


@pytest.mark.asyncio
async def test_drain_response_detects_canary_split_across_chunks(tmp_path):
    """Canary token split across two consecutive text chunks is still detected."""
    from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

    canary = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
    canary_string = f"CANARY:{canary}"
    split_point = len(canary_string) // 2

    messages = [
        AssistantMessage(
            content=[TextBlock(text=f"prefix {canary_string[:split_point]}")],
            model="claude-opus-4-6",
        ),
        AssistantMessage(
            content=[TextBlock(text=f"{canary_string[split_point:]} suffix")],
            model="claude-opus-4-6",
        ),
        ResultMessage(
            subtype="result", duration_ms=100, duration_api_ms=80,
            is_error=False, num_turns=1, session_id="sess-1",
        ),
    ]

    class FakeClient:
        def __init__(self):
            self.interrupted = False

        async def receive_messages(self):
            for msg in messages:
                yield msg

        async def interrupt(self):
            self.interrupted = True

    sessions = SessionManager(tmp_path / "sessions.json")
    sessions.get_or_create("test/session")
    runner = AgentRunner(sessions, LaneManager(), fera_home=tmp_path)

    client = FakeClient()
    events = []
    async for event in runner._drain_response(client, "test/session", canary_token=canary_string):
        events.append(event)

    event_types = [e["event"] for e in events]
    assert "agent.alert" in event_types
    assert client.interrupted


@pytest.mark.asyncio
async def test_drain_response_no_alert_without_canary(tmp_path):
    """Normal text without canary token produces no alert."""
    from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

    messages = [
        AssistantMessage(
            content=[TextBlock(text="Hello, how can I help?")],
            model="claude-opus-4-6",
        ),
        ResultMessage(
            subtype="result", duration_ms=100, duration_api_ms=80,
            is_error=False, num_turns=1, session_id="sess-1",
        ),
    ]

    class FakeClient:
        async def receive_messages(self):
            for msg in messages:
                yield msg

    sessions = SessionManager(tmp_path / "sessions.json")
    sessions.get_or_create("test/session")
    runner = AgentRunner(sessions, LaneManager(), fera_home=tmp_path)

    events = []
    async for event in runner._drain_response(FakeClient(), "test/session", canary_token="CANARY:deadbeefdeadbeefdeadbeefdeadbeef"):
        events.append(event)

    event_types = [e["event"] for e in events]
    assert "agent.alert" not in event_types
    assert "agent.text" in event_types
    assert "agent.done" in event_types


@pytest.mark.asyncio
async def test_drain_response_no_canary_check_when_token_none(tmp_path):
    """When canary_token is None, no scanning occurs (backward compat)."""
    from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

    messages = [
        AssistantMessage(
            content=[TextBlock(text="CANARY:fake_but_no_real_token_set")],
            model="claude-opus-4-6",
        ),
        ResultMessage(
            subtype="result", duration_ms=100, duration_api_ms=80,
            is_error=False, num_turns=1, session_id="sess-1",
        ),
    ]

    class FakeClient:
        async def receive_messages(self):
            for msg in messages:
                yield msg

    sessions = SessionManager(tmp_path / "sessions.json")
    sessions.get_or_create("test/session")
    runner = AgentRunner(sessions, LaneManager(), fera_home=tmp_path)

    events = []
    async for event in runner._drain_response(FakeClient(), "test/session"):
        events.append(event)

    event_types = [e["event"] for e in events]
    assert "agent.alert" not in event_types


# --- _drain_leftover: SDK background notification handling ---


@pytest.mark.asyncio
async def test_drain_leftover_returns_zero_when_pipe_empty(tmp_path):
    """Drain returns 0 quickly when no leftover messages in the pipe."""

    class FakeClient:
        async def receive_messages(self):
            await asyncio.get_event_loop().create_future()  # block forever
            yield  # never reached

    sessions = SessionManager(tmp_path / "sessions.json")
    sessions.get_or_create("test/session")
    runner = AgentRunner(sessions, LaneManager(), fera_home=tmp_path)

    count = await runner._drain_leftover(FakeClient(), "test/session")
    assert count == 0


@pytest.mark.asyncio
async def test_drain_leftover_consumes_notification_messages(tmp_path):
    """Drain consumes leftover notification messages from the pipe."""
    from claude_agent_sdk.types import (
        AssistantMessage, UserMessage, ResultMessage, TextBlock,
    )

    class FakeClient:
        def __init__(self):
            self._queue = asyncio.Queue()
            # Simulate leftover notification: UserMessage + AssistantMessage + ResultMessage
            self._queue.put_nowait(UserMessage(
                content=[TextBlock(text="<task-notification>task failed</task-notification>")],
            ))
            self._queue.put_nowait(AssistantMessage(
                content=[TextBlock(text="That notification is resolved.")],
                model="claude-opus-4-6",
            ))
            self._queue.put_nowait(ResultMessage(
                subtype="result", duration_ms=100, duration_api_ms=80,
                is_error=False, num_turns=1, session_id="sdk-123",
            ))

        async def receive_messages(self):
            while True:
                msg = await self._queue.get()
                yield msg

    sessions = SessionManager(tmp_path / "sessions.json")
    sessions.get_or_create("test/session")
    runner = AgentRunner(sessions, LaneManager(), fera_home=tmp_path)

    count = await runner._drain_leftover(FakeClient(), "test/session")
    assert count == 3


@pytest.mark.asyncio
async def test_drain_leftover_preserves_subsequent_messages(tmp_path):
    """After draining leftovers, fresh messages are still readable."""
    from claude_agent_sdk.types import (
        AssistantMessage, ResultMessage, TextBlock,
    )

    class FakeClient:
        def __init__(self):
            self._queue = asyncio.Queue()
            # Leftover notification
            self._queue.put_nowait(AssistantMessage(
                content=[TextBlock(text="Notification text")],
                model="claude-opus-4-6",
            ))
            self._queue.put_nowait(ResultMessage(
                subtype="result", duration_ms=100, duration_api_ms=80,
                is_error=False, num_turns=1, session_id="sdk-123",
            ))

        async def receive_messages(self):
            while True:
                msg = await self._queue.get()
                yield msg

    sessions = SessionManager(tmp_path / "sessions.json")
    sessions.get_or_create("test/session")
    runner = AgentRunner(sessions, LaneManager(), fera_home=tmp_path)

    client = FakeClient()

    # Drain leftovers
    count = await runner._drain_leftover(client, "test/session")
    assert count == 2

    # Now add fresh messages and verify they're readable via _drain_response
    client._queue.put_nowait(AssistantMessage(
        content=[TextBlock(text="Fresh response")],
        model="claude-opus-4-6",
    ))
    client._queue.put_nowait(ResultMessage(
        subtype="result", duration_ms=200, duration_api_ms=180,
        is_error=False, num_turns=1, session_id="sdk-456",
    ))

    events = []
    async for event in runner._drain_response(client, "test/session"):
        events.append(event)

    text_events = [e for e in events if e["event"] == "agent.text"]
    assert len(text_events) == 1
    assert text_events[0]["data"]["text"] == "Fresh response"


@pytest.mark.asyncio
async def test_pooled_turn_drains_leftover_before_query(tmp_path, monkeypatch):
    """Two consecutive pooled turns — notification leftovers between them don't corrupt turn 2."""
    from claude_agent_sdk.types import (
        AssistantMessage, ResultMessage, TextBlock,
    )

    class FakeClient:
        def __init__(self):
            self._queue = asyncio.Queue()
            self._query_count = 0

        async def query(self, text):
            self._query_count += 1
            if self._query_count == 1:
                self._queue.put_nowait(AssistantMessage(
                    content=[TextBlock(text="Turn 1 response")],
                    model="claude-opus-4-6",
                ))
                self._queue.put_nowait(_sdk_result())
            elif self._query_count == 2:
                self._queue.put_nowait(AssistantMessage(
                    content=[TextBlock(text="Turn 2 response")],
                    model="claude-opus-4-6",
                ))
                self._queue.put_nowait(_sdk_result())

        async def receive_messages(self):
            while True:
                msg = await self._queue.get()
                yield msg

        async def set_model(self, model):
            pass

    client = FakeClient()

    class FakePool:
        _clients = {}

        async def acquire(self, session_name, sdk_session_id=None, fork_session=False):
            self._clients[session_name] = client
            return client

        def mark_active(self, session_name):
            pass

        def mark_idle(self, session_name):
            pass

        async def release(self, session_name):
            self._clients.pop(session_name, None)

    sessions = SessionManager(tmp_path / "sessions.json")
    sessions.get_or_create("test/session")
    runner = AgentRunner(sessions, LaneManager(), pool=FakePool(), fera_home=tmp_path)

    # Turn 1
    events1 = []
    async for event in runner.run_turn("test/session", "hello"):
        events1.append(event)

    texts1 = [e["data"]["text"] for e in events1 if e["event"] == "agent.text"]
    assert texts1 == ["Turn 1 response"]

    # Inject notification leftovers (simulates SDK background task notification)
    client._queue.put_nowait(AssistantMessage(
        content=[TextBlock(text="Notification: background task failed")],
        model="claude-opus-4-6",
    ))
    client._queue.put_nowait(_sdk_result())

    # Turn 2 — WITHOUT the fix, _drain_response reads notification's ResultMessage
    # and returns early, yielding "Notification: background task failed" instead of
    # "Turn 2 response"
    events2 = []
    async for event in runner.run_turn("test/session", "world"):
        events2.append(event)

    texts2 = [e["data"]["text"] for e in events2 if e["event"] == "agent.text"]
    assert texts2 == ["Turn 2 response"], (
        f"Expected Turn 2's own response, got leftover notification text: {texts2}"
    )


# --- Message queuing during active turn ---


@pytest.mark.asyncio
async def test_run_turn_queues_when_lane_busy(tmp_path):
    """When a turn is already running, run_turn queues the message and yields message.queued."""
    sessions = SessionManager(tmp_path / "sessions.json")
    sessions.create("default")
    lanes = LaneManager()
    runner = AgentRunner(sessions, lanes)

    turn_started = asyncio.Event()
    turn_proceed = asyncio.Event()

    async def slow_ephemeral(session, text, sdk_session_id, prompt_mode="full", model=None, allowed_tools=None, agents=None, fork_session=False):
        turn_started.set()
        await turn_proceed.wait()
        yield make_event("agent.text", session=session, data={"text": f"reply to: {text}"})
        yield make_event("agent.done", session=session, data={})

    with patch.object(runner, "_run_turn_ephemeral", side_effect=slow_ephemeral):
        # Start first turn (will block until turn_proceed is set)
        task1 = asyncio.create_task(_collect_events(runner.run_turn("default", "first")))
        await turn_started.wait()

        # Second call while first is running — should be queued
        events2 = []
        async for event in runner.run_turn("default", "second", source="mm"):
            events2.append(event)

        assert len(events2) == 1
        assert events2[0]["event"] == "message.queued"
        assert events2[0]["data"]["text"] == "second"

        # Let first turn finish
        turn_proceed.set()
        events1 = await task1

    # First turn should include its own response AND a follow-up from the queued message
    texts = [e["data"]["text"] for e in events1 if e["event"] == "agent.text"]
    assert "reply to: first" in texts
    assert "reply to: second" in texts


@pytest.mark.asyncio
async def test_queued_messages_combined_with_newlines(tmp_path):
    """Multiple queued messages are joined with newlines into a single turn."""
    sessions = SessionManager(tmp_path / "sessions.json")
    sessions.create("default")
    lanes = LaneManager()
    runner = AgentRunner(sessions, lanes)

    turn_started = asyncio.Event()
    turn_proceed = asyncio.Event()
    received_texts = []

    async def capturing_ephemeral(session, text, sdk_session_id, prompt_mode="full", model=None, allowed_tools=None, agents=None, fork_session=False):
        received_texts.append(text)
        if not turn_started.is_set():
            turn_started.set()
            await turn_proceed.wait()
        yield make_event("agent.done", session=session, data={})

    with patch.object(runner, "_run_turn_ephemeral", side_effect=capturing_ephemeral):
        task1 = asyncio.create_task(_collect_events(runner.run_turn("default", "first")))
        await turn_started.wait()

        # Queue two messages
        async for _ in runner.run_turn("default", "second"):
            pass
        async for _ in runner.run_turn("default", "third"):
            pass

        turn_proceed.set()
        await task1

    assert received_texts[0] == "first"
    assert received_texts[1] == "second\nthird"


@pytest.mark.asyncio
async def test_queued_turn_gets_turn_source(tmp_path):
    """Events from queued follow-up turns carry a turn_source."""
    sessions = SessionManager(tmp_path / "sessions.json")
    sessions.create("default")
    lanes = LaneManager()
    runner = AgentRunner(sessions, lanes)

    turn_started = asyncio.Event()
    turn_proceed = asyncio.Event()

    async def fake_ephemeral(session, text, sdk_session_id, prompt_mode="full", model=None, allowed_tools=None, agents=None, fork_session=False):
        if not turn_started.is_set():
            turn_started.set()
            await turn_proceed.wait()
        yield make_event("agent.text", session=session, data={"text": text})
        yield make_event("agent.done", session=session, data={})

    with patch.object(runner, "_run_turn_ephemeral", side_effect=fake_ephemeral):
        task1 = asyncio.create_task(_collect_events(runner.run_turn("default", "first", source="mm")))
        await turn_started.wait()

        async for _ in runner.run_turn("default", "second", source="mm"):
            pass

        turn_proceed.set()
        events = await task1

    # All events (including follow-up turn) should have turn_source
    sourced = [e for e in events if e.get("turn_source")]
    assert len(sourced) == len(events)


@pytest.mark.asyncio
async def test_no_queue_when_lane_free(tmp_path):
    """When no turn is active, run_turn proceeds normally without queuing."""
    runner = _make_runner(tmp_path)

    done_event = make_event("agent.done", session="default", data={})

    async def fake_ephemeral(session, text, sdk_session_id, prompt_mode="full", model=None, allowed_tools=None, agents=None, fork_session=False):
        yield done_event

    with patch.object(runner, "_run_turn_ephemeral", side_effect=fake_ephemeral):
        events = []
        async for event in runner.run_turn("default", "hello"):
            events.append(event)

    assert len(events) == 1
    assert events[0]["event"] == "agent.done"
    # No message.queued event
    assert not any(e["event"] == "message.queued" for e in events)


@pytest.mark.asyncio
async def test_queue_isolates_sessions(tmp_path):
    """Queued messages don't leak between sessions."""
    sessions = SessionManager(tmp_path / "sessions.json")
    sessions.create("s1")
    sessions.create("s2")
    lanes = LaneManager()
    runner = AgentRunner(sessions, lanes)

    s1_started = asyncio.Event()
    s1_proceed = asyncio.Event()
    received_texts = []

    async def capturing_ephemeral(session, text, sdk_session_id, prompt_mode="full", model=None, allowed_tools=None, agents=None, fork_session=False):
        received_texts.append((session, text))
        if session == "s1" and not s1_started.is_set():
            s1_started.set()
            await s1_proceed.wait()
        yield make_event("agent.done", session=session, data={})

    with patch.object(runner, "_run_turn_ephemeral", side_effect=capturing_ephemeral):
        # Start turn on s1 (blocks)
        task1 = asyncio.create_task(_collect_events(runner.run_turn("s1", "for-s1")))
        await s1_started.wait()

        # Queue on s1
        async for _ in runner.run_turn("s1", "queued-for-s1"):
            pass

        # s2 should run immediately (different session, not blocked)
        events_s2 = []
        async for event in runner.run_turn("s2", "for-s2"):
            events_s2.append(event)

        assert any(e["event"] == "agent.done" for e in events_s2)

        s1_proceed.set()
        await task1

    # s1 should have processed both messages
    s1_texts = [t for s, t in received_texts if s == "s1"]
    assert "for-s1" in s1_texts
    assert "queued-for-s1" in s1_texts


async def _collect_events(agen) -> list[dict]:
    """Helper to collect all events from an async generator into a list."""
    events = []
    async for event in agen:
        events.append(event)
    return events


@pytest.mark.asyncio
async def test_drain_response_timeout_on_hung_reader(tmp_path):
    """_drain_response raises TimeoutError when the SDK reader hangs."""

    messages = [
        _make_assistant_message([{"type": "text", "text": "Starting work..."}]),
        # After yielding one message, the iterator hangs forever
    ]

    class HangingClient:
        async def receive_messages(self):
            for msg in messages:
                yield msg
            # Simulate a dead reader — hang forever
            await asyncio.sleep(999999)

    sessions = SessionManager(tmp_path / "sessions.json")
    sessions.get_or_create("test/session")
    runner = AgentRunner(sessions, LaneManager(), fera_home=tmp_path)

    events = []
    with pytest.raises(asyncio.TimeoutError):
        async for event in runner._drain_response(
            HangingClient(), "test/session",
            inactivity_timeout=0.1,  # 100ms for fast test
        ):
            events.append(event)

    # Should have yielded the first message before hanging
    assert any(e["event"] == "agent.text" for e in events)


@pytest.mark.asyncio
async def test_drain_response_no_timeout_on_normal_response(tmp_path):
    """Normal response stream completes without timeout."""

    messages = [
        _make_assistant_message([{"type": "text", "text": "Here is my response"}]),
        _sdk_result(),
    ]

    class NormalClient:
        async def receive_messages(self):
            for msg in messages:
                yield msg

    sessions = SessionManager(tmp_path / "sessions.json")
    sessions.get_or_create("test/session")
    runner = AgentRunner(sessions, LaneManager(), fera_home=tmp_path)

    events = []
    async for event in runner._drain_response(
        NormalClient(), "test/session",
        inactivity_timeout=1.0,
    ):
        events.append(event)

    assert any(e["event"] == "agent.text" for e in events)
    assert any(e["event"] == "agent.done" for e in events)


@pytest.mark.asyncio
async def test_drain_response_no_timeout_when_question_pending(tmp_path):
    """With a pending AskUserQuestion, the 300s timeout should NOT fire."""

    responded = asyncio.Event()

    class SlowQuestionClient:
        async def receive_messages(self):
            # Simulate SDK blocked in can_use_tool — no messages for a while
            await asyncio.wait_for(responded.wait(), timeout=5.0)
            # After question is answered, SDK resumes
            yield _make_assistant_message([{"type": "text", "text": "Got answer!"}])
            yield _sdk_result()

    sessions = SessionManager(tmp_path / "sessions.json")
    sessions.get_or_create("test/session")
    runner = AgentRunner(sessions, LaneManager(), fera_home=tmp_path)

    # Simulate a pending question for this session
    fut = asyncio.get_event_loop().create_future()
    runner._pending_questions["test/session:q-123"] = fut

    events = []

    async def drain():
        async for event in runner._drain_response(
            SlowQuestionClient(), "test/session",
            inactivity_timeout=0.1,  # Would fire instantly without the fix
        ):
            events.append(event)

    # Start draining in background
    drain_task = asyncio.create_task(drain())

    # Wait a bit — longer than inactivity_timeout but shorter than question timeout
    await asyncio.sleep(0.3)

    # Drain should still be running (not timed out)
    assert not drain_task.done(), "Drain timed out despite pending question"

    # "Answer" the question to unblock the client
    responded.set()
    await drain_task

    assert any(e["event"] == "agent.text" for e in events)
    assert any(e["event"] == "agent.done" for e in events)


def test_truncate_user_message_under_limit():
    """Messages under the limit pass through unchanged."""
    text = "Hello, this is a normal message."
    assert _truncate_user_message(text) == text


def test_truncate_user_message_over_limit():
    """Messages over the limit are truncated with a note prepended."""
    big = "x" * 600_000
    result = _truncate_user_message(big)
    assert len(result.encode("utf-8")) < MAX_USER_MESSAGE_BYTES + 500  # note overhead
    assert "[This message was truncated" in result
    assert "600000" in result  # original size mentioned


def test_truncate_user_message_preserves_head_and_tail():
    """Truncation keeps beginning and end of original content."""
    head = "HEAD_MARKER_" + "a" * 1000
    tail = "b" * 1000 + "_TAIL_MARKER"
    middle = "m" * 600_000
    big = head + middle + tail
    result = _truncate_user_message(big)
    assert "HEAD_MARKER_" in result
    assert "_TAIL_MARKER" in result


# --- Session forking ---


@pytest.mark.asyncio
async def test_execute_turn_forks_from_parent_session(tmp_path):
    """When fork_from is set and session has no sdk_session_id, use parent's ID with fork_session=True."""
    sessions = SessionManager(tmp_path / "sessions.json")
    sessions.create("parent-dm")
    sessions.set_sdk_session_id("main/parent-dm", "parent-sdk-id-123")
    sessions.create("child-thread")
    # child has NO sdk_session_id
    lanes = LaneManager()
    runner = AgentRunner(sessions, lanes)

    captured_args = {}

    async def fake_ephemeral(session_name, text, sdk_session_id, prompt_mode="full",
                              model=None, allowed_tools=None, agents=None, fork_session=False):
        captured_args["sdk_session_id"] = sdk_session_id
        captured_args["fork_session"] = fork_session
        yield make_event("agent.done", session=session_name, data={})

    with patch.object(runner, "_run_turn_ephemeral", side_effect=fake_ephemeral):
        async for _ in runner.run_turn(
            "main/child-thread", "hello",
            fork_from="main/parent-dm",
        ):
            pass

    assert captured_args["sdk_session_id"] == "parent-sdk-id-123"
    assert captured_args["fork_session"] is True


@pytest.mark.asyncio
async def test_execute_turn_skips_fork_when_session_has_own_id(tmp_path):
    """When session already has its own sdk_session_id, fork_from is ignored."""
    sessions = SessionManager(tmp_path / "sessions.json")
    sessions.create("parent-dm")
    sessions.set_sdk_session_id("main/parent-dm", "parent-sdk-id")
    sessions.create("child-thread")
    sessions.set_sdk_session_id("main/child-thread", "child-sdk-id")
    lanes = LaneManager()
    runner = AgentRunner(sessions, lanes)

    captured_args = {}

    async def fake_ephemeral(session_name, text, sdk_session_id, prompt_mode="full",
                              model=None, allowed_tools=None, agents=None, fork_session=False):
        captured_args["sdk_session_id"] = sdk_session_id
        captured_args["fork_session"] = fork_session
        yield make_event("agent.done", session=session_name, data={})

    with patch.object(runner, "_run_turn_ephemeral", side_effect=fake_ephemeral):
        async for _ in runner.run_turn(
            "main/child-thread", "hello",
            fork_from="main/parent-dm",
        ):
            pass

    # Should use the child's own session ID, not the parent's
    assert captured_args["sdk_session_id"] == "child-sdk-id"
    assert captured_args["fork_session"] is False


@pytest.mark.asyncio
async def test_execute_turn_no_fork_when_parent_has_no_sdk_id(tmp_path):
    """When parent session has no sdk_session_id, fork cannot happen."""
    sessions = SessionManager(tmp_path / "sessions.json")
    sessions.create("parent-dm")
    # parent has NO sdk_session_id
    sessions.create("child-thread")
    lanes = LaneManager()
    runner = AgentRunner(sessions, lanes)

    captured_args = {}

    async def fake_ephemeral(session_name, text, sdk_session_id, prompt_mode="full",
                              model=None, allowed_tools=None, agents=None, fork_session=False):
        captured_args["sdk_session_id"] = sdk_session_id
        captured_args["fork_session"] = fork_session
        yield make_event("agent.done", session=session_name, data={})

    with patch.object(runner, "_run_turn_ephemeral", side_effect=fake_ephemeral):
        async for _ in runner.run_turn(
            "main/child-thread", "hello",
            fork_from="main/parent-dm",
        ):
            pass

    assert captured_args["sdk_session_id"] is None
    assert captured_args["fork_session"] is False
