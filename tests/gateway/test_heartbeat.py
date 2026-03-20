# tests/gateway/test_heartbeat.py

import asyncio
from datetime import datetime, time
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from fera.gateway.heartbeat import (
    HeartbeatScheduler,
    archive_inbox_files,
    build_instruction,
    has_heartbeat_content,
    is_active_hours,
    is_heartbeat_ok,
    list_inbox_files,
)
from fera.gateway.protocol import make_event


class TestIsActiveHours:
    def test_inside_active_hours(self):
        # Use a time we know is inside the window
        assert is_active_hours("00:00-23:59", now=time(12, 0)) is True

    def test_outside_active_hours(self):
        assert is_active_hours("09:00-17:00", now=time(22, 0)) is False

    def test_at_start_boundary(self):
        assert is_active_hours("09:00-17:00", now=time(9, 0)) is True

    def test_at_end_boundary(self):
        # End time is exclusive
        assert is_active_hours("09:00-17:00", now=time(17, 0)) is False

    def test_wraps_midnight(self):
        assert is_active_hours("22:00-06:00", now=time(23, 0)) is True
        assert is_active_hours("22:00-06:00", now=time(3, 0)) is True
        assert is_active_hours("22:00-06:00", now=time(12, 0)) is False

    def test_timezone_inside_active_hours(self):
        # datetime.now(tz) returns 14:00 local time → inside 08:00-22:00
        tz = ZoneInfo("Asia/Kuala_Lumpur")
        with patch("fera.gateway.heartbeat.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 1, 14, 0, 0, tzinfo=tz)
            assert is_active_hours("08:00-22:00", tz="Asia/Kuala_Lumpur") is True
        mock_dt.now.assert_called_once_with(tz)

    def test_timezone_outside_active_hours(self):
        # datetime.now(tz) returns 07:00 local time → outside 08:00-22:00
        tz = ZoneInfo("Asia/Kuala_Lumpur")
        with patch("fera.gateway.heartbeat.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 1, 7, 0, 0, tzinfo=tz)
            assert is_active_hours("08:00-22:00", tz="Asia/Kuala_Lumpur") is False

    def test_no_timezone_uses_now_parameter_directly(self):
        # Without tz, the 'now' parameter is used directly (backward compat)
        assert is_active_hours("09:00-17:00", tz=None, now=time(12, 0)) is True


class TestHasHeartbeatContent:
    def test_stock_template_has_no_content(self, tmp_path):
        hb = tmp_path / "HEARTBEAT.md"
        hb.write_text(
            "# HEARTBEAT.md\n\n"
            "# Keep this file empty (or with only comments) to skip heartbeat API calls\n\n"
            "# Add tasks below when you want the agent to check something periodically\n"
        )
        assert has_heartbeat_content(tmp_path) is False

    def test_has_content_when_task_added(self, tmp_path):
        hb = tmp_path / "HEARTBEAT.md"
        hb.write_text(
            "# HEARTBEAT.md\n\n"
            "# Add tasks below\n\n"
            "- Check if backup completed\n"
        )
        assert has_heartbeat_content(tmp_path) is True

    def test_missing_file_has_no_content(self, tmp_path):
        assert has_heartbeat_content(tmp_path) is False

    def test_empty_file_has_no_content(self, tmp_path):
        (tmp_path / "HEARTBEAT.md").write_text("")
        assert has_heartbeat_content(tmp_path) is False

    def test_only_blank_lines_and_comments(self, tmp_path):
        (tmp_path / "HEARTBEAT.md").write_text("# heading\n\n   \n# comment\n")
        assert has_heartbeat_content(tmp_path) is False


class TestIsHeartbeatOk:
    def test_silent_heartbeat_has_no_text_events(self):
        """After translate_message strips silent markers, no text events remain."""
        events = [{"event": "agent.done", "data": {}}]
        assert is_heartbeat_ok(events) is True

    def test_real_alert_is_not_ok(self):
        events = [
            {"event": "agent.text", "data": {"text": "Your backup task failed!"}},
            {"event": "agent.done", "data": {}},
        ]
        assert is_heartbeat_ok(events) is False

    def test_reasoning_then_done_is_not_ok(self):
        """Real text that survived translate_message means there is content to deliver.

        translate_message already dropped the trailing HEARTBEAT_OK event;
        the reasoning text survives.
        """
        events = [
            {"event": "agent.text", "data": {"text": "Checking calendar... nothing urgent."}},
            {"event": "agent.done", "data": {}},
        ]
        assert is_heartbeat_ok(events) is False

    def test_real_text_after_done_is_not_ok(self):
        """Real text surviving translate_message is not suppressed."""
        events = [
            {"event": "agent.text", "data": {"text": "But also check this thing"}},
            {"event": "agent.done", "data": {}},
        ]
        assert is_heartbeat_ok(events) is False

    def test_multiple_reasoning_chunks_is_not_ok(self):
        """Real text mixed with tool calls survives translate_message — not suppressed."""
        events = [
            {"event": "agent.tool_use", "data": {"id": "1", "name": "Read"}},
            {"event": "agent.tool_result", "data": {"id": "1"}},
            {"event": "agent.text", "data": {"text": "UTC 09:50 = Manila 17:50. Not in morning window."}},
            {"event": "agent.tool_use", "data": {"id": "2", "name": "Bash"}},
            {"event": "agent.tool_result", "data": {"id": "2"}},
            {"event": "agent.done", "data": {}},
        ]
        assert is_heartbeat_ok(events) is False

    def test_multiple_text_chunks_no_heartbeat_ok(self):
        events = [
            {"event": "agent.text", "data": {"text": "Checking things..."}},
            {"event": "agent.text", "data": {"text": "Your backup failed!"}},
            {"event": "agent.done", "data": {}},
        ]
        assert is_heartbeat_ok(events) is False

    def test_briefing_followed_by_done_is_not_ok(self):
        """Real briefing survives translate_message — not suppressed."""
        events = [
            {"event": "agent.tool_use", "data": {"id": "1", "name": "Read"}},
            {"event": "agent.tool_result", "data": {"id": "1"}},
            {"event": "agent.text", "data": {"text": "Good morning! Here's your briefing:\n- Task A needs attention\n- Task B is pending"}},
            {"event": "agent.done", "data": {}},
        ]
        assert is_heartbeat_ok(events) is False


class TestListInboxFiles:
    def test_returns_empty_when_no_inbox_dir(self, tmp_path):
        assert list_inbox_files(tmp_path) == []

    def test_returns_empty_when_inbox_empty(self, tmp_path):
        (tmp_path / "inbox" / "heartbeat").mkdir(parents=True)
        assert list_inbox_files(tmp_path) == []

    def test_returns_files_sorted_by_name(self, tmp_path):
        inbox = tmp_path / "inbox" / "heartbeat"
        inbox.mkdir(parents=True)
        (inbox / "beta.md").write_text("b")
        (inbox / "alpha.md").write_text("a")
        result = list_inbox_files(tmp_path)
        assert [f.name for f in result] == ["alpha.md", "beta.md"]

    def test_ignores_dotfiles(self, tmp_path):
        inbox = tmp_path / "inbox" / "heartbeat"
        inbox.mkdir(parents=True)
        (inbox / ".processed").mkdir()
        (inbox / ".hidden").write_text("x")
        (inbox / "visible.md").write_text("y")
        result = list_inbox_files(tmp_path)
        assert [f.name for f in result] == ["visible.md"]

    def test_ignores_subdirectories(self, tmp_path):
        inbox = tmp_path / "inbox" / "heartbeat"
        inbox.mkdir(parents=True)
        (inbox / "subdir").mkdir()
        (inbox / "file.md").write_text("x")
        result = list_inbox_files(tmp_path)
        assert [f.name for f in result] == ["file.md"]


class TestArchiveInboxFiles:
    def test_moves_files_to_processed_subdir(self, tmp_path):
        inbox = tmp_path / "inbox" / "heartbeat"
        inbox.mkdir(parents=True)
        f = inbox / "briefing.md"
        f.write_text("hello")
        archive_inbox_files(tmp_path, [f])
        assert not f.exists()
        processed = inbox / ".processed"
        assert processed.is_dir()
        archived = list(processed.iterdir())
        assert len(archived) == 1
        assert archived[0].name.endswith("_briefing.md")
        assert archived[0].read_text() == "hello"

    def test_creates_processed_dir_if_missing(self, tmp_path):
        inbox = tmp_path / "inbox" / "heartbeat"
        inbox.mkdir(parents=True)
        f = inbox / "note.txt"
        f.write_text("data")
        archive_inbox_files(tmp_path, [f])
        assert (inbox / ".processed").is_dir()

    def test_noop_when_file_list_empty(self, tmp_path):
        inbox = tmp_path / "inbox" / "heartbeat"
        inbox.mkdir(parents=True)
        archive_inbox_files(tmp_path, [])
        assert not (inbox / ".processed").exists()


class TestBuildInstruction:
    def test_heartbeat_only(self):
        result = build_instruction(has_heartbeat=True, inbox_files=[])
        assert "HEARTBEAT.md" in result
        assert "HEARTBEAT_OK" in result
        assert "inbox" not in result

    def test_inbox_only(self, tmp_path):
        files = [tmp_path / "morning-briefing.md"]
        result = build_instruction(has_heartbeat=False, inbox_files=files)
        assert "inbox" in result
        assert "morning-briefing.md" in result
        assert "HEARTBEAT_OK" not in result
        assert "HEARTBEAT.md" not in result

    def test_both_heartbeat_and_inbox(self, tmp_path):
        files = [tmp_path / "briefing.md"]
        result = build_instruction(has_heartbeat=True, inbox_files=files)
        assert "HEARTBEAT.md" in result
        assert "HEARTBEAT_OK" in result
        assert "inbox" in result
        assert "briefing.md" in result

    def test_multiple_inbox_files(self, tmp_path):
        files = [tmp_path / "a.md", tmp_path / "b.txt"]
        result = build_instruction(has_heartbeat=False, inbox_files=files)
        assert "a.md" in result
        assert "b.txt" in result
        assert "2" in result  # file count

    def test_includes_local_time_when_timezone_provided(self):
        result = build_instruction(has_heartbeat=True, inbox_files=[], tz="Europe/Berlin")
        assert "In your user's time zone it is now" in result

    def test_local_time_uses_24h_format(self):
        result = build_instruction(has_heartbeat=True, inbox_files=[], tz="Europe/Berlin")
        # Should not contain AM/PM
        assert "AM" not in result
        assert "PM" not in result

    def test_local_time_includes_day_of_week_and_date(self):
        result = build_instruction(has_heartbeat=True, inbox_files=[], tz="Europe/Berlin")
        # Should contain a day name (e.g. "Saturday") and a month name
        import re
        days = r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
        assert re.search(days, result), f"Expected day of week in: {result}"

    def test_no_time_without_timezone(self):
        result = build_instruction(has_heartbeat=True, inbox_files=[])
        assert "time zone" not in result


class TestHeartbeatScheduler:
    def _make_scheduler(self, tmp_path, **overrides):
        config = {
            "enabled": True,
            "interval_minutes": 30,
            "active_hours": "00:00-23:59",
            "session": "default",
        }
        config.update(overrides)
        runner = MagicMock()
        bus = MagicMock()
        bus.publish = AsyncMock()
        lanes = MagicMock()
        lanes.is_locked = MagicMock(return_value=False)
        return HeartbeatScheduler(
            config=config,
            runner=runner,
            bus=bus,
            lanes=lanes,
            workspace=tmp_path,
        )

    @pytest.mark.asyncio
    async def test_tick_skips_when_outside_active_hours(self, tmp_path):
        sched = self._make_scheduler(tmp_path, active_hours="03:00-04:00")
        # Write actionable content so only active_hours gate blocks
        (tmp_path / "HEARTBEAT.md").write_text("- check something\n")
        with patch("fera.gateway.heartbeat.is_active_hours", return_value=False):
            result = await sched.tick()
        assert result == "skipped:inactive_hours"
        sched._runner.run_turn.assert_not_called()

    @pytest.mark.asyncio
    async def test_tick_passes_timezone_to_active_hours_check(self, tmp_path):
        sched = self._make_scheduler(
            tmp_path, active_hours="08:00-22:00", timezone="Asia/Kuala_Lumpur"
        )
        (tmp_path / "HEARTBEAT.md").write_text("- check something\n")
        with patch("fera.gateway.heartbeat.is_active_hours", return_value=False) as mock_check:
            await sched.tick()
        mock_check.assert_called_once_with("08:00-22:00", tz="Asia/Kuala_Lumpur")

    @pytest.mark.asyncio
    async def test_tick_skips_when_no_heartbeat_content(self, tmp_path):
        sched = self._make_scheduler(tmp_path)
        (tmp_path / "HEARTBEAT.md").write_text("# just a comment\n")
        result = await sched.tick()
        assert result == "skipped:no_content"

    @pytest.mark.asyncio
    async def test_tick_skips_when_session_busy(self, tmp_path):
        sched = self._make_scheduler(tmp_path)
        (tmp_path / "HEARTBEAT.md").write_text("- check something\n")
        sched._lanes.is_locked.return_value = True
        result = await sched.tick()
        assert result == "skipped:session_busy"

    @pytest.mark.asyncio
    async def test_tick_runs_turn_and_discards_silent_heartbeat(self, tmp_path):
        """After translate_message strips HEARTBEAT_OK, no text events remain."""
        sched = self._make_scheduler(tmp_path)
        (tmp_path / "HEARTBEAT.md").write_text("- check something\n")

        async def fake_turn(session, text, source="", **kwargs):
            yield make_event("agent.done", session=session, data={})

        sched._runner.run_turn = fake_turn
        result = await sched.tick()
        assert result == "ok"
        sched._bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_tick_publishes_real_alert(self, tmp_path):
        sched = self._make_scheduler(tmp_path)
        (tmp_path / "HEARTBEAT.md").write_text("- check something\n")

        async def fake_turn(session, text, source="", **kwargs):
            yield make_event("agent.text", session=session, data={"text": "Your backup failed!"})
            yield make_event("agent.done", session=session, data={})

        sched._runner.run_turn = fake_turn
        result = await sched.tick()
        assert result == "alert"
        assert sched._bus.publish.call_count == 2  # text + done

    @pytest.mark.asyncio
    async def test_tick_publishes_reasoning_text_after_translate(self, tmp_path):
        """Real text survives translate_message; HEARTBEAT_OK was already dropped."""
        sched = self._make_scheduler(tmp_path)
        (tmp_path / "HEARTBEAT.md").write_text("- check something\n")

        async def fake_turn(session, text, source="", **kwargs):
            yield make_event("agent.text", session=session, data={"text": "Checking... nothing urgent."})
            yield make_event("agent.done", session=session, data={})

        sched._runner.run_turn = fake_turn
        result = await sched.tick()
        assert result == "alert"
        assert sched._bus.publish.call_count == 2  # text + done

    @pytest.mark.asyncio
    async def test_tick_prepends_heartbeat_instruction(self, tmp_path):
        sched = self._make_scheduler(tmp_path)
        (tmp_path / "HEARTBEAT.md").write_text("- check something\n")

        captured_text = None

        async def capture_turn(session, text, source="", **kwargs):
            nonlocal captured_text
            captured_text = text
            yield make_event("agent.done", session=session, data={})

        sched._runner.run_turn = capture_turn
        await sched.tick()
        assert "heartbeat" in captured_text.lower()
        assert "HEARTBEAT_OK" in captured_text


@pytest.mark.asyncio
async def test_full_tick_cycle_ok_then_alert(tmp_path):
    """Two consecutive ticks: first HEARTBEAT_OK (discarded), then real alert (published)."""
    from fera.gateway.heartbeat import HeartbeatScheduler
    from fera.gateway.lanes import LaneManager
    from fera.adapters.bus import EventBus

    published = []
    bus = EventBus()
    bus_publish_original = bus.publish

    async def tracking_publish(event):
        published.append(event)
        await bus_publish_original(event)

    bus.publish = tracking_publish
    lanes = LaneManager()
    runner = MagicMock()

    (tmp_path / "HEARTBEAT.md").write_text("- check backup status\n")

    sched = HeartbeatScheduler(
        config={"enabled": True, "interval_minutes": 30,
                "active_hours": "00:00-23:59", "session": "test"},
        runner=runner, bus=bus, lanes=lanes, workspace=tmp_path,
    )

    # Tick 1: silent heartbeat (translate_message already stripped HEARTBEAT_OK)
    async def ok_turn(session, text, source="", **kwargs):
        yield make_event("agent.done", session=session, data={})

    runner.run_turn = ok_turn
    assert await sched.tick() == "ok"
    assert len(published) == 0

    # Tick 2: real alert
    async def alert_turn(session, text, source="", **kwargs):
        yield make_event("agent.text", session=session, data={"text": "Backup failed!"})
        yield make_event("agent.done", session=session, data={})

    runner.run_turn = alert_turn
    assert await sched.tick() == "alert"
    assert len(published) == 2
    assert published[0]["event"] == "agent.text"
    assert published[1]["event"] == "agent.done"


class TestHeartbeatTaskResilience:
    def _make_scheduler(self, tmp_path):
        config = {
            "enabled": True,
            "interval_minutes": 30,
            "active_hours": "00:00-23:59",
            "session": "default",
        }
        runner = MagicMock()
        bus = MagicMock()
        bus.publish = AsyncMock()
        lanes = MagicMock()
        lanes.is_locked = MagicMock(return_value=False)
        return HeartbeatScheduler(
            config=config,
            runner=runner,
            bus=bus,
            lanes=lanes,
            workspace=tmp_path,
        )

    @pytest.mark.asyncio
    async def test_task_restarts_after_unexpected_exception(self, tmp_path):
        sched = self._make_scheduler(tmp_path)

        call_count = 0
        original_loop = sched._loop

        async def failing_loop():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("unexpected crash")
            # Second call: just sleep so the task stays alive
            await asyncio.sleep(10)

        sched._loop = failing_loop
        sched._config["enabled"] = True
        sched.start()

        # Give the callback time to fire and restart
        await asyncio.sleep(0.1)

        assert call_count >= 2, "Loop should have been restarted after crash"
        assert sched._task is not None
        assert not sched._task.done()
        await sched.stop()

    @pytest.mark.asyncio
    async def test_task_not_restarted_after_cancel(self, tmp_path):
        sched = self._make_scheduler(tmp_path)

        async def sleeping_loop():
            await asyncio.sleep(100)

        sched._loop = sleeping_loop
        sched._config["enabled"] = True
        sched.start()
        assert sched._task is not None

        await sched.stop()
        # Give any callback time to fire
        await asyncio.sleep(0.05)
        assert sched._task is None


class TestHeartbeatInbox:
    def _make_scheduler(self, tmp_path, sessions=None, **overrides):
        config = {
            "enabled": True,
            "interval_minutes": 30,
            "active_hours": "00:00-23:59",
            "session": "main/default",
        }
        config.update(overrides)
        runner = MagicMock()
        bus = MagicMock()
        bus.publish = AsyncMock()
        lanes = MagicMock()
        lanes.is_locked = MagicMock(return_value=False)
        return HeartbeatScheduler(
            config=config,
            runner=runner,
            bus=bus,
            lanes=lanes,
            workspace=tmp_path,
            sessions=sessions,
        )

    @pytest.mark.asyncio
    async def test_tick_proceeds_with_inbox_content_even_without_heartbeat_md(self, tmp_path):
        """Inbox files alone are enough to trigger a tick."""
        sessions = MagicMock()
        sessions.get = MagicMock(return_value={"last_inbound_adapter": "telegram"})
        sched = self._make_scheduler(tmp_path, sessions=sessions)
        # No HEARTBEAT.md, but inbox has a file
        inbox = tmp_path / "inbox" / "heartbeat"
        inbox.mkdir(parents=True)
        (inbox / "briefing.md").write_text("Weather: sunny")

        captured = {}

        async def capture_turn(session, text, source="", **kwargs):
            captured["text"] = text
            yield make_event("agent.text", session=session, data={"text": "Here's your briefing..."})
            yield make_event("agent.done", session=session, data={})

        sched._runner.run_turn = capture_turn
        result = await sched.tick()
        assert result == "alert"
        assert "inbox" in captured["text"]
        assert "briefing.md" in captured["text"]

    @pytest.mark.asyncio
    async def test_tick_archives_inbox_files_after_success(self, tmp_path):
        sessions = MagicMock()
        sessions.get = MagicMock(return_value={})
        sched = self._make_scheduler(tmp_path, sessions=sessions)
        inbox = tmp_path / "inbox" / "heartbeat"
        inbox.mkdir(parents=True)
        (inbox / "note.md").write_text("data")

        async def ok_turn(session, text, source="", **kwargs):
            yield make_event("agent.text", session=session, data={"text": "Done"})
            yield make_event("agent.done", session=session, data={})

        sched._runner.run_turn = ok_turn
        await sched.tick()
        assert not (inbox / "note.md").exists()
        assert (inbox / ".processed").is_dir()

    @pytest.mark.asyncio
    async def test_tick_does_not_archive_on_error(self, tmp_path):
        sessions = MagicMock()
        sched = self._make_scheduler(tmp_path, sessions=sessions)
        inbox = tmp_path / "inbox" / "heartbeat"
        inbox.mkdir(parents=True)
        (inbox / "note.md").write_text("data")

        async def failing_turn(session, text, source="", **kwargs):
            raise RuntimeError("boom")
            yield  # make it a generator  # noqa: unreachable

        sched._runner.run_turn = failing_turn
        result = await sched.tick()
        assert result == "error"
        assert (inbox / "note.md").exists()

    @pytest.mark.asyncio
    async def test_tick_stamps_target_adapter_on_published_events(self, tmp_path):
        sessions = MagicMock()
        sessions.get = MagicMock(return_value={"last_inbound_adapter": "telegram"})
        sched = self._make_scheduler(tmp_path, sessions=sessions)
        (tmp_path / "HEARTBEAT.md").write_text("- check something\n")

        async def alert_turn(session, text, source="", **kwargs):
            yield make_event("agent.text", session=session, data={"text": "Alert!"})
            yield make_event("agent.done", session=session, data={})

        sched._runner.run_turn = alert_turn
        await sched.tick()
        published_events = [c.args[0] for c in sched._bus.publish.call_args_list]
        assert all(e.get("target_adapter") == "telegram" for e in published_events)

    @pytest.mark.asyncio
    async def test_tick_suppresses_tool_use_events_on_alert(self, tmp_path):
        """Tool use/result events are not published even when the turn produces a real alert."""
        sessions = MagicMock()
        sessions.get = MagicMock(return_value={})
        sched = self._make_scheduler(tmp_path, sessions=sessions)
        (tmp_path / "HEARTBEAT.md").write_text("- check something\n")

        async def alert_turn_with_tools(session, text, source=""):
            yield make_event("agent.tool_use", session=session, data={"id": "1", "name": "Bash", "input": {"command": "date"}})
            yield make_event("agent.tool_result", session=session, data={"tool_use_id": "1", "content": "Mon Mar 17"})
            yield make_event("agent.text", session=session, data={"text": "Something requires attention!"})
            yield make_event("agent.done", session=session, data={})

        sched._runner.run_turn = alert_turn_with_tools
        result = await sched.tick()
        assert result == "alert"
        published_events = [c.args[0] for c in sched._bus.publish.call_args_list]
        event_types = [e["event"] for e in published_events]
        assert "agent.tool_use" not in event_types
        assert "agent.tool_result" not in event_types
        assert "agent.text" in event_types
        assert "agent.done" in event_types

    @pytest.mark.asyncio
    async def test_tick_no_target_adapter_when_none_recorded(self, tmp_path):
        sessions = MagicMock()
        sessions.get = MagicMock(return_value={})
        sched = self._make_scheduler(tmp_path, sessions=sessions)
        (tmp_path / "HEARTBEAT.md").write_text("- check something\n")

        async def alert_turn(session, text, source="", **kwargs):
            yield make_event("agent.text", session=session, data={"text": "Alert!"})
            yield make_event("agent.done", session=session, data={})

        sched._runner.run_turn = alert_turn
        await sched.tick()
        published_events = [c.args[0] for c in sched._bus.publish.call_args_list]
        assert all("target_adapter" not in e for e in published_events)


@pytest.mark.asyncio
async def test_inbox_to_targeted_delivery_integration(tmp_path):
    """Full flow: inbox file -> heartbeat tick -> targeted events -> archived."""
    from fera.gateway.heartbeat import HeartbeatScheduler
    from fera.gateway.lanes import LaneManager
    from fera.adapters.bus import EventBus
    from fera.gateway.sessions import SessionManager

    # Set up sessions with last_inbound_adapter
    sessions = SessionManager(tmp_path / "sessions.json", fera_home=tmp_path)
    sessions.create("default")
    sessions.set_last_inbound_adapter("main/default", "telegram")

    # Set up inbox
    workspace = tmp_path / "agents" / "main" / "workspace"
    workspace.mkdir(parents=True)
    inbox = workspace / "inbox" / "heartbeat"
    inbox.mkdir(parents=True)
    (inbox / "morning-briefing.md").write_text("Weather: sunny, 22C")

    published = []
    bus = EventBus()
    original_publish = bus.publish

    async def tracking_publish(event):
        published.append(event)
        await original_publish(event)

    bus.publish = tracking_publish

    lanes = LaneManager()
    runner = MagicMock()

    async def fake_turn(session, text, source="", **kwargs):
        yield make_event(
            "agent.text",
            session=session,
            data={"text": "Good morning! Weather is sunny, 22C."},
        )
        yield make_event("agent.done", session=session, data={})

    runner.run_turn = fake_turn

    sched = HeartbeatScheduler(
        config={
            "enabled": True,
            "interval_minutes": 30,
            "active_hours": "00:00-23:59",
            "session": "main/default",
        },
        runner=runner,
        bus=bus,
        lanes=lanes,
        workspace=workspace,
        sessions=sessions,
    )

    result = await sched.tick()
    assert result == "alert"

    # Events were published with target_adapter
    text_events = [e for e in published if e.get("event") == "agent.text"]
    assert len(text_events) == 1
    assert text_events[0]["target_adapter"] == "telegram"

    done_events = [e for e in published if e.get("event") == "agent.done"]
    assert len(done_events) == 1
    assert done_events[0]["target_adapter"] == "telegram"

    # Inbox file was archived
    assert not (inbox / "morning-briefing.md").exists()
    assert (inbox / ".processed").is_dir()
    archived = list((inbox / ".processed").iterdir())
    assert len(archived) == 1
    assert archived[0].read_text() == "Weather: sunny, 22C"


