import asyncio
import json
import logging

import pytest
import pytest_asyncio
import websockets

from fera.gateway.server import Gateway


@pytest_asyncio.fixture
async def gateway(tmp_path):
    import fera.logger as logger_mod

    gw = Gateway(
        host="127.0.0.1",
        port=0,  # Random available port
        fera_home=tmp_path,
        log_dir=tmp_path / "logs",
    )
    await gw.start()
    yield gw
    await gw.stop()
    if logger_mod._logger is not None:
        logger_mod._logger.close()
        logger_mod._logger = None


def _ws_url(gateway):
    return f"ws://127.0.0.1:{gateway.port}"


def _make_req(method, params=None):
    import uuid

    return json.dumps(
        {
            "type": "req",
            "id": str(uuid.uuid4()),
            "method": method,
            "params": params or {},
        }
    )


async def _recv_response(ws, timeout=5):
    """Read WebSocket messages, skipping log.entry events, until we get an RPC response."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError("Timed out waiting for RPC response")
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        frame = json.loads(raw)
        # Skip log.entry events broadcast by the logger
        if frame.get("event") == "log.entry":
            continue
        return frame


@pytest.mark.asyncio
async def test_connect_handshake(gateway):
    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("connect"))
        frame = await _recv_response(ws)
        assert frame["type"] == "res"
        assert frame["ok"] is True
        assert "sessions" in frame["payload"]
        assert "version" in frame["payload"]


@pytest.mark.asyncio
async def test_session_list(gateway):
    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("session.list"))
        frame = await _recv_response(ws)
        assert frame["ok"] is True
        assert isinstance(frame["payload"]["sessions"], list)


@pytest.mark.asyncio
async def test_session_create(gateway):
    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("session.create", {"name": "test-session"}))
        frame = await _recv_response(ws)
        assert frame["ok"] is True
        assert frame["payload"]["id"] == "main/test-session"

        # Verify it shows up in list
        await ws.send(_make_req("session.list"))
        frame = await _recv_response(ws)
        names = [s["name"] for s in frame["payload"]["sessions"]]
        assert "test-session" in names


@pytest.mark.asyncio
async def test_interrupt_no_active_turn(gateway):
    """Interrupting when nothing is running should succeed (no-op)."""
    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("chat.interrupt", {"session": "default"}))
        frame = await _recv_response(ws)
        assert frame["ok"] is True


@pytest.mark.asyncio
async def test_interrupt_missing_session_param(gateway):
    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("chat.interrupt", {}))
        frame = await _recv_response(ws)
        assert frame["ok"] is False
        assert "session" in frame["error"].lower()


@pytest.mark.asyncio
async def test_unknown_method(gateway):
    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("nonexistent.method"))
        frame = await _recv_response(ws)
        assert frame["ok"] is False
        assert "unknown method" in frame["error"].lower()


@pytest.mark.asyncio
async def test_workspace_list(gateway, tmp_path):
    ws_dir = tmp_path / "agents" / "main" / "workspace"
    ws_dir.mkdir(parents=True, exist_ok=True)
    (ws_dir / "MEMORY.md").write_text("# Memory\n")

    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("workspace.list"))
        frame = await _recv_response(ws)
        assert frame["ok"] is True
        names = {f["name"] for f in frame["payload"]["files"]}
        assert "MEMORY.md" in names


@pytest.mark.asyncio
async def test_workspace_get(gateway, tmp_path):
    ws_dir = tmp_path / "agents" / "main" / "workspace"
    ws_dir.mkdir(parents=True, exist_ok=True)
    (ws_dir / "MEMORY.md").write_text("hello")

    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("workspace.get", {"path": "MEMORY.md"}))
        frame = await _recv_response(ws)
        assert frame["ok"] is True
        assert frame["payload"]["content"] == "hello"


@pytest.mark.asyncio
async def test_workspace_set(gateway, tmp_path):
    ws_dir = tmp_path / "agents" / "main" / "workspace"
    ws_dir.mkdir(parents=True, exist_ok=True)

    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(
            _make_req("workspace.set", {"path": "NEW.md", "content": "# New"})
        )
        frame = await _recv_response(ws)
        assert frame["ok"] is True
        assert (ws_dir / "NEW.md").read_text() == "# New"


@pytest.mark.asyncio
async def test_workspace_list_routes_to_agent_directory(gateway, tmp_path):
    """workspace.list uses the agent-specific directory, not the default agent's."""
    forge_ws = tmp_path / "agents" / "forge" / "workspace"
    forge_ws.mkdir(parents=True)
    (forge_ws / "forge-file.txt").write_text("hello")

    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("workspace.list", {"agent": "forge", "path": ""}))
        frame = await _recv_response(ws)

    assert frame["ok"] is True
    names = [f["name"] for f in frame["payload"]["files"]]
    assert "forge-file.txt" in names


@pytest.mark.asyncio
async def test_workspace_list_rejects_invalid_agent_name(gateway):
    """workspace.list returns an error for agent names containing path traversal."""
    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("workspace.list", {"agent": "../../etc"}))
        frame = await _recv_response(ws)

    assert frame["ok"] is False
    assert "invalid agent" in frame["error"].lower()


# --- MCP methods ---


@pytest.mark.asyncio
async def test_mcp_list_empty(gateway):
    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("mcp.list"))
        frame = await _recv_response(ws)
        assert frame["ok"] is True
        assert frame["payload"]["servers"] == []


# --- build_adapters_from_config ---


def test_build_adapters_empty_with_no_config():
    from fera.gateway.server import build_adapters_from_config

    assert build_adapters_from_config({}, workspace_dir=None) == []


def test_build_adapters_telegram_from_agent_config(tmp_path):
    from fera.gateway.server import build_adapters_from_config
    from fera.adapters.telegram import TelegramAdapter

    agent_config = {
        "adapters": {"telegram": {"bot_token": "fake-token", "allowed_users": {"alex": 123456}}}
    }
    adapters = build_adapters_from_config(agent_config, workspace_dir=tmp_path)
    assert len(adapters) == 1
    assert isinstance(adapters[0], TelegramAdapter)


def test_build_adapters_telegram_sets_fields(tmp_path):
    from fera.gateway.server import build_adapters_from_config

    agent_config = {
        "adapters": {
            "telegram": {
                "bot_token": "my-token",
                "allowed_users": {"alice": 111, "bob": 222},
            }
        }
    }
    adapters = build_adapters_from_config(agent_config, workspace_dir=tmp_path, agent_name="main")
    tg = adapters[0]
    assert tg._bot_token == "my-token"
    assert tg._allowed_users == {111, 222}
    assert tg.agent_name == "main"


def test_build_adapters_uses_literal_token(tmp_path):
    from fera.gateway.server import build_adapters_from_config

    agent_config = {
        "adapters": {"telegram": {"bot_token": "literal-token", "allowed_users": {"alex": 123}}}
    }
    adapters = build_adapters_from_config(agent_config, workspace_dir=tmp_path)
    assert adapters[0]._bot_token == "literal-token"


# --- Graceful shutdown ---


@pytest.mark.asyncio
async def test_stop_waits_for_inflight_tasks(tmp_path):
    """stop() should wait for in-flight chat tasks to complete."""
    import fera.logger as logger_mod

    gw = Gateway(
        host="127.0.0.1", port=0, fera_home=tmp_path, log_dir=tmp_path / "logs"
    )
    await gw.start()

    completed = False

    async def slow_task():
        nonlocal completed
        await asyncio.sleep(0.1)
        completed = True

    task = asyncio.create_task(slow_task())
    gw._chat_tasks.add(task)
    task.add_done_callback(gw._chat_tasks.discard)

    await gw.stop()
    if logger_mod._logger is not None:
        logger_mod._logger.close()
        logger_mod._logger = None
    assert completed is True


@pytest.mark.asyncio
async def test_stop_interrupts_after_drain_timeout(tmp_path):
    """After drain_timeout, active agent clients should be interrupted."""
    import fera.logger as logger_mod

    gw = Gateway(
        host="127.0.0.1", port=0, fera_home=tmp_path, log_dir=tmp_path / "logs"
    )
    await gw.start()

    interrupted = []

    class FakeClient:
        async def interrupt(self):
            interrupted.append(True)

    gw._runner._active_clients["sess"] = FakeClient()

    async def hanging_task():
        await asyncio.sleep(60)  # would hang forever without cancellation

    task = asyncio.create_task(hanging_task())
    gw._chat_tasks.add(task)
    task.add_done_callback(gw._chat_tasks.discard)

    await gw.stop(drain_timeout=0.1, interrupt_timeout=0.5)
    if logger_mod._logger is not None:
        logger_mod._logger.close()
        logger_mod._logger = None
    assert len(interrupted) == 1
    assert task.cancelled() or task.done()


@pytest.mark.asyncio
async def test_stop_force_cancels_after_interrupt_timeout(tmp_path):
    """Tasks still running after interrupt grace period should be force-cancelled."""
    import fera.logger as logger_mod

    gw = Gateway(
        host="127.0.0.1", port=0, fera_home=tmp_path, log_dir=tmp_path / "logs"
    )
    await gw.start()

    # Client interrupt does nothing — task keeps running
    class StubClient:
        async def interrupt(self):
            pass

    gw._runner._active_clients["sess"] = StubClient()

    force_cancelled = False

    async def unkillable_task():
        nonlocal force_cancelled
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            force_cancelled = True
            raise

    task = asyncio.create_task(unkillable_task())
    gw._chat_tasks.add(task)
    task.add_done_callback(gw._chat_tasks.discard)

    await gw.stop(drain_timeout=0.1, interrupt_timeout=0.1)
    if logger_mod._logger is not None:
        logger_mod._logger.close()
        logger_mod._logger = None
    assert force_cancelled is True


@pytest.mark.asyncio
async def test_stop_no_tasks_completes_quickly(tmp_path):
    """stop() with no in-flight tasks should complete immediately."""
    import fera.logger as logger_mod

    gw = Gateway(
        host="127.0.0.1", port=0, fera_home=tmp_path, log_dir=tmp_path / "logs"
    )
    await gw.start()
    await asyncio.wait_for(gw.stop(), timeout=2.0)
    if logger_mod._logger is not None:
        logger_mod._logger.close()
        logger_mod._logger = None


@pytest.mark.asyncio
async def test_stop_logs_drain_phases(tmp_path, caplog):
    """stop() should log the shutdown phases."""
    import fera.logger as logger_mod

    gw = Gateway(
        host="127.0.0.1", port=0, fera_home=tmp_path, log_dir=tmp_path / "logs"
    )
    await gw.start()

    class FakeClient:
        async def interrupt(self):
            pass

    gw._runner._active_clients["sess"] = FakeClient()

    async def hanging_task():
        await asyncio.sleep(60)

    task = asyncio.create_task(hanging_task())
    gw._chat_tasks.add(task)
    task.add_done_callback(gw._chat_tasks.discard)

    with caplog.at_level(logging.INFO, logger="fera.gateway.server"):
        await gw.stop(drain_timeout=0.1, interrupt_timeout=0.1)

    if logger_mod._logger is not None:
        logger_mod._logger.close()
        logger_mod._logger = None
    messages = caplog.text
    assert "Draining" in messages
    assert "Interrupting" in messages


@pytest.mark.asyncio
async def test_logs_list_contains_startup_entry_after_start(tmp_path):
    import fera.logger as logger_mod
    import datetime

    # Gateway.start() writes a system.startup entry, so today's date will be present.
    gw = Gateway(
        host="127.0.0.1", port=0, fera_home=tmp_path, log_dir=tmp_path / "logs"
    )
    await gw.start()
    async with websockets.connect(_ws_url(gw)) as ws:
        await ws.send(_make_req("logs.list"))
        frame = await _recv_response(ws)
    await gw.stop()
    if logger_mod._logger is not None:
        logger_mod._logger.close()
        logger_mod._logger = None
    assert frame["ok"] is True
    assert isinstance(frame["payload"]["dates"], list)
    assert str(datetime.date.today()) in frame["payload"]["dates"]


@pytest.mark.asyncio
async def test_logs_read_returns_empty_for_missing_date(tmp_path):
    import fera.logger as logger_mod

    gw = Gateway(
        host="127.0.0.1", port=0, fera_home=tmp_path, log_dir=tmp_path / "logs"
    )
    await gw.start()
    async with websockets.connect(_ws_url(gw)) as ws:
        await ws.send(_make_req("logs.read", {"date": "2026-01-01"}))
        frame = await _recv_response(ws)
    await gw.stop()
    if logger_mod._logger is not None:
        logger_mod._logger.close()
        logger_mod._logger = None
    assert frame["ok"] is True
    assert frame["payload"]["entries"] == []


@pytest.mark.asyncio
async def test_logs_read_returns_entries_for_existing_date(tmp_path):
    import fera.logger as logger_mod
    import datetime

    log_dir = tmp_path / "logs"
    today = datetime.date.today()
    log_file = log_dir / str(today.year) / f"{today.month:02d}" / f"{today}.jsonl"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text(
        json.dumps(
            {
                "ts": "2026-02-20T00:00:00.000Z",
                "level": "info",
                "event": "system.startup",
                "session": None,
                "data": {},
            }
        )
        + "\n"
    )

    gw = Gateway(host="127.0.0.1", port=0, fera_home=tmp_path, log_dir=log_dir)
    await gw.start()
    async with websockets.connect(_ws_url(gw)) as ws:
        await ws.send(_make_req("logs.read", {"date": str(today)}))
        frame = await _recv_response(ws)
    await gw.stop()
    if logger_mod._logger is not None:
        logger_mod._logger.close()
        logger_mod._logger = None
    assert frame["ok"] is True
    assert len(frame["payload"]["entries"]) >= 1
    assert any(e["event"] == "system.startup" for e in frame["payload"]["entries"])


@pytest.mark.asyncio
async def test_logs_read_missing_date_param(tmp_path):
    import fera.logger as logger_mod

    gw = Gateway(
        host="127.0.0.1", port=0, fera_home=tmp_path, log_dir=tmp_path / "logs"
    )
    await gw.start()
    async with websockets.connect(_ws_url(gw)) as ws:
        await ws.send(_make_req("logs.read", {}))
        frame = await _recv_response(ws)
    await gw.stop()
    if logger_mod._logger is not None:
        logger_mod._logger.close()
        logger_mod._logger = None
    assert frame["ok"] is False
    assert "date" in frame["error"].lower()


@pytest.mark.asyncio
async def test_logs_list_returns_available_dates(tmp_path):
    import fera.logger as logger_mod
    import datetime

    log_dir = tmp_path / "logs"
    today = datetime.date.today()
    log_file = log_dir / str(today.year) / f"{today.month:02d}" / f"{today}.jsonl"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("{}\n")

    gw = Gateway(host="127.0.0.1", port=0, fera_home=tmp_path, log_dir=log_dir)
    await gw.start()
    async with websockets.connect(_ws_url(gw)) as ws:
        await ws.send(_make_req("logs.list"))
        frame = await _recv_response(ws)
    await gw.stop()
    if logger_mod._logger is not None:
        logger_mod._logger.close()
        logger_mod._logger = None
    assert frame["ok"] is True
    assert str(today) in frame["payload"]["dates"]


@pytest.mark.asyncio
async def test_session_history_uses_composite_session_id(gateway):
    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("session.create", {"name": "histtest", "agent": "forge"}))
        await _recv_response(ws)

        # Use composite ID in session.history
        await ws.send(_make_req("session.history", {"session": "forge/histtest"}))
        resp = await _recv_response(ws)

    assert resp["ok"] is True
    assert resp["payload"]["messages"] == []


@pytest.mark.asyncio
async def test_session_history_requires_session_param(gateway):
    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("session.history", {}))
        resp = await _recv_response(ws)

    assert resp["ok"] is False
    assert "session" in resp["error"]


@pytest.mark.asyncio
async def test_startup_writes_log_entry(tmp_path):
    """Gateway.start() should write a system.startup log entry."""
    import fera.logger as logger_mod
    import datetime

    log_dir = tmp_path / "logs"
    gw = Gateway(host="127.0.0.1", port=0, fera_home=tmp_path, log_dir=log_dir)
    await gw.start()
    await gw.stop()

    today = datetime.date.today()
    log_file = log_dir / str(today.year) / f"{today.month:02d}" / f"{today}.jsonl"
    assert log_file.exists()
    entries = [
        json.loads(line) for line in log_file.read_text().splitlines() if line.strip()
    ]
    events = [e["event"] for e in entries]
    assert "system.startup" in events

    if logger_mod._logger is not None:
        logger_mod._logger.close()
        logger_mod._logger = None


@pytest.mark.asyncio
async def test_session_create_rejects_invalid_agent_name(gateway):
    """session.create returns an error for agent names containing path traversal."""
    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("session.create", {"name": "mysession", "agent": "../../etc"}))
        frame = await _recv_response(ws)

    assert frame["ok"] is False
    assert "invalid agent" in frame["error"].lower()


@pytest.mark.asyncio
async def test_session_create_returns_composite_id(gateway):
    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("session.create", {"name": "test-session"}))
        frame = await _recv_response(ws)
        assert frame["ok"] is True
        assert frame["payload"]["id"] == "main/test-session"
        assert frame["payload"]["name"] == "test-session"
        assert frame["payload"]["agent"] == "main"


@pytest.mark.asyncio
async def test_session_create_stores_agent(gateway):
    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(
            _make_req("session.create", {"name": "coding-1", "agent": "forge"})
        )
        frame = await _recv_response(ws)
        assert frame["ok"] is True
        assert frame["payload"]["id"] == "forge/coding-1"

        await ws.send(_make_req("session.list"))
        frame = await _recv_response(ws)
        sessions = {s["id"]: s for s in frame["payload"]["sessions"]}
        assert sessions["forge/coding-1"]["agent"] == "forge"
        assert sessions["forge/coding-1"]["name"] == "coding-1"


@pytest.mark.asyncio
async def test_gateway_has_stats(tmp_path):
    """Gateway creates a SessionStats instance."""
    import fera.logger as logger_mod

    gw = Gateway(
        host="127.0.0.1", port=0, fera_home=tmp_path, log_dir=tmp_path / "logs"
    )
    from fera.gateway.stats import SessionStats

    assert isinstance(gw._stats, SessionStats)
    if logger_mod._logger is not None:
        logger_mod._logger.close()
        logger_mod._logger = None


@pytest.mark.asyncio
async def test_session_create_defaults_agent_to_main(gateway):
    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("session.create", {"name": "default"}))
        frame = await _recv_response(ws)
        assert frame["ok"] is True

        await ws.send(_make_req("session.list"))
        frame = await _recv_response(ws)
        sessions = {s["id"]: s for s in frame["payload"]["sessions"]}
        assert sessions["main/default"]["agent"] == "main"


def test_gateway_creates_memory_writer(tmp_path):
    from fera.gateway.server import Gateway
    gw = Gateway(fera_home=tmp_path)
    assert gw._memory_writer is not None


def test_gateway_creates_heartbeat_scheduler(tmp_path):
    """Gateway accepts heartbeat_config and creates scheduler."""
    from fera.gateway.server import Gateway

    gw = Gateway(
        fera_home=tmp_path,
        heartbeat_config={
            "enabled": False,
            "interval_minutes": 30,
            "active_hours": "08:00-22:00",
            "session": "default",
        },
    )
    assert gw._heartbeat is not None


@pytest.mark.asyncio
async def test_session_list_includes_stats(gateway):
    """session.list merges stats into each session entry."""
    # Create a session and record a turn in stats
    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("session.create", {"name": "work"}))
        await _recv_response(ws)

    gateway._stats.record_turn("main/work", {
        "input_tokens": 5000, "output_tokens": 200,
        "model": "claude-opus-4-6",
    })

    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("session.list"))
        frame = await _recv_response(ws)

    assert frame["ok"] is True
    work_session = next(s for s in frame["payload"]["sessions"] if s["name"] == "work")
    assert "stats" in work_session
    assert work_session["stats"]["turns"] == 1
    assert work_session["stats"]["model"] == "claude-opus-4-6"


@pytest.mark.asyncio
async def test_connect_includes_stats(gateway):
    """connect response merges stats into session entries."""
    gateway._stats.record_turn("main/default", {
        "input_tokens": 1000, "output_tokens": 50,
    })
    # Auto-create the "default" session so it appears in the list
    gateway._sessions.get_or_create("default")

    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("connect"))
        frame = await _recv_response(ws)

    assert frame["ok"] is True
    default_session = next(
        (s for s in frame["payload"]["sessions"] if s["name"] == "default"), None
    )
    assert default_session is not None
    assert "stats" in default_session
    assert default_session["stats"]["turns"] == 1


@pytest.mark.asyncio
async def test_session_stats_method(gateway):
    """session.stats returns stats for a specific session."""
    gateway._stats.record_turn(
        "work",
        {
            "input_tokens": 5000,
            "output_tokens": 200,
            "model": "claude-opus-4-6",
            "duration_ms": 2000,
        },
    )
    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("session.stats", {"session": "work"}))
        frame = await _recv_response(ws)
    assert frame["ok"] is True
    assert frame["payload"]["turns"] == 1
    assert frame["payload"]["total_input_tokens"] == 5000
    assert frame["payload"]["model"] == "claude-opus-4-6"


@pytest.mark.asyncio
async def test_session_stats_all_sessions(gateway):
    """session.stats without session param returns all sessions."""
    gateway._stats.record_turn("a", {"input_tokens": 10, "output_tokens": 5})
    gateway._stats.record_turn("b", {"input_tokens": 20, "output_tokens": 10})
    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("session.stats", {}))
        frame = await _recv_response(ws)
    assert frame["ok"] is True
    assert "a" in frame["payload"]["sessions"]
    assert "b" in frame["payload"]["sessions"]


@pytest.mark.asyncio
async def test_cron_run_unknown_job(gateway, tmp_path):
    """cron.run returns error for unknown job name."""
    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("connect"))
        await _recv_response(ws)

        await ws.send(_make_req("cron.run", {"job": "nonexistent"}))
        frame = await _recv_response(ws)
        assert frame["ok"] is False
        assert "not found" in frame["error"].lower()


@pytest.mark.asyncio
async def test_cron_run_missing_job_param(gateway):
    """cron.run returns error when job name is missing."""
    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("connect"))
        await _recv_response(ws)

        await ws.send(_make_req("cron.run", {}))
        frame = await _recv_response(ws)
        assert frame["ok"] is False
        assert "job name required" in frame["error"].lower()


@pytest.mark.asyncio
async def test_agents_list_returns_existing_agents(tmp_path):
    """agents.list returns agent dirs that have a workspace/ subdir."""
    (tmp_path / "agents" / "main" / "workspace").mkdir(parents=True)
    (tmp_path / "agents" / "forge" / "workspace").mkdir(parents=True)
    (tmp_path / "agents" / "orphan").mkdir(parents=True)  # no workspace/, must be excluded

    gw = Gateway(host="127.0.0.1", port=0, fera_home=tmp_path, log_dir=tmp_path / "logs")
    await gw.start()
    try:
        async with websockets.connect(_ws_url(gw)) as ws:
            await ws.send(_make_req("agents.list"))
            frame = await _recv_response(ws)
    finally:
        await gw.stop()

    assert frame["ok"] is True
    assert "forge" in frame["payload"]["agents"]
    assert "main" in frame["payload"]["agents"]
    assert "orphan" not in frame["payload"]["agents"]


@pytest.mark.asyncio
async def test_agents_list_always_includes_default_agent(tmp_path):
    """DEFAULT_AGENT is always in agents list even if its directory doesn't exist."""
    # Don't create any agent directories
    gw = Gateway(host="127.0.0.1", port=0, fera_home=tmp_path, log_dir=tmp_path / "logs")
    await gw.start()
    try:
        async with websockets.connect(_ws_url(gw)) as ws:
            await ws.send(_make_req("agents.list"))
            frame = await _recv_response(ws)
    finally:
        await gw.stop()

    assert frame["ok"] is True
    from fera.config import DEFAULT_AGENT
    assert DEFAULT_AGENT in frame["payload"]["agents"]


@pytest.mark.asyncio
async def test_connect_includes_agents(gateway):
    """connect response includes agents list."""
    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("connect"))
        frame = await _recv_response(ws)

    assert frame["ok"] is True
    assert "agents" in frame["payload"]
    assert isinstance(frame["payload"]["agents"], list)


def test_gateway_creates_dream_cycle_scheduler(tmp_path):
    from fera.gateway.server import Gateway
    gw = Gateway(fera_home=tmp_path)
    assert gw._dream_cycle is not None


def test_gateway_accepts_dream_cycle_config(tmp_path):
    from fera.gateway.server import Gateway
    gw = Gateway(
        fera_home=tmp_path,
        dream_cycle_config={
            "enabled": True,
            "time": "03:00",
            "agents": ["main"],
        },
    )
    assert gw._dream_cycle is not None


@pytest.mark.asyncio
async def test_make_client_registers_pre_compact_hook(tmp_path, monkeypatch):
    """Verify PreCompact hook is wired into ClaudeAgentOptions for pool clients."""
    from fera.gateway.server import Gateway

    gw = Gateway(fera_home=tmp_path)
    # Ensure the session exists so _make_client can look it up
    gw._sessions.create("default", agent="main")

    captured = {}

    class FakeBuilder:
        def __init__(self, path): pass
        def build(self, mode): return "system prompt"

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

        async def connect(self): pass

    # _make_client imports ClaudeAgentOptions, ClaudeSDKClient, and HookMatcher
    # locally from claude_agent_sdk, so we patch the module directly.
    monkeypatch.setattr("claude_agent_sdk.ClaudeAgentOptions", FakeOptions)
    monkeypatch.setattr("claude_agent_sdk.ClaudeSDKClient", FakeClient)
    monkeypatch.setattr("claude_agent_sdk.HookMatcher", FakeHookMatcher)

    await gw._make_client("main/default")

    options = captured["options"]
    assert options.hooks is not None, "hooks was not set on ClaudeAgentOptions"
    assert "PreCompact" in options.hooks, "PreCompact key missing from hooks"
    hooks_list = options.hooks["PreCompact"]
    assert len(hooks_list) == 1, "Expected exactly one HookMatcher in PreCompact list"


def test_build_adapters_mattermost_from_config(tmp_path):
    from fera.gateway.server import build_adapters_from_config
    from fera.adapters.mattermost import MattermostAdapter

    agent_config = {
        "adapters": {
            "mattermost": {
                "url": "https://mm.example.com",
                "bot_token": "fake-token",
                "allowed_users": {"alex": "alex_mm"},
            }
        }
    }
    adapters = build_adapters_from_config(agent_config, workspace_dir=tmp_path)
    assert len(adapters) == 1
    assert isinstance(adapters[0], MattermostAdapter)


def test_build_adapters_mattermost_default_fields(tmp_path):
    from fera.gateway.server import build_adapters_from_config

    agent_config = {
        "adapters": {
            "mattermost": {
                "url": "https://mm.example.com",
                "bot_token": "fake-token",
                "allowed_users": {"alex": "alex_mm"},
                # no trusted
            }
        }
    }
    adapters = build_adapters_from_config(agent_config, workspace_dir=tmp_path)
    mm = adapters[0]
    assert mm._trusted is False
    assert mm.agent_name == "main"


def test_build_adapters_mattermost_sets_fields(tmp_path):
    from fera.gateway.server import build_adapters_from_config

    agent_config = {
        "adapters": {
            "mattermost": {
                "url": "https://mm.example.com",
                "bot_token": "my-token",
                "allowed_users": {"alice": "alice_mm", "bob": "bob_mm"},
                "trusted": True,
            }
        }
    }
    adapters = build_adapters_from_config(agent_config, workspace_dir=tmp_path)
    mm = adapters[0]
    assert mm._url == "https://mm.example.com"
    assert mm._bot_token == "my-token"
    assert sorted(mm._allowed_usernames) == ["alice_mm", "bob_mm"]
    assert mm._trusted is True


@pytest.mark.asyncio
async def test_chat_send_with_bare_session_publishes_composite_id(tmp_path):
    """chat.send with a bare session name like 'default' must publish events as 'main/default'."""
    import fera.logger as logger_mod
    gw = Gateway(host="127.0.0.1", port=0, fera_home=tmp_path, log_dir=tmp_path / "logs")
    await gw.start()

    published = []
    async def capture(event):
        published.append(event)
    gw._bus.subscribe("*", capture)

    try:
        async with websockets.connect(_ws_url(gw)) as ws:
            # Send chat.send with a bare session name
            await ws.send(_make_req("chat.send", {"session": "default", "text": "hi"}))
            await asyncio.sleep(0.3)
    finally:
        await gw.stop()
        if logger_mod._logger is not None:
            logger_mod._logger.close()
            logger_mod._logger = None

    user_events = [e for e in published if e.get("event") == "user.message"]
    assert len(user_events) == 1
    assert user_events[0]["session"] == "main/default"


def test_build_all_adapters_loads_all_agents(tmp_path):
    """build_all_adapters should start adapters for every agent directory."""
    from fera.gateway.server import build_all_adapters
    from fera.adapters.telegram import TelegramAdapter

    agents_dir = tmp_path / "agents"
    for agent_name, token in [("main", "token-main"), ("coding", "token-coding")]:
        agent_dir = agents_dir / agent_name
        (agent_dir / "workspace").mkdir(parents=True)
        (agent_dir / "data").mkdir()
        (agent_dir / "config.json").write_text(
            f'{{"adapters": {{"telegram": {{"bot_token": "{token}", "allowed_users": {{"alex": 123}}}}}}}}'
        )

    adapters = build_all_adapters(agents_dir)

    assert len(adapters) == 2
    tokens = {a._bot_token for a in adapters if isinstance(a, TelegramAdapter)}
    assert tokens == {"token-main", "token-coding"}


def test_build_all_adapters_empty_dir(tmp_path):
    from fera.gateway.server import build_all_adapters

    adapters = build_all_adapters(tmp_path / "agents")
    assert adapters == []


def test_build_adapters_mattermost_and_telegram_together(tmp_path):
    from fera.gateway.server import build_adapters_from_config
    from fera.adapters.telegram import TelegramAdapter
    from fera.adapters.mattermost import MattermostAdapter

    agent_config = {
        "adapters": {
            "telegram": {"bot_token": "tg-token", "allowed_users": {"alex": 123}},
            "mattermost": {
                "url": "https://mm.example.com",
                "bot_token": "mm-token",
                "allowed_users": {"alex": "alex_mm"},
            },
        }
    }
    adapters = build_adapters_from_config(agent_config, workspace_dir=tmp_path)
    types = {type(a).__name__ for a in adapters}
    assert "TelegramAdapter" in types
    assert "MattermostAdapter" in types


@pytest.mark.asyncio
async def test_make_client_uses_agent_allowed_tools(tmp_path, monkeypatch):
    """When agent config has allowed_tools, pool factory passes them to ClaudeAgentOptions."""
    from fera.gateway.server import Gateway

    agent_dir = tmp_path / "agents" / "main"
    agent_dir.mkdir(parents=True)
    (agent_dir / "config.json").write_text(json.dumps({"allowed_tools": ["Read", "Glob"]}))

    gw = Gateway(fera_home=tmp_path)
    gw._sessions.create("default", agent="main")

    captured = {}

    class FakeBuilder:
        def __init__(self, path): pass
        def build(self, mode): return "system prompt"

    monkeypatch.setattr("fera.prompt.SystemPromptBuilder", FakeBuilder)

    class FakeOptions:
        def __init__(self, **kwargs):
            captured["allowed_tools"] = kwargs.get("allowed_tools")
            self.hooks = None
            self.resume = None

    class FakeHookMatcher:
        def __init__(self, **kwargs): pass

    class FakeClient:
        def __init__(self, options):
            captured["options"] = options
        async def connect(self): pass

    monkeypatch.setattr("claude_agent_sdk.ClaudeAgentOptions", FakeOptions)
    monkeypatch.setattr("claude_agent_sdk.ClaudeSDKClient", FakeClient)
    monkeypatch.setattr("claude_agent_sdk.HookMatcher", FakeHookMatcher)

    await gw._make_client("main/default")

    assert captured["allowed_tools"] == ["Read", "Glob"]


@pytest.mark.asyncio
async def test_session_deactivate(gateway):
    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("session.create", {"name": "s1"}))
        await _recv_response(ws)

        await ws.send(_make_req("session.deactivate", {"session": "main/s1"}))
        frame = await _recv_response(ws)
        assert frame["ok"] is True


@pytest.mark.asyncio
async def test_session_deactivate_missing_param(gateway):
    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("session.deactivate", {}))
        frame = await _recv_response(ws)
        assert frame["ok"] is False
        assert "session" in frame["error"].lower()


@pytest.mark.asyncio
async def test_session_deactivate_rejects_active_turn(gateway):
    """Deactivating a session mid-turn should fail."""
    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("session.create", {"name": "busy"}))
        await _recv_response(ws)

        # Simulate active turn by injecting into runner's _active_clients
        gateway._runner._active_clients["main/busy"] = object()

        await ws.send(_make_req("session.deactivate", {"session": "main/busy"}))
        frame = await _recv_response(ws)
        assert frame["ok"] is False
        assert "active" in frame["error"].lower()

        # Clean up
        gateway._runner._active_clients.pop("main/busy", None)


@pytest.mark.asyncio
async def test_session_delete(gateway):
    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("session.create", {"name": "doomed"}))
        await _recv_response(ws)

        await ws.send(_make_req("session.delete", {"session": "main/doomed"}))
        frame = await _recv_response(ws)
        assert frame["ok"] is True

        # Verify it's gone from list
        await ws.send(_make_req("session.list"))
        frame = await _recv_response(ws)
        ids = [s["id"] for s in frame["payload"]["sessions"]]
        assert "main/doomed" not in ids


@pytest.mark.asyncio
async def test_session_delete_missing_param(gateway):
    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("session.delete", {}))
        frame = await _recv_response(ws)
        assert frame["ok"] is False
        assert "session" in frame["error"].lower()


@pytest.mark.asyncio
async def test_session_delete_rejects_active_turn(gateway):
    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("session.create", {"name": "busy2"}))
        await _recv_response(ws)

        gateway._runner._active_clients["main/busy2"] = object()

        await ws.send(_make_req("session.delete", {"session": "main/busy2"}))
        frame = await _recv_response(ws)
        assert frame["ok"] is False
        assert "active" in frame["error"].lower()

        gateway._runner._active_clients.pop("main/busy2", None)


@pytest.mark.asyncio
async def test_session_delete_nonexistent_is_noop(gateway):
    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("session.delete", {"session": "main/ghost"}))
        frame = await _recv_response(ws)
        assert frame["ok"] is True


@pytest.mark.asyncio
async def test_session_list_includes_pooled_field(gateway):
    async with websockets.connect(_ws_url(gateway)) as ws:
        await ws.send(_make_req("session.create", {"name": "pooltest"}))
        await _recv_response(ws)

        await ws.send(_make_req("session.list"))
        frame = await _recv_response(ws)
        session = next(s for s in frame["payload"]["sessions"] if s["name"] == "pooltest")
        # No pool configured in test gateway fixture, so pooled should be False
        assert "pooled" in session
        assert session["pooled"] is False
