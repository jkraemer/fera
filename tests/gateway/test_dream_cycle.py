# tests/gateway/test_dream_cycle.py
from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fera.gateway.dream_cycle import DreamCycleScheduler


def _make_scheduler(
    tmp_path: Path,
    agents: list[str] | None = None,
    sessions_for_agent: dict | None = None,
) -> DreamCycleScheduler:
    config = {
        "enabled": True,
        "time": "03:00",
        "agents": agents or ["main"],
    }
    runner = MagicMock()
    runner.clear_session = AsyncMock()

    sessions = MagicMock()
    # sessions_for_agent maps agent -> list of session dicts
    sessions.sessions_for_agent = lambda agent: (sessions_for_agent or {}).get(agent, [])

    memory_writer = MagicMock()
    memory_writer.archive_session = AsyncMock()
    memory_writer.write_synthesis = AsyncMock()
    memory_writer.update_memory_md = AsyncMock()

    lanes = MagicMock()
    lanes.is_locked = MagicMock(return_value=False)

    transcript_logger = MagicMock()
    transcript_logger.transcript_path = MagicMock(
        side_effect=lambda sid: Path(tmp_path / "transcripts" / f"{sid.replace('/', '_')}.jsonl")
    )

    return DreamCycleScheduler(
        config=config,
        runner=runner,
        sessions=sessions,
        memory_writer=memory_writer,
        lanes=lanes,
        fera_home=tmp_path,
        transcript_logger=transcript_logger,
    )


@pytest.mark.asyncio
async def test_tick_skips_sessions_without_sdk_session_id(tmp_path):
    sched = _make_scheduler(
        tmp_path,
        sessions_for_agent={
            "main": [
                {"id": "main/default", "agent": "main", "workspace_dir": str(tmp_path)},
                # No sdk_session_id
            ]
        },
    )
    await sched.tick()
    sched._memory_writer.archive_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_archives_session_via_memory_writer(tmp_path):
    """Phase 1 calls archive_session for active sessions."""
    session_info = {
        "id": "main/default",
        "agent": "main",
        "workspace_dir": str(tmp_path / "agents" / "main" / "workspace"),
        "sdk_session_id": "sdk-abc",
    }
    sched = _make_scheduler(
        tmp_path,
        sessions_for_agent={"main": [session_info]},
    )
    await sched.tick()

    sched._memory_writer.archive_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_tick_clears_session_when_lane_not_locked(tmp_path):
    """Phase 1 clears the session when the lane is free."""
    session_info = {
        "id": "main/default",
        "agent": "main",
        "workspace_dir": str(tmp_path / "agents" / "main" / "workspace"),
        "sdk_session_id": "sdk-abc",
    }
    sched = _make_scheduler(
        tmp_path,
        sessions_for_agent={"main": [session_info]},
    )
    await sched.tick()

    sched._runner.clear_session.assert_awaited_once_with("main/default")


@pytest.mark.asyncio
async def test_tick_skips_clear_when_lane_locked(tmp_path):
    """Phase 1 archives but does NOT clear when the session is busy."""
    session_info = {
        "id": "main/default",
        "agent": "main",
        "workspace_dir": str(tmp_path / "agents" / "main" / "workspace"),
        "sdk_session_id": "sdk-abc",
    }
    sched = _make_scheduler(
        tmp_path,
        sessions_for_agent={"main": [session_info]},
    )
    sched._lanes.is_locked = MagicMock(return_value=True)

    await sched.tick()

    # Archive still runs
    sched._memory_writer.archive_session.assert_awaited_once()
    # But clear is skipped
    sched._runner.clear_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_does_not_call_runner_run_turn(tmp_path):
    """Phase 1 no longer sends archivist turns through the runner."""
    session_info = {
        "id": "main/default",
        "agent": "main",
        "workspace_dir": str(tmp_path / "agents" / "main" / "workspace"),
        "sdk_session_id": "sdk-abc",
    }
    sched = _make_scheduler(
        tmp_path,
        sessions_for_agent={"main": [session_info]},
    )
    await sched.tick()

    # run_turn should not be called at all - Phase 1 uses archive_session now
    sched._runner.run_turn.assert_not_called()


@pytest.mark.asyncio
async def test_tick_runs_synthesis_and_memory_md_update(tmp_path):
    sched = _make_scheduler(tmp_path, sessions_for_agent={"main": []})
    await sched.tick()

    today = date.today()
    workspace = tmp_path / "agents" / "main" / "workspace"
    sched._memory_writer.write_synthesis.assert_awaited_once_with(workspace, today)
    sched._memory_writer.update_memory_md.assert_awaited_once_with(workspace, today)


@pytest.mark.asyncio
async def test_tick_skips_memory_md_when_synthesis_fails(tmp_path):
    sched = _make_scheduler(tmp_path, sessions_for_agent={"main": []})
    sched._memory_writer.write_synthesis = AsyncMock(side_effect=RuntimeError("fail"))

    await sched.tick()  # must not raise

    sched._memory_writer.update_memory_md.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_processes_agents_independently(tmp_path):
    """An error in one agent's cycle does not prevent others from running."""
    (tmp_path / "agents" / "main" / "workspace").mkdir(parents=True)
    (tmp_path / "agents" / "forge" / "workspace").mkdir(parents=True)

    sched = _make_scheduler(
        tmp_path,
        agents=["main", "forge"],
        sessions_for_agent={"main": [], "forge": []},
    )
    sched._memory_writer.write_synthesis = AsyncMock(
        side_effect=[RuntimeError("main failed"), None]
    )
    await sched.tick()  # must not raise

    assert sched._memory_writer.write_synthesis.await_count == 2


@pytest.mark.asyncio
async def test_full_dream_cycle_with_two_agents(tmp_path):
    """Full tick: two agents, one has an active session, one does not."""
    (tmp_path / "agents" / "main" / "workspace").mkdir(parents=True)
    (tmp_path / "agents" / "forge" / "workspace").mkdir(parents=True)

    clear_calls: list[str] = []

    runner = MagicMock()
    runner.clear_session = AsyncMock(side_effect=lambda s: clear_calls.append(s))

    sessions = MagicMock()
    sessions.sessions_for_agent = lambda agent: {
        "main": [
            {
                "id": "main/default",
                "agent": "main",
                "workspace_dir": str(tmp_path / "agents" / "main" / "workspace"),
                "sdk_session_id": "sdk-main-1",
            }
        ],
        "forge": [],
    }.get(agent, [])

    memory_writer = MagicMock()
    memory_writer.archive_session = AsyncMock()
    memory_writer.write_synthesis = AsyncMock()
    memory_writer.update_memory_md = AsyncMock()

    lanes = MagicMock()
    lanes.is_locked = MagicMock(return_value=False)

    transcript_logger = MagicMock()
    transcript_logger.transcript_path = MagicMock(
        side_effect=lambda sid: Path(tmp_path / "transcripts" / f"{sid.replace('/', '_')}.jsonl")
    )

    sched = DreamCycleScheduler(
        config={"enabled": True, "time": "03:00", "agents": ["main", "forge"]},
        runner=runner,
        sessions=sessions,
        memory_writer=memory_writer,
        lanes=lanes,
        fera_home=tmp_path,
        transcript_logger=transcript_logger,
    )
    await sched.tick()

    # main agent had active session — archived and cleared
    memory_writer.archive_session.assert_awaited_once()
    assert "main/default" in clear_calls

    # both agents get synthesis + memory update
    assert memory_writer.write_synthesis.await_count == 2
    assert memory_writer.update_memory_md.await_count == 2


@pytest.mark.asyncio
async def test_tick_uses_configured_timezone_for_date(tmp_path):
    """When timezone is configured, tick() computes yesterday using that timezone."""
    sched = _make_scheduler(tmp_path, sessions_for_agent={"main": []})
    sched._config["timezone"] = "Asia/Singapore"

    fixed_date = date(2026, 2, 22)
    with patch("fera.gateway.dream_cycle.local_date", return_value=fixed_date) as mock_local_date:
        await sched.tick()
        mock_local_date.assert_called_with("Asia/Singapore")

    workspace = tmp_path / "agents" / "main" / "workspace"
    sched._memory_writer.write_synthesis.assert_awaited_once_with(workspace, fixed_date)
    sched._memory_writer.update_memory_md.assert_awaited_once_with(workspace, fixed_date)


@pytest.mark.asyncio
async def test_task_restarts_after_unexpected_exception(tmp_path):
    sched = _make_scheduler(tmp_path)

    call_count = 0

    async def failing_loop():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("unexpected crash")
        await asyncio.sleep(10)

    sched._loop = failing_loop
    sched._config["enabled"] = True
    sched.start()

    await asyncio.sleep(0.1)

    assert call_count >= 2, "Loop should have been restarted after crash"
    assert sched._task is not None
    assert not sched._task.done()
    await sched.stop()


@pytest.mark.asyncio
async def test_task_not_restarted_after_cancel(tmp_path):
    sched = _make_scheduler(tmp_path)

    async def sleeping_loop():
        await asyncio.sleep(100)

    sched._loop = sleeping_loop
    sched._config["enabled"] = True
    sched.start()
    assert sched._task is not None

    await sched.stop()
    await asyncio.sleep(0.05)
    assert sched._task is None

