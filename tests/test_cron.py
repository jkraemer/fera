import asyncio
import json
import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fera.gateway.protocol import make_event
from fera.gateway.sessions import SessionManager


@pytest.fixture
def mock_runner():
    return MagicMock()


@pytest.fixture
def mock_bus():
    bus = MagicMock()
    bus.publish = AsyncMock()
    return bus


@pytest.fixture
def sessions(tmp_path):
    return SessionManager(tmp_path / "sessions.json")


class TestExecuteSessionJob:
    @pytest.mark.asyncio
    async def test_runs_turn_in_named_session(self, mock_runner, mock_bus, sessions):
        from fera.cron import execute_job

        captured = {}

        async def fake_turn(session, text, source="", prompt_mode="full", model=None, allowed_tools=None):
            captured["session"] = session
            captured["text"] = text
            captured["source"] = source
            captured["prompt_mode"] = prompt_mode
            yield make_event("agent.text", session=session, data={"text": "Done!"})
            yield make_event("agent.done", session=session, data={})

        mock_runner.run_turn = fake_turn

        result = await execute_job(
            job_name="reminder",
            job={"agent": "main", "session": "default", "payload": "Take vitamins."},
            runner=mock_runner,
            bus=mock_bus,
            sessions=sessions,
        )

        assert captured["session"] == "main/default"
        assert captured["text"] == "Take vitamins."
        assert captured["source"] == "cron"
        assert captured["prompt_mode"] == "full"
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_publishes_events_to_bus(self, mock_runner, mock_bus, sessions):
        from fera.cron import execute_job

        async def fake_turn(session, text, source="", prompt_mode="full", model=None, allowed_tools=None):
            yield make_event("agent.text", session=session, data={"text": "Hi"})
            yield make_event("agent.done", session=session, data={})

        mock_runner.run_turn = fake_turn

        await execute_job(
            job_name="test",
            job={"agent": "main", "session": "default", "payload": "Hello."},
            runner=mock_runner,
            bus=mock_bus,
            sessions=sessions,
        )

        assert mock_bus.publish.call_count == 2  # text + done

    @pytest.mark.asyncio
    async def test_respects_prompt_mode_override(self, mock_runner, mock_bus, sessions):
        from fera.cron import execute_job

        captured = {}

        async def fake_turn(session, text, source="", prompt_mode="full", model=None, allowed_tools=None):
            captured["prompt_mode"] = prompt_mode
            yield make_event("agent.done", session=session, data={})

        mock_runner.run_turn = fake_turn

        await execute_job(
            job_name="test",
            job={"agent": "main", "session": "default", "payload": "Hi.", "prompt_mode": "minimal"},
            runner=mock_runner,
            bus=mock_bus,
            sessions=sessions,
        )

        assert captured["prompt_mode"] == "minimal"

    @pytest.mark.asyncio
    async def test_passes_model_to_runner(self, mock_runner, mock_bus, sessions):
        from fera.cron import execute_job

        captured = {}

        async def fake_turn(session, text, source="", prompt_mode="full", model=None, allowed_tools=None):
            captured["model"] = model
            yield make_event("agent.done", session=session, data={})

        mock_runner.run_turn = fake_turn

        await execute_job(
            job_name="test",
            job={"agent": "main", "session": "default", "payload": "Hi.", "model": "haiku"},
            runner=mock_runner,
            bus=mock_bus,
            sessions=sessions,
        )

        assert captured["model"] == "haiku"


class TestExecuteEphemeralJob:
    @pytest.mark.asyncio
    async def test_runs_oneshot_with_payload(self, mock_runner, mock_bus, sessions):
        from fera.cron import execute_job

        oneshot_calls = []

        async def fake_oneshot(text, *, agent_name="main", prompt_mode="minimal", model=None, allowed_tools=None):
            oneshot_calls.append({"text": text, "prompt_mode": prompt_mode, "model": model})
            yield make_event("agent.text", session="", data={"text": "Found 3 tickets."})
            yield make_event("agent.done", session="", data={})

        mock_runner.run_oneshot = fake_oneshot

        result = await execute_job(
            job_name="digest",
            job={"agent": "main", "payload": "Check tickets."},
            runner=mock_runner,
            bus=mock_bus,
            sessions=sessions,
        )

        assert result["status"] == "completed"
        assert result["job"] == "digest"
        assert len(oneshot_calls) == 1
        assert oneshot_calls[0]["text"] == "Check tickets."
        assert oneshot_calls[0]["prompt_mode"] == "minimal"

    @pytest.mark.asyncio
    async def test_oneshot_events_not_published_to_bus(self, mock_runner, mock_bus, sessions):
        from fera.cron import execute_job

        async def fake_oneshot(text, *, agent_name="main", prompt_mode="minimal", model=None, allowed_tools=None):
            yield make_event("agent.text", session="", data={"text": "Result."})
            yield make_event("agent.done", session="", data={})

        mock_runner.run_oneshot = fake_oneshot

        await execute_job(
            job_name="job1",
            job={"agent": "main", "payload": "Do it."},
            runner=mock_runner,
            bus=mock_bus,
            sessions=sessions,
        )

        mock_bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_session_created(self, mock_runner, mock_bus, sessions):
        from fera.cron import execute_job

        async def fake_oneshot(text, *, agent_name="main", prompt_mode="minimal", model=None, allowed_tools=None):
            yield make_event("agent.done", session="", data={})

        mock_runner.run_oneshot = fake_oneshot

        await execute_job(
            job_name="digest",
            job={"payload": "Check it."},
            runner=mock_runner,
            bus=mock_bus,
            sessions=sessions,
        )

        assert sessions.list() == []

    @pytest.mark.asyncio
    async def test_passes_model_to_oneshot(self, mock_runner, mock_bus, sessions):
        from fera.cron import execute_job

        oneshot_calls = []

        async def fake_oneshot(text, *, agent_name="main", prompt_mode="minimal", model=None, allowed_tools=None):
            oneshot_calls.append({"model": model})
            yield make_event("agent.done", session="", data={})

        mock_runner.run_oneshot = fake_oneshot

        await execute_job(
            job_name="digest",
            job={"agent": "main", "payload": "Check it.", "model": "haiku"},
            runner=mock_runner,
            bus=mock_bus,
            sessions=sessions,
        )

        assert oneshot_calls[0]["model"] == "haiku"


class TestAgentRouting:
    @pytest.mark.asyncio
    async def test_session_job_qualifies_session_with_agent(self, mock_runner, mock_bus, sessions):
        from fera.cron import execute_job

        captured = {}

        async def fake_turn(session, text, source="", prompt_mode="full", model=None, allowed_tools=None):
            captured["session"] = session
            yield make_event("agent.done", session=session, data={})

        mock_runner.run_turn = fake_turn

        await execute_job(
            job_name="forge-job",
            job={"agent": "forge", "session": "research", "payload": "Investigate."},
            runner=mock_runner,
            bus=mock_bus,
            sessions=sessions,
        )

        assert captured["session"] == "forge/research"

    @pytest.mark.asyncio
    async def test_session_job_defaults_to_main_agent(self, mock_runner, mock_bus, sessions):
        from fera.cron import execute_job

        captured = {}

        async def fake_turn(session, text, source="", prompt_mode="full", model=None, allowed_tools=None):
            captured["session"] = session
            yield make_event("agent.done", session=session, data={})

        mock_runner.run_turn = fake_turn

        await execute_job(
            job_name="default-job",
            job={"session": "daily", "payload": "Hello."},
            runner=mock_runner,
            bus=mock_bus,
            sessions=sessions,
        )

        assert captured["session"] == "main/daily"

    @pytest.mark.asyncio
    async def test_session_job_preserves_already_qualified_name(self, mock_runner, mock_bus, sessions):
        from fera.cron import execute_job

        captured = {}

        async def fake_turn(session, text, source="", prompt_mode="full", model=None, allowed_tools=None):
            captured["session"] = session
            yield make_event("agent.done", session=session, data={})

        mock_runner.run_turn = fake_turn

        await execute_job(
            job_name="qualified-job",
            job={"agent": "forge", "session": "forge/research", "payload": "Go."},
            runner=mock_runner,
            bus=mock_bus,
            sessions=sessions,
        )

        assert captured["session"] == "forge/research"

    @pytest.mark.asyncio
    async def test_ephemeral_job_passes_agent_to_oneshot(self, mock_runner, mock_bus, sessions):
        from fera.cron import execute_job

        captured = {}

        async def fake_oneshot(text, *, agent_name="main", prompt_mode="minimal", model=None, allowed_tools=None):
            captured["agent_name"] = agent_name
            yield make_event("agent.done", session="", data={})

        mock_runner.run_oneshot = fake_oneshot

        await execute_job(
            job_name="forge-ephemeral",
            job={"agent": "forge", "payload": "Check it."},
            runner=mock_runner,
            bus=mock_bus,
            sessions=sessions,
        )

        assert captured["agent_name"] == "forge"

    @pytest.mark.asyncio
    async def test_ephemeral_job_defaults_agent_to_main(self, mock_runner, mock_bus, sessions):
        from fera.cron import execute_job

        captured = {}

        async def fake_oneshot(text, *, agent_name="main", prompt_mode="minimal", model=None, allowed_tools=None):
            captured["agent_name"] = agent_name
            yield make_event("agent.done", session="", data={})

        mock_runner.run_oneshot = fake_oneshot

        await execute_job(
            job_name="default-ephemeral",
            job={"payload": "Check it."},
            runner=mock_runner,
            bus=mock_bus,
            sessions=sessions,
        )

        assert captured["agent_name"] == "main"

    @pytest.mark.asyncio
    async def test_session_created_under_correct_agent(self, mock_runner, mock_bus, sessions):
        from fera.cron import execute_job

        async def fake_turn(session, text, source="", prompt_mode="full", model=None, allowed_tools=None):
            yield make_event("agent.done", session=session, data={})

        mock_runner.run_turn = fake_turn

        await execute_job(
            job_name="forge-job",
            job={"agent": "forge", "session": "research", "payload": "Go."},
            runner=mock_runner,
            bus=mock_bus,
            sessions=sessions,
        )

        info = sessions.get("forge/research")
        assert info is not None
        assert info["agent"] == "forge"


class TestAllowedToolsPassthrough:
    @pytest.mark.asyncio
    async def test_session_job_passes_allowed_tools_to_runner(self, mock_runner, mock_bus, sessions):
        from fera.cron import execute_job

        captured = {}

        async def fake_turn(session, text, source="", prompt_mode="full", model=None, allowed_tools=None):
            captured["allowed_tools"] = allowed_tools
            yield make_event("agent.done", session=session, data={})

        mock_runner.run_turn = fake_turn

        await execute_job(
            job_name="restricted",
            job={
                "agent": "main", "session": "default",
                "payload": "Do it.",
                "allowed_tools": ["Read", "Glob"],
            },
            runner=mock_runner,
            bus=mock_bus,
            sessions=sessions,
        )

        assert captured["allowed_tools"] == ["Read", "Glob"]

    @pytest.mark.asyncio
    async def test_session_job_omits_allowed_tools_when_not_specified(self, mock_runner, mock_bus, sessions):
        from fera.cron import execute_job

        captured = {}

        async def fake_turn(session, text, source="", prompt_mode="full", model=None, allowed_tools=None):
            captured["allowed_tools"] = allowed_tools
            yield make_event("agent.done", session=session, data={})

        mock_runner.run_turn = fake_turn

        await execute_job(
            job_name="default",
            job={"agent": "main", "session": "default", "payload": "Do it."},
            runner=mock_runner,
            bus=mock_bus,
            sessions=sessions,
        )

        assert captured["allowed_tools"] is None

    @pytest.mark.asyncio
    async def test_ephemeral_job_passes_allowed_tools_to_oneshot(self, mock_runner, mock_bus, sessions):
        from fera.cron import execute_job

        captured = {}

        async def fake_oneshot(text, *, agent_name="main", prompt_mode="minimal", model=None, allowed_tools=None):
            captured["allowed_tools"] = allowed_tools
            yield make_event("agent.done", session="", data={})

        mock_runner.run_oneshot = fake_oneshot

        await execute_job(
            job_name="restricted-oneshot",
            job={
                "agent": "main",
                "payload": "Check it.",
                "allowed_tools": ["WebSearch", "WebFetch"],
            },
            runner=mock_runner,
            bus=mock_bus,
            sessions=sessions,
        )

        assert captured["allowed_tools"] == ["WebSearch", "WebFetch"]

    @pytest.mark.asyncio
    async def test_ephemeral_job_omits_allowed_tools_when_not_specified(self, mock_runner, mock_bus, sessions):
        from fera.cron import execute_job

        captured = {}

        async def fake_oneshot(text, *, agent_name="main", prompt_mode="minimal", model=None, allowed_tools=None):
            captured["allowed_tools"] = allowed_tools
            yield make_event("agent.done", session="", data={})

        mock_runner.run_oneshot = fake_oneshot

        await execute_job(
            job_name="default-oneshot",
            job={"agent": "main", "payload": "Check it."},
            runner=mock_runner,
            bus=mock_bus,
            sessions=sessions,
        )

        assert captured["allowed_tools"] is None


class TestCronLogging:
    @pytest.mark.asyncio
    async def test_session_job_logs_started_and_completed(self, mock_runner, mock_bus, sessions, tmp_path):
        import fera.logger as logger_mod
        from fera.logger import init_logger
        from fera.cron import execute_job

        logger_mod._logger = None
        logger = init_logger(tmp_path / "logs")
        logged = []

        async def cb(entry):
            logged.append(entry)

        logger.set_broadcast(cb)
        try:
            async def fake_turn(session, text, source="", prompt_mode="full", model=None, allowed_tools=None):
                yield make_event("agent.text", session=session, data={"text": "Good morning!"})
                yield make_event("agent.done", session=session, data={})

            mock_runner.run_turn = fake_turn

            await execute_job(
                job_name="morning-brief",
                job={"agent": "main", "session": "default", "payload": "Run briefing."},
                runner=mock_runner, bus=mock_bus, sessions=sessions,
            )

            events = [e["event"] for e in logged]
            assert "cron.started" in events
            assert "cron.completed" in events

            started = next(e for e in logged if e["event"] == "cron.started")
            assert started["data"]["job"] == "morning-brief"

            completed = next(e for e in logged if e["event"] == "cron.completed")
            assert completed["data"]["job"] == "morning-brief"
        finally:
            logger_mod._logger = None

    @pytest.mark.asyncio
    async def test_ephemeral_job_logs_with_output(self, mock_runner, mock_bus, sessions, tmp_path):
        import fera.logger as logger_mod
        from fera.logger import init_logger
        from fera.cron import execute_job

        logger_mod._logger = None
        logger = init_logger(tmp_path / "logs")
        logged = []

        async def cb(entry):
            logged.append(entry)

        logger.set_broadcast(cb)
        try:
            async def fake_oneshot(text, *, agent_name="main", prompt_mode="minimal", model=None, allowed_tools=None):
                yield make_event("agent.text", session="", data={"text": "Found 3 items."})
                yield make_event("agent.done", session="", data={})

            mock_runner.run_oneshot = fake_oneshot

            await execute_job(
                job_name="digest",
                job={"agent": "main", "payload": "Check tickets."},
                runner=mock_runner, bus=mock_bus, sessions=sessions,
            )

            completed = next(e for e in logged if e["event"] == "cron.completed")
            assert completed["data"]["job"] == "digest"
            assert "Found 3 items." in completed["data"]["output"]
        finally:
            logger_mod._logger = None

    @pytest.mark.asyncio
    async def test_ephemeral_job_logs_empty_output(self, mock_runner, mock_bus, sessions, tmp_path):
        import fera.logger as logger_mod
        from fera.logger import init_logger
        from fera.cron import execute_job

        logger_mod._logger = None
        logger = init_logger(tmp_path / "logs")
        logged = []

        async def cb(entry):
            logged.append(entry)

        logger.set_broadcast(cb)
        try:
            async def fake_oneshot(text, *, agent_name="main", prompt_mode="minimal", model=None, allowed_tools=None):
                yield make_event("agent.done", session="", data={})

            mock_runner.run_oneshot = fake_oneshot

            await execute_job(
                job_name="silent-job",
                job={"agent": "main", "payload": "Do it."},
                runner=mock_runner, bus=mock_bus, sessions=sessions,
            )

            completed = next(e for e in logged if e["event"] == "cron.completed")
            assert completed["data"]["job"] == "silent-job"
            assert completed["data"]["output"] == ""
        finally:
            logger_mod._logger = None


class TestCronCli:
    def test_main_missing_arg_exits_1(self, capsys):
        from fera.cron import main

        with patch("sys.argv", ["fera-run-job"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 1
        captured = capsys.readouterr()
        assert "usage" in captured.err.lower()

    def test_main_job_not_found_exits_1(self, tmp_path, monkeypatch, capsys):
        from fera.cron import main

        monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
        with patch("sys.argv", ["fera-run-job", "nonexistent"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 1
        captured = capsys.readouterr()
        assert "not found" in captured.err.lower()

    def test_main_valid_job_calls_gateway(self, tmp_path, monkeypatch):
        from fera.cron import main

        monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
        (tmp_path / "cron.json").write_text(
            '{"jobs": {"daily": {"payload": "Hello.", "session": "default"}}}'
        )
        gateway_mock = AsyncMock()
        with patch("sys.argv", ["fera-run-job", "daily"]):
            with patch("fera.cron._run_job_via_gateway", gateway_mock):
                main()
        gateway_mock.assert_called_once_with("daily")
