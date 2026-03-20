from pathlib import Path

import pytest
from unittest.mock import AsyncMock, MagicMock
from fera.adapters.commands import SlashCommandHandler, CommandResult
from fera.adapters.base import AdapterContext
from fera.adapters.bus import EventBus


def _make_context(sessions=None, stats=None, fera_home=None):
    bus = EventBus()
    runner = MagicMock()
    sm = MagicMock()
    sm.sessions_for_agent.return_value = sessions or [
        {"id": "main/default", "name": "default", "agent": "main"},
        {"id": "main/work", "name": "work", "agent": "main"},
    ]
    ctx = AdapterContext(
        bus=bus, runner=runner, sessions=sm, agent_name="main",
        fera_home=fera_home,
    )
    if stats is not None:
        ctx._stats = stats
    return ctx


def _make_handler(sessions=None, stats=None):
    return SlashCommandHandler(_make_context(sessions=sessions, stats=stats))


def test_match_session():
    h = _make_handler()
    assert h.match("/session") is True


def test_match_session_with_arg():
    """Still matches /session name — returns error response instead of switching."""
    h = _make_handler()
    assert h.match("/session work") is True


def test_match_sessions():
    h = _make_handler()
    assert h.match("/sessions") is True


def test_match_status():
    h = _make_handler()
    assert h.match("/status") is True


def test_match_non_command():
    h = _make_handler()
    assert h.match("hello world") is False


def test_match_partial_prefix():
    h = _make_handler()
    assert h.match("/sessionx") is False


def test_command_result_has_no_new_session_field():
    """CommandResult no longer has a new_session field."""
    r = CommandResult(response="ok")
    assert not hasattr(r, "new_session")


@pytest.mark.asyncio
async def test_handle_session_bare_returns_stats():
    """/session bare returns current session stats (like /status)."""
    from fera.gateway.stats import SessionStats
    stats = SessionStats()
    stats.record_turn("main/default", {
        "input_tokens": 3,
        "output_tokens": 500,
        "cache_creation_input_tokens": 5000,
        "cache_read_input_tokens": 75000,
        "model": "claude-opus-4-6",
        "duration_ms": 3000,
        "cost_usd": 0.02,
    })
    h = _make_handler(stats=stats)
    result = await h.handle("/session", "main/default")
    assert result is not None
    assert "%" in result.response
    assert "main/default" in result.response


@pytest.mark.asyncio
async def test_handle_sessions_lists_agent_sessions():
    """/sessions lists sessions for the current agent."""
    h = _make_handler()
    result = await h.handle("/sessions", "main/default")
    assert result is not None
    assert "default" in result.response
    assert "work" in result.response


@pytest.mark.asyncio
async def test_handle_sessions_no_agent_prefix_in_display():
    """Session names shown without agent/prefix."""
    h = _make_handler()
    result = await h.handle("/sessions", "main/default")
    assert "main/" not in result.response


@pytest.mark.asyncio
async def test_handle_session_with_arg_returns_error():
    """/session <name> returns 'not supported' — no switching."""
    h = _make_handler()
    result = await h.handle("/session work", "main/default")
    assert result is not None
    assert "not supported" in result.response.lower()


@pytest.mark.asyncio
async def test_handle_non_command_returns_none():
    h = _make_handler()
    result = await h.handle("hello", "main/default")
    assert result is None


@pytest.mark.asyncio
async def test_handle_status_no_data():
    h = _make_handler()
    result = await h.handle("/status", "main/default")
    assert result is not None
    assert "no data" in result.response.lower()


@pytest.mark.asyncio
async def test_handle_status_with_data():
    from fera.gateway.stats import SessionStats
    stats = SessionStats()
    stats.record_turn("main/default", {
        "input_tokens": 3,
        "output_tokens": 500,
        "cache_creation_input_tokens": 5000,
        "cache_read_input_tokens": 75000,
        "model": "claude-opus-4-6",
        "duration_ms": 3000,
        "cost_usd": 0.02,
    })
    h = _make_handler(stats=stats)
    result = await h.handle("/status", "main/default")
    assert "%" in result.response
    assert "opus" in result.response.lower() or "claude" in result.response.lower()


def test_match_model():
    h = _make_handler()
    assert h.match("/model") is True


def test_match_model_with_arg():
    h = _make_handler()
    assert h.match("/model haiku") is True


@pytest.mark.asyncio
async def test_handle_model_no_arg_lists_models(tmp_path):
    """'/model' with no argument shows current model and available models."""
    from fera.gateway.stats import SessionStats
    import json

    stats = SessionStats()
    stats.record_turn("main/default", {"model": "claude-sonnet-4-6"})
    (tmp_path / "models.json").write_text(json.dumps({
        "models": {
            "opus": "claude-opus-4-6",
            "sonnet": "claude-sonnet-4-6",
            "haiku": "claude-haiku-4-5-20251001",
        }
    }))

    ctx = _make_context(stats=stats, fera_home=tmp_path)
    h = SlashCommandHandler(ctx)
    result = await h.handle("/model", "main/default")

    assert result is not None
    assert "claude-sonnet-4-6" in result.response
    assert "opus" in result.response
    assert "haiku" in result.response


@pytest.mark.asyncio
async def test_handle_model_switch(tmp_path):
    """'/model opus' calls set_model on context."""
    import json

    (tmp_path / "models.json").write_text(json.dumps({
        "models": {"opus": "claude-opus-4-6"}
    }))

    ctx = _make_context(fera_home=tmp_path)
    ctx._runner.set_model = AsyncMock()
    ctx._runner._pool = True  # truthy to indicate pooling is enabled

    h = SlashCommandHandler(ctx)
    result = await h.handle("/model opus", "main/default")

    assert result is not None
    assert "claude-opus-4-6" in result.response
    ctx._runner.set_model.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_model_switch_no_pool(tmp_path):
    """'/model opus' returns error when no pool (ephemeral mode)."""
    import json

    (tmp_path / "models.json").write_text(json.dumps({
        "models": {"opus": "claude-opus-4-6"}
    }))

    ctx = _make_context(fera_home=tmp_path)
    ctx._runner._pool = None

    h = SlashCommandHandler(ctx)
    result = await h.handle("/model opus", "main/default")

    assert result is not None
    assert "pooled" in result.response.lower() or "not supported" in result.response.lower()


def test_match_clear():
    h = _make_handler()
    assert h.match("/clear") is True


@pytest.mark.asyncio
async def test_handle_clear_surfaces_archive_status():
    """clear response includes the archive status from clear_session."""
    ctx = _make_context()
    ctx.clear_session = AsyncMock(return_value="Archived (MEMORY_SAVED: memory/timeline/2026-02-25/001.md)")
    h = SlashCommandHandler(ctx)
    result = await h.handle("/clear", "main/default")
    assert result is not None
    ctx.clear_session.assert_awaited_once_with("main/default")
    assert "fresh context" in result.response.lower()
    assert "MEMORY_SAVED" in result.response


@pytest.mark.asyncio
async def test_handle_clear_without_archive_status():
    """clear response omits archive detail when status is empty."""
    ctx = _make_context()
    ctx.clear_session = AsyncMock(return_value="")
    h = SlashCommandHandler(ctx)
    result = await h.handle("/clear", "main/default")
    assert result is not None
    assert "fresh context" in result.response.lower()


@pytest.mark.asyncio
async def test_handle_clear_error_returns_error_result():
    ctx = _make_context()
    ctx.clear_session = AsyncMock(side_effect=RuntimeError("boom"))
    h = SlashCommandHandler(ctx)
    result = await h.handle("/clear", "main/default")
    assert result is not None
    assert "wrong" in result.response.lower() or "gateway logs" in result.response.lower()


def test_match_stop():
    h = _make_handler()
    assert h.match("/stop") is True


@pytest.mark.asyncio
async def test_handle_stop_calls_interrupt():
    ctx = _make_context()
    ctx.interrupt_session = AsyncMock()
    h = SlashCommandHandler(ctx)
    result = await h.handle("/stop", "main/default")
    assert result is not None
    ctx.interrupt_session.assert_awaited_once_with("main/default")


@pytest.mark.asyncio
async def test_handle_stop_reports_no_active_turn():
    ctx = _make_context()
    ctx.interrupt_session = AsyncMock(return_value=False)
    h = SlashCommandHandler(ctx)
    result = await h.handle("/stop", "main/default")
    assert result is not None
    assert "no active" in result.response.lower() or "nothing" in result.response.lower()


@pytest.mark.asyncio
async def test_handle_stop_reports_interrupted():
    ctx = _make_context()
    ctx.interrupt_session = AsyncMock(return_value=True)
    h = SlashCommandHandler(ctx)
    result = await h.handle("/stop", "main/default")
    assert result is not None
    assert "interrupt" in result.response.lower() or "stop" in result.response.lower()


# --- Model shortcut tests ---


def test_match_model_shortcut(tmp_path):
    """'/sonnet' matches when 'sonnet' is a known model alias."""
    import json

    (tmp_path / "models.json").write_text(json.dumps({
        "models": {"opus": "claude-opus-4-6", "sonnet": "claude-sonnet-4-6"}
    }))
    ctx = _make_context(fera_home=tmp_path)
    h = SlashCommandHandler(ctx)
    assert h.match("/sonnet") is True
    assert h.match("/opus") is True


def test_match_model_shortcut_unknown(tmp_path):
    """'/foobar' does NOT match when 'foobar' isn't a known alias."""
    import json

    (tmp_path / "models.json").write_text(json.dumps({
        "models": {"opus": "claude-opus-4-6"}
    }))
    ctx = _make_context(fera_home=tmp_path)
    h = SlashCommandHandler(ctx)
    assert h.match("/foobar") is False


def test_match_model_shortcut_no_models_file():
    """'/sonnet' doesn't match if there's no models.json."""
    ctx = _make_context(fera_home=Path("/nonexistent"))
    h = SlashCommandHandler(ctx)
    assert h.match("/sonnet") is False


@pytest.mark.asyncio
async def test_handle_model_shortcut(tmp_path):
    """'/sonnet' switches model just like '/model sonnet'."""
    import json

    (tmp_path / "models.json").write_text(json.dumps({
        "models": {"sonnet": "claude-sonnet-4-6"}
    }))
    ctx = _make_context(fera_home=tmp_path)
    ctx._runner.set_model = AsyncMock()
    ctx._runner._pool = True
    h = SlashCommandHandler(ctx)

    result = await h.handle("/sonnet", "main/default")
    assert result is not None
    assert "claude-sonnet-4-6" in result.response
    ctx._runner.set_model.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_model_shortcut_no_pool(tmp_path):
    """'/sonnet' returns pool-required error when not pooled."""
    import json

    (tmp_path / "models.json").write_text(json.dumps({
        "models": {"sonnet": "claude-sonnet-4-6"}
    }))
    ctx = _make_context(fera_home=tmp_path)
    ctx._runner._pool = None
    h = SlashCommandHandler(ctx)

    result = await h.handle("/sonnet", "main/default")
    assert result is not None
    assert "pooled" in result.response.lower()
