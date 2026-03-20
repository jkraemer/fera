import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from telegram.constants import ChatAction

import pytest

from fera.adapters.telegram import TelegramAdapter
from fera.adapters.base import AdapterContext, AdapterStatus
from fera.adapters.bus import EventBus
from fera.adapters.commands import SlashCommandHandler, CommandResult


def _make_context():
    bus = EventBus()
    runner = MagicMock()

    async def _empty_run_turn(*a, **kw):
        return
        yield  # async generator

    runner.run_turn = _empty_run_turn
    sessions = MagicMock()
    sessions.sessions_for_agent.return_value = [
        {"id": "main/default", "name": "default", "agent": "main"},
        {"id": "main/work", "name": "work", "agent": "main"},
    ]
    return AdapterContext(bus=bus, runner=runner, sessions=sessions, agent_name="main")


def _make_update(
    text,
    user_id=111,
    chat_id=111,
    first_name="Alex",
    chat_type="private",
    chat_title=None,
):
    """Build a minimal mock Telegram Update for a text message."""
    user = MagicMock()
    user.id = user_id
    user.first_name = first_name
    chat = MagicMock()
    chat.id = chat_id
    chat.type = chat_type
    chat.title = chat_title
    message = MagicMock()
    message.text = text
    message.from_user = user
    message.chat = chat
    message.photo = None
    message.document = None
    message.message_thread_id = None
    message.reply_text = AsyncMock()
    update = MagicMock()
    update.effective_user = user
    update.effective_chat = chat
    update.message = message
    return update


def test_adapter_name():
    adapter = TelegramAdapter(
        bot_token="fake:token",
        allowed_users={"alex": 111},
    )
    assert adapter.name == "telegram"


def test_adapter_status_before_start():
    adapter = TelegramAdapter(
        bot_token="fake:token",
        allowed_users={"alex": 111},
    )
    status = adapter.status()
    assert status.connected is False


@pytest.mark.asyncio
async def test_handle_message_authorized_user():
    adapter = TelegramAdapter(
        bot_token="fake:token",
        allowed_users={"alex": 111},
    )
    ctx = _make_context()
    adapter._context = ctx
    update = _make_update("hello", user_id=111)
    tg_context = MagicMock()

    # Should not raise for an authorized user; run_turn fires via asyncio.create_task
    await adapter._handle_message(update, tg_context)


@pytest.mark.asyncio
async def test_handle_message_unauthorized_user():
    adapter = TelegramAdapter(
        bot_token="fake:token",
        allowed_users={"alex": 111},
    )
    ctx = _make_context()
    adapter._context = ctx
    update = _make_update("hello", user_id=999)
    tg_context = MagicMock()

    await adapter._handle_message(update, tg_context)
    update.message.reply_text.assert_awaited_once()
    reply_text = update.message.reply_text.call_args[0][0]
    assert "999" in reply_text  # shows user their ID


@pytest.mark.asyncio
async def test_session_command_with_arg_returns_stats():
    """/session <name> ignores the arg and returns session stats (no switching)."""
    adapter = TelegramAdapter(
        bot_token="fake:token",
        allowed_users={"alex": 111},
    )
    ctx = _make_context()
    adapter._context = ctx
    adapter._commands = SlashCommandHandler(ctx)
    update = _make_update("/session work", user_id=111, chat_id=111)
    tg_context = MagicMock()

    await adapter._handle_command(update, tg_context)
    update.message.reply_text.assert_awaited_once()
    reply = update.message.reply_text.call_args[0][0]
    assert "Context:" in reply
    assert 111 not in adapter._chat_sessions


@pytest.mark.asyncio
async def test_sessions_command_lists_sessions():
    adapter = TelegramAdapter(
        bot_token="fake:token",
        allowed_users={"alex": 111},
    )
    ctx = _make_context()
    adapter._context = ctx
    adapter._commands = SlashCommandHandler(ctx)
    update = _make_update("/sessions", user_id=111, chat_id=111)
    tg_context = MagicMock()

    await adapter._handle_sessions_command(update, tg_context)
    update.message.reply_text.assert_awaited_once()
    reply = update.message.reply_text.call_args[0][0]
    assert "default" in reply
    assert "work" in reply


def _make_document_update(user_id=111, chat_id=111, filename="report.pdf"):
    """Build a mock Update for a document file message."""
    user = MagicMock()
    user.id = user_id
    chat = MagicMock()
    chat.id = chat_id
    chat.type = "private"
    doc = MagicMock()
    doc.file_name = filename
    file_mock = AsyncMock()
    file_mock.download_to_drive = AsyncMock()
    doc.get_file = AsyncMock(return_value=file_mock)
    message = MagicMock()
    message.voice = None
    message.audio = None
    message.photo = None
    message.document = doc
    message.caption = None
    message.message_thread_id = None
    message.reply_text = AsyncMock()
    update = MagicMock()
    update.effective_user = user
    update.effective_chat = chat
    update.message = message
    return update, file_mock


def _make_audio_update(user_id=111, chat_id=111, filename="recording.mp3"):
    """Build a mock Update for an audio file message."""
    user = MagicMock()
    user.id = user_id
    chat = MagicMock()
    chat.id = chat_id
    chat.type = "private"
    audio = MagicMock()
    audio.file_name = filename
    file_mock = AsyncMock()
    file_mock.download_to_drive = AsyncMock()
    audio.get_file = AsyncMock(return_value=file_mock)
    message = MagicMock()
    message.voice = None
    message.audio = audio
    message.photo = None
    message.document = None
    message.caption = None
    message.message_thread_id = None
    message.reply_text = AsyncMock()
    update = MagicMock()
    update.effective_user = user
    update.effective_chat = chat
    update.message = message
    return update, file_mock


def _make_voice_update(user_id=111, chat_id=111):
    """Build a mock Update for a Telegram voice note."""
    user = MagicMock()
    user.id = user_id
    chat = MagicMock()
    chat.id = chat_id
    chat.type = "private"
    voice = MagicMock()
    file_mock = AsyncMock()

    async def do_download(path: str) -> None:
        Path(path).write_bytes(b"fake_audio")

    file_mock.download_to_drive = AsyncMock(side_effect=do_download)
    voice.get_file = AsyncMock(return_value=file_mock)
    message = MagicMock()
    message.voice = voice
    message.audio = None
    message.photo = None
    message.document = None
    message.caption = None
    message.message_thread_id = None
    message.reply_text = AsyncMock()
    update = MagicMock()
    update.effective_user = user
    update.effective_chat = chat
    update.message = message
    return update, file_mock


@pytest.mark.asyncio
async def test_handle_audio_stores_without_transcription(tmp_path):
    adapter = TelegramAdapter(
        bot_token="t",
        allowed_users={"alex": 111},
        workspace_dir=tmp_path,
    )
    ctx = _make_context()
    adapter._context = ctx
    mock_transcriber = MagicMock()
    adapter._transcriber = mock_transcriber

    update, _ = _make_audio_update(user_id=111)

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_media(update, MagicMock())
        await asyncio.sleep(0)  # drain event loop so create_task fires

    mock_send.assert_awaited_once()
    text = mock_send.call_args[0][2]
    assert "recording.mp3" in text
    mock_transcriber.transcribe.assert_not_called()


@pytest.mark.asyncio
async def test_handle_voice_transcribes_and_sends(tmp_path):
    adapter = TelegramAdapter(
        bot_token="t",
        allowed_users={"alex": 111},
        workspace_dir=tmp_path,
    )
    ctx = _make_context()
    adapter._context = ctx

    seg1, seg2 = MagicMock(), MagicMock()
    seg1.text = " Hello world "
    seg2.text = " How are you "
    mock_transcriber = MagicMock()
    mock_transcriber.transcribe.return_value = ([seg1, seg2], MagicMock())
    adapter._transcriber = mock_transcriber

    update, _ = _make_voice_update(user_id=111)

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_voice(update, MagicMock())
        await asyncio.sleep(0)  # drain event loop so create_task fires

    mock_send.assert_awaited_once()
    text = mock_send.call_args[0][2]
    assert "Transcript:" in text
    assert "Hello world" in text
    assert "How are you" in text

    # Transcript file saved alongside audio
    txt_files = list(tmp_path.rglob("*.txt"))
    assert len(txt_files) == 1
    assert "Hello world" in txt_files[0].read_text()


@pytest.mark.asyncio
async def test_handle_voice_unauthorized():
    adapter = TelegramAdapter(
        bot_token="t",
        allowed_users={"alex": 111},
    )
    ctx = _make_context()
    adapter._context = ctx
    adapter._transcriber = MagicMock()

    update, _ = _make_voice_update(user_id=999)

    await adapter._handle_voice(update, MagicMock())

    update.message.reply_text.assert_awaited_once()
    reply = update.message.reply_text.call_args[0][0]
    assert "999" in reply
    adapter._transcriber.transcribe.assert_not_called()


@pytest.mark.asyncio
async def test_handle_voice_no_workspace():
    adapter = TelegramAdapter(
        bot_token="t",
        allowed_users={"alex": 111},
        workspace_dir=None,
    )
    ctx = _make_context()
    adapter._context = ctx
    adapter._transcriber = MagicMock()

    update, _ = _make_voice_update(user_id=111)

    await adapter._handle_voice(update, MagicMock())

    update.message.reply_text.assert_awaited_once()
    adapter._transcriber.transcribe.assert_not_called()


@pytest.mark.asyncio
async def test_session_command_with_arg_does_not_persist_mapping(tmp_path):
    """/session <name> does not switch or save any session mapping."""
    data_dir = tmp_path / "data"
    adapter = TelegramAdapter(
        bot_token="fake:token",
        allowed_users={"alex": 111},
        data_dir=data_dir,
    )
    ctx = _make_context()
    adapter._context = ctx
    adapter._commands = SlashCommandHandler(ctx)
    update = _make_update("/session work", user_id=111, chat_id=111)

    await adapter._handle_command(update, MagicMock())

    sessions_file = data_dir / "telegram_sessions.json"
    assert not sessions_file.exists()


@pytest.mark.asyncio
async def test_chat_sessions_loaded_on_start(tmp_path):
    """Previously saved group chat-session mappings are restored after restart."""
    import json

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    sessions_file = data_dir / "telegram_sessions.json"
    sessions_file.write_text(json.dumps({"999": "tg-team"}))

    adapter = TelegramAdapter(
        bot_token="fake:token",
        allowed_users={"alex": 111},
        data_dir=data_dir,
    )
    # Load without actually starting the Telegram connection
    adapter._load_chat_sessions()

    assert adapter._chat_sessions[999] == "tg-team"


@pytest.mark.asyncio
async def test_resubscribe_loaded_sessions_subscribes_chat_sessions(tmp_path):
    """Chat sessions loaded from file are subscribed on the bus after _resubscribe_loaded_sessions()."""
    import json

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "telegram_sessions.json").write_text(json.dumps({"123": "tg-group", "456": "tg-team"}))

    adapter = TelegramAdapter(bot_token="fake:token", allowed_users={"alex": 111}, data_dir=data_dir)
    ctx = _make_context()
    adapter._context = ctx
    adapter._load_chat_sessions()
    adapter._resubscribe_loaded_sessions()

    assert ctx._bus._subscribers.get("main/tg-group")
    assert ctx._bus._subscribers.get("main/tg-team")


@pytest.mark.asyncio
async def test_resubscribe_loaded_sessions_subscribes_topic_sessions(tmp_path):
    """Topic sessions loaded from file are subscribed on the bus after _resubscribe_loaded_sessions()."""
    import json

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "telegram_sessions.json").write_text(json.dumps({"123:42": "tg-fera-hq-t42"}))

    adapter = TelegramAdapter(bot_token="fake:token", allowed_users={"alex": 111}, data_dir=data_dir)
    ctx = _make_context()
    adapter._context = ctx
    adapter._load_chat_sessions()
    adapter._resubscribe_loaded_sessions()

    assert ctx._bus._subscribers.get("main/tg-fera-hq-t42")


@pytest.mark.asyncio
async def test_on_event_ignores_events_targeted_at_other_adapter():
    """Events with target_adapter set to a different adapter are ignored."""
    adapter = TelegramAdapter(
        bot_token="t", allowed_users={"alex": 1}
    )
    bot = AsyncMock()
    adapter._app = MagicMock()
    adapter._app.bot = bot

    event = {
        "event": "agent.text",
        "turn_source": "heartbeat",
        "target_adapter": "mattermost",
        "data": {"text": "hello"},
    }
    await adapter._on_event(event, chat_id=1)
    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_on_event_processes_events_targeted_at_self():
    """Events with target_adapter matching this adapter are processed."""
    adapter = TelegramAdapter(
        bot_token="t", allowed_users={"alex": 1}
    )
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    adapter._app = MagicMock()
    adapter._app.bot = bot

    event = {
        "event": "agent.text",
        "turn_source": "heartbeat",
        "target_adapter": "telegram",
        "data": {"text": "Your morning briefing"},
    }
    await adapter._on_event(event, chat_id=1)
    # Verify the event was NOT filtered — should have accumulated draft text
    assert adapter._draft_text.get(1) is not None


@pytest.mark.asyncio
async def test_on_event_ignores_agent_text_from_other_turn_source():
    """Agent replies from web-initiated turns are not sent to Telegram."""
    adapter = TelegramAdapter(
        bot_token="t", allowed_users={"alex": 1}
    )
    bot = AsyncMock()
    adapter._app = MagicMock()
    adapter._app.bot = bot

    event = {
        "event": "agent.text",
        "session": "default",
        "turn_source": "web",
        "data": {"text": "reply from web turn"},
    }
    await adapter._on_event(event, chat_id=111)

    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_on_event_streams_agent_text_for_own_turn_source():
    """Agent replies from Telegram-initiated turns ARE streamed to Telegram."""
    adapter = TelegramAdapter(
        bot_token="t", allowed_users={"alex": 1}
    )
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    adapter._app = MagicMock()
    adapter._app.bot = bot

    event = {
        "event": "agent.text",
        "session": "default",
        "turn_source": "telegram",
        "data": {"text": "response"},
    }
    await adapter._on_event(event, chat_id=111)

    bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["cron", "heartbeat"])
async def test_on_event_passes_through_proactive_sources(source):
    """Cron and heartbeat events are delivered even though they're not Telegram-sourced."""
    adapter = TelegramAdapter(
        bot_token="t", allowed_users={"alex": 1}
    )
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    adapter._app = MagicMock()
    adapter._app.bot = bot

    event = {
        "event": "agent.text",
        "session": "main/dm-alex",
        "turn_source": source,
        "data": {"text": "proactive message"},
    }
    await adapter._on_event(event, chat_id=111)

    bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_event_streams_agent_text_for_untagged_turn():
    """Events without turn_source are treated as own channel's (pass through)."""
    adapter = TelegramAdapter(
        bot_token="t", allowed_users={"alex": 1}
    )
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    adapter._app = MagicMock()
    adapter._app.bot = bot

    event = {
        "event": "agent.text",
        "session": "default",
        # no turn_source
        "data": {"text": "response"},
    }
    await adapter._on_event(event, chat_id=111)

    bot.send_message.assert_awaited_once()


def test_trusted_defaults_to_false():
    adapter = TelegramAdapter(bot_token="t", allowed_users={"alex": 1})
    assert adapter._trusted is False


def test_trusted_can_be_set():
    adapter = TelegramAdapter(bot_token="t", allowed_users={"alex": 1}, trusted=True)
    assert adapter._trusted is True


@pytest.mark.asyncio
async def test_untrusted_adapter_wraps_text_messages():
    adapter = TelegramAdapter(
        bot_token="t",
        allowed_users={"alex": 111},
        trusted=False,
    )
    ctx = _make_context()
    adapter._context = ctx
    update = _make_update("hello", user_id=111)

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_message(update, MagicMock())
        await asyncio.sleep(0)

    text = mock_send.call_args[0][2]
    assert "<untrusted" in text
    assert 'source="telegram"' in text
    assert "hello" in text


@pytest.mark.asyncio
async def test_trusted_adapter_does_not_wrap_text_messages():
    adapter = TelegramAdapter(
        bot_token="t",
        allowed_users={"alex": 111},
        trusted=True,
    )
    ctx = _make_context()
    adapter._context = ctx
    update = _make_update("hello", user_id=111)

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_message(update, MagicMock())
        await asyncio.sleep(0)

    text = mock_send.call_args[0][2]
    assert text == "hello"
    assert "<untrusted" not in text


@pytest.mark.asyncio
async def test_trusted_adapter_wraps_media_notifications(tmp_path):
    adapter = TelegramAdapter(
        bot_token="t",
        allowed_users={"alex": 111},
        trusted=True,
        workspace_dir=tmp_path,
    )
    ctx = _make_context()
    adapter._context = ctx
    update, _ = _make_audio_update(user_id=111)

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_media(update, MagicMock())
        await asyncio.sleep(0)

    text = mock_send.call_args[0][2]
    assert "<untrusted" in text
    assert 'source="telegram"' in text


@pytest.mark.asyncio
async def test_trusted_adapter_does_not_wrap_voice(tmp_path):
    adapter = TelegramAdapter(
        bot_token="t",
        allowed_users={"alex": 111},
        trusted=True,
        workspace_dir=tmp_path,
    )
    ctx = _make_context()
    adapter._context = ctx

    seg = MagicMock()
    seg.text = " Hello world "
    mock_transcriber = MagicMock()
    mock_transcriber.transcribe.return_value = ([seg], MagicMock())
    adapter._transcriber = mock_transcriber

    update, _ = _make_voice_update(user_id=111)

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_voice(update, MagicMock())
        await asyncio.sleep(0)

    text = mock_send.call_args[0][2]
    assert "<untrusted" not in text
    assert "Hello world" in text


@pytest.mark.asyncio
async def test_untrusted_adapter_wraps_voice(tmp_path):
    adapter = TelegramAdapter(
        bot_token="t",
        allowed_users={"alex": 111},
        trusted=False,
        workspace_dir=tmp_path,
    )
    ctx = _make_context()
    adapter._context = ctx

    seg = MagicMock()
    seg.text = " Hello world "
    mock_transcriber = MagicMock()
    mock_transcriber.transcribe.return_value = ([seg], MagicMock())
    adapter._transcriber = mock_transcriber

    update, _ = _make_voice_update(user_id=111)

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_voice(update, MagicMock())
        await asyncio.sleep(0)

    text = mock_send.call_args[0][2]
    assert "<untrusted" in text
    assert 'source="telegram"' in text


# --- Activity indicator tests ---


def _make_bot_adapter(chat_id=111):
    """Create an adapter with a mocked bot, ready for event-handling tests."""
    adapter = TelegramAdapter(
        bot_token="t", allowed_users={"alex": 1}
    )
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    bot.edit_message_text = AsyncMock()
    bot.delete_message = AsyncMock()
    bot.send_chat_action = AsyncMock()
    adapter._app = MagicMock()
    adapter._app.bot = bot
    return adapter, bot


@pytest.mark.asyncio
async def test_send_to_agent_starts_typing():
    """Typing indicator is sent when an agent turn begins."""
    adapter, bot = _make_bot_adapter()
    ctx = _make_context()
    adapter._context = ctx

    await adapter._send_to_agent(111, "default", "hello")

    bot.send_chat_action.assert_any_await(111, ChatAction.TYPING)


@pytest.mark.asyncio
async def test_typing_task_created_on_send():
    """A background typing task is running after _send_to_agent."""
    adapter, bot = _make_bot_adapter()
    ctx = _make_context()
    adapter._context = ctx

    await adapter._send_to_agent(111, "default", "hello")

    assert 111 in adapter._typing_tasks
    task = adapter._typing_tasks[111]
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_tool_use_creates_status_message():
    """First tool_use event sends a status message with the tool name."""
    adapter, bot = _make_bot_adapter()

    event = {
        "event": "agent.tool_use",
        "session": "default",
        "turn_source": "telegram",
        "data": {"id": "t1", "name": "WebSearch", "input": {}},
    }
    await adapter._on_event(event, chat_id=111)

    bot.send_message.assert_awaited_once_with(111, "WebSearch\u2026")
    assert 111 in adapter._status_messages


@pytest.mark.asyncio
async def test_tool_use_edits_status_message():
    """Subsequent tool_use events edit the existing status message."""
    adapter, bot = _make_bot_adapter()
    # Simulate existing status message
    adapter._status_messages[111] = 42

    event = {
        "event": "agent.tool_use",
        "session": "default",
        "turn_source": "telegram",
        "data": {"id": "t2", "name": "Read", "input": {}},
    }
    await adapter._on_event(event, chat_id=111)

    bot.edit_message_text.assert_awaited_once_with(
        "Read\u2026",
        chat_id=111,
        message_id=42,
    )
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_text_deletes_status_and_creates_draft():
    """First agent.text deletes the status message and starts the real draft."""
    adapter, bot = _make_bot_adapter()
    adapter._status_messages[111] = 99  # existing status

    event = {
        "event": "agent.text",
        "session": "default",
        "turn_source": "telegram",
        "data": {"text": "Hello!"},
    }
    await adapter._on_event(event, chat_id=111)

    bot.delete_message.assert_awaited_once_with(111, 99)
    assert 111 not in adapter._status_messages
    # Draft created with real text
    bot.send_message.assert_awaited_once()
    assert adapter._draft_text[111] == "Hello!"


@pytest.mark.asyncio
async def test_text_stops_typing():
    """Typing task is cancelled when real text arrives."""
    adapter, bot = _make_bot_adapter()
    # Simulate running typing task
    adapter._typing_tasks[111] = asyncio.create_task(asyncio.sleep(999))

    event = {
        "event": "agent.text",
        "session": "default",
        "turn_source": "telegram",
        "data": {"text": "Hi"},
    }
    await adapter._on_event(event, chat_id=111)
    await asyncio.sleep(0)  # let cancellation propagate

    assert 111 not in adapter._typing_tasks


@pytest.mark.asyncio
async def test_mid_turn_tool_use_restarts_typing_and_status():
    """After text has been streamed, a new tool_use restarts the cycle."""
    adapter, bot = _make_bot_adapter()
    # Simulate mid-turn state: draft exists, no status, no typing
    adapter._draft_messages[111] = 42
    adapter._draft_text[111] = "Some text so far"

    event = {
        "event": "agent.tool_use",
        "session": "default",
        "turn_source": "telegram",
        "data": {"id": "t3", "name": "Bash", "input": {}},
    }
    await adapter._on_event(event, chat_id=111)

    # New status message sent
    bot.send_message.assert_awaited_once_with(111, "Bash\u2026")
    assert 111 in adapter._status_messages
    # Typing restarted
    bot.send_chat_action.assert_any_await(111, ChatAction.TYPING)
    assert 111 in adapter._typing_tasks
    # Clean up
    adapter._typing_tasks[111].cancel()
    with pytest.raises(asyncio.CancelledError):
        await adapter._typing_tasks[111]


@pytest.mark.asyncio
async def test_second_text_after_mid_turn_tool_deletes_new_status():
    """Text arriving after a mid-turn tool_use deletes the new status message."""
    adapter, bot = _make_bot_adapter()
    # Simulate: draft exists with prior text, new status from mid-turn tool_use
    adapter._draft_messages[111] = 42
    adapter._draft_text[111] = "Prior text"
    adapter._status_messages[111] = 55  # mid-turn status
    adapter._last_edit[111] = 0  # allow edit

    event = {
        "event": "agent.text",
        "session": "default",
        "turn_source": "telegram",
        "data": {"text": " more text"},
    }
    await adapter._on_event(event, chat_id=111)

    bot.delete_message.assert_awaited_once_with(111, 55)
    assert 111 not in adapter._status_messages
    assert adapter._draft_text[111] == "Prior text\n\n more text"


@pytest.mark.asyncio
async def test_flush_cleans_up_typing_and_status():
    """agent.done cleans up typing tasks and leftover status messages."""
    adapter, bot = _make_bot_adapter()
    adapter._typing_tasks[111] = asyncio.create_task(asyncio.sleep(999))
    adapter._status_messages[111] = 77

    event = {
        "event": "agent.done",
        "session": "default",
        "turn_source": "telegram",
        "data": {},
    }
    await adapter._on_event(event, chat_id=111)
    await asyncio.sleep(0)

    assert 111 not in adapter._typing_tasks
    assert 111 not in adapter._status_messages
    bot.delete_message.assert_awaited_once_with(111, 77)


@pytest.mark.asyncio
async def test_tool_use_from_other_source_ignored():
    """tool_use events from other channels don't trigger status messages."""
    adapter, bot = _make_bot_adapter()

    event = {
        "event": "agent.tool_use",
        "session": "default",
        "turn_source": "web",
        "data": {"id": "t1", "name": "Bash", "input": {}},
    }
    await adapter._on_event(event, chat_id=111)

    bot.send_message.assert_not_awaited()
    assert 111 not in adapter._status_messages


@pytest.mark.asyncio
async def test_flush_draft_renders_markdown_as_telegram_html():
    """On flush, accumulated markdown is rendered as Telegram HTML."""
    adapter, bot = _make_bot_adapter()
    adapter._draft_messages[111] = 42
    adapter._draft_text[111] = "**bold** and *italic*"
    adapter._last_edit[111] = 0

    await adapter._flush_draft(111)

    bot.edit_message_text.assert_awaited_once()
    call_kwargs = bot.edit_message_text.call_args
    sent_text = call_kwargs[0][0]
    assert "<b>bold</b>" in sent_text
    assert "<i>italic</i>" in sent_text
    assert call_kwargs[1]["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_flush_draft_plain_text_still_works():
    """Plain text without markdown is rendered and sent as HTML."""
    adapter, bot = _make_bot_adapter()
    adapter._draft_messages[111] = 42
    adapter._draft_text[111] = "just plain text"
    adapter._last_edit[111] = 0

    await adapter._flush_draft(111)

    call_kwargs = bot.edit_message_text.call_args
    sent_text = call_kwargs[0][0]
    assert "just plain text" in sent_text
    assert call_kwargs[1]["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_no_status_when_text_comes_before_tool_use():
    """If text arrives before any tool_use, no status message is involved."""
    adapter, bot = _make_bot_adapter()

    event = {
        "event": "agent.text",
        "session": "default",
        "turn_source": "telegram",
        "data": {"text": "Quick reply"},
    }
    await adapter._on_event(event, chat_id=111)

    bot.delete_message.assert_not_awaited()
    bot.send_message.assert_awaited_once()
    assert adapter._draft_text[111] == "Quick reply"


@pytest.mark.asyncio
async def test_flush_draft_splits_long_messages():
    """Messages exceeding 4096 chars are split into multiple Telegram messages."""
    adapter, bot = _make_bot_adapter()
    long_text = "\n\n".join([f"Paragraph {i}. " + "x" * 200 for i in range(30)])
    adapter._draft_messages[111] = 42
    adapter._draft_text[111] = long_text
    adapter._last_edit[111] = 0

    await adapter._flush_draft(111)

    # First chunk edits the existing draft
    bot.edit_message_text.assert_awaited_once()
    assert bot.edit_message_text.call_args[1]["parse_mode"] == "HTML"
    # Additional chunks sent as new messages
    assert bot.send_message.call_count >= 1
    for call in bot.send_message.call_args_list:
        assert call[1]["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_flush_draft_skips_continuations_on_first_chunk_failure():
    """If editing the first chunk fails, continuation chunks are not sent."""
    adapter, bot = _make_bot_adapter()
    bot.edit_message_text = AsyncMock(side_effect=Exception("Telegram API error"))
    long_text = "\n\n".join([f"Paragraph {i}. " + "x" * 200 for i in range(30)])
    adapter._draft_messages[111] = 42
    adapter._draft_text[111] = long_text
    adapter._last_edit[111] = 0

    await adapter._flush_draft(111)

    bot.edit_message_text.assert_awaited_once()
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_text_bad_request_on_edit_is_debug_not_error(caplog):
    """BadRequest on throttled draft edit is logged at DEBUG, not ERROR."""
    import logging
    from telegram.error import BadRequest

    adapter, bot = _make_bot_adapter()
    bot.edit_message_text = AsyncMock(side_effect=BadRequest("Message is not modified"))
    adapter._draft_messages[111] = 42
    adapter._draft_text[111] = "existing text"
    adapter._last_edit[111] = 0  # allow edit

    with caplog.at_level(logging.DEBUG, logger="fera.adapters.telegram"):
        await adapter._stream_text(111, " more text")

    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


@pytest.mark.asyncio
async def test_flush_draft_bad_request_on_edit_is_debug_not_error(caplog):
    """BadRequest on flush edit is logged at DEBUG, not ERROR."""
    import logging
    from telegram.error import BadRequest

    adapter, bot = _make_bot_adapter()
    bot.edit_message_text = AsyncMock(side_effect=BadRequest("Message is not modified"))
    adapter._draft_messages[111] = 42
    adapter._draft_text[111] = "text"
    adapter._last_edit[111] = 0

    with caplog.at_level(logging.DEBUG, logger="fera.adapters.telegram"):
        await adapter._flush_draft(111)

    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


@pytest.mark.asyncio
async def test_delete_status_bad_request_is_debug_not_error(caplog):
    """BadRequest on delete_message (e.g. already deleted) is logged at DEBUG."""
    import logging
    from telegram.error import BadRequest

    adapter, bot = _make_bot_adapter()
    bot.delete_message = AsyncMock(side_effect=BadRequest("Message to delete not found"))
    adapter._status_messages[111] = 99

    with caplog.at_level(logging.DEBUG, logger="fera.adapters.telegram"):
        await adapter._delete_status(111)

    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


# --- Group chat support tests ---


@pytest.mark.asyncio
async def test_group_message_accepted():
    """Messages from group chats are processed, not silently dropped."""
    adapter = TelegramAdapter(
        bot_token="t", allowed_users={"alex": 111}
    )
    ctx = _make_context()
    adapter._context = ctx
    update = _make_update(
        "hello", user_id=111, chat_id=999, chat_type="group", chat_title="Test Group"
    )

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_message(update, MagicMock())
        await asyncio.sleep(0)

    mock_send.assert_awaited_once()


@pytest.mark.asyncio
async def test_supergroup_message_accepted():
    """Messages from supergroup chats are processed, not silently dropped."""
    adapter = TelegramAdapter(
        bot_token="t", allowed_users={"alex": 111}
    )
    ctx = _make_context()
    adapter._context = ctx
    update = _make_update(
        "hello",
        user_id=111,
        chat_id=999,
        chat_type="supergroup",
        chat_title="Test Supergroup",
    )

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_message(update, MagicMock())
        await asyncio.sleep(0)

    mock_send.assert_awaited_once()


@pytest.mark.asyncio
async def test_channel_message_dropped():
    """Messages from channel chats (not a supported type) are still dropped."""
    adapter = TelegramAdapter(
        bot_token="t", allowed_users={"alex": 111}
    )
    ctx = _make_context()
    adapter._context = ctx
    update = _make_update("hello", user_id=111, chat_id=999, chat_type="channel")

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_message(update, MagicMock())
        await asyncio.sleep(0)

    mock_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_auto_session_from_title():
    """First message from a group auto-creates a session named from the group title."""
    adapter = TelegramAdapter(
        bot_token="t", allowed_users={"alex": 111}
    )
    ctx = _make_context()
    adapter._context = ctx
    update = _make_update(
        "hello", user_id=111, chat_id=999, chat_type="group", chat_title="Family Chat"
    )

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_message(update, MagicMock())
        await asyncio.sleep(0)

    session = mock_send.call_args[0][1]
    assert session == "tg-family-chat"


@pytest.mark.asyncio
async def test_group_auto_session_slugifies_title():
    """Special characters and spaces in group title are slugified."""
    adapter = TelegramAdapter(
        bot_token="t", allowed_users={"alex": 111}
    )
    ctx = _make_context()
    adapter._context = ctx
    update = _make_update(
        "hello",
        user_id=111,
        chat_id=999,
        chat_type="group",
        chat_title="My Work & Projects!",
    )

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_message(update, MagicMock())
        await asyncio.sleep(0)

    session = mock_send.call_args[0][1]
    assert session == "tg-my-work-projects"


@pytest.mark.asyncio
async def test_group_auto_session_stored_in_chat_sessions():
    """Auto-created group session is stored in _chat_sessions for future lookups."""
    adapter = TelegramAdapter(
        bot_token="t", allowed_users={"alex": 111}
    )
    ctx = _make_context()
    adapter._context = ctx
    update = _make_update(
        "hello", user_id=111, chat_id=999, chat_type="group", chat_title="Family Chat"
    )

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock):
        await adapter._handle_message(update, MagicMock())
        await asyncio.sleep(0)

    assert adapter._chat_sessions[999] == "tg-family-chat"


@pytest.mark.asyncio
async def test_group_auto_session_stable_across_messages():
    """Second message from same group reuses the same auto-created session."""
    adapter = TelegramAdapter(
        bot_token="t", allowed_users={"alex": 111}
    )
    ctx = _make_context()
    adapter._context = ctx

    sessions_seen = []

    async def capture_session(chat_id, session, text, thread_id=None):
        sessions_seen.append(session)

    with patch.object(adapter, "_send_to_agent", new=capture_session):
        for _ in range(2):
            update = _make_update(
                "msg",
                user_id=111,
                chat_id=999,
                chat_type="group",
                chat_title="Family Chat",
            )
            await adapter._handle_message(update, MagicMock())
            await asyncio.sleep(0)

    assert sessions_seen == ["tg-family-chat", "tg-family-chat"]


@pytest.mark.asyncio
async def test_private_chat_uses_canonical_name_as_session():
    """Private DMs use canonical name to form session (dm-{canonical_name})."""
    adapter = TelegramAdapter(
        bot_token="t", allowed_users={"alex": 111}
    )
    ctx = _make_context()
    adapter._context = ctx
    update = _make_update("hello", user_id=111, chat_id=111, chat_type="private")

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_message(update, MagicMock())
        await asyncio.sleep(0)

    session = mock_send.call_args[0][1]
    assert session == "dm-alex"


@pytest.mark.asyncio
async def test_session_command_noarg_shows_stats():
    """/session without argument shows current session stats."""
    adapter = TelegramAdapter(
        bot_token="t", allowed_users={"alex": 111}
    )
    ctx = _make_context()
    adapter._context = ctx
    adapter._commands = SlashCommandHandler(ctx)
    update = _make_update("/session", user_id=111, chat_id=111, chat_type="private")

    await adapter._handle_command(update, MagicMock())

    update.message.reply_text.assert_awaited_once()
    reply = update.message.reply_text.call_args[0][0]
    assert "Session:" in reply
    assert "Context:" in reply


@pytest.mark.asyncio
async def test_status_command_shows_session_stats():
    from fera.gateway.stats import SessionStats

    adapter = TelegramAdapter(bot_token="t", allowed_users={"alex": 111})
    stats = SessionStats()
    stats.record_turn(
        "main/dm-alex",
        {
            "input_tokens": 3,
            "output_tokens": 3000,
            "cache_creation_input_tokens": 5000,
            "cache_read_input_tokens": 75000,
            "model": "claude-opus-4-6",
            "duration_ms": 5000,
            "cost_usd": 0.05,
        },
    )
    ctx = _make_context()
    ctx._stats = stats
    adapter._context = ctx
    adapter._commands = SlashCommandHandler(ctx)

    update = _make_update("/status", user_id=111)
    await adapter._handle_status_command(update, MagicMock())

    update.message.reply_text.assert_awaited_once()
    reply = update.message.reply_text.call_args[0][0]
    assert "%" in reply
    assert "opus" in reply.lower() or "claude" in reply.lower()


@pytest.mark.asyncio
async def test_status_command_unauthorized():
    adapter = TelegramAdapter(bot_token="t", allowed_users={"alex": 111})
    ctx = _make_context()
    adapter._context = ctx

    update = _make_update("/status", user_id=999)
    await adapter._handle_status_command(update, MagicMock())

    update.message.reply_text.assert_awaited_once()
    reply = update.message.reply_text.call_args[0][0]
    assert "999" in reply


@pytest.mark.asyncio
async def test_status_command_no_data():
    adapter = TelegramAdapter(bot_token="t", allowed_users={"alex": 111})
    ctx = _make_context()
    adapter._context = ctx
    adapter._commands = SlashCommandHandler(ctx)

    update = _make_update("/status", user_id=111)
    await adapter._handle_status_command(update, MagicMock())

    update.message.reply_text.assert_awaited_once()
    reply = update.message.reply_text.call_args[0][0]
    assert "no data" in reply.lower()


@pytest.mark.asyncio
async def test_handle_sessions_command_shows_composite_ids(tmp_path):
    """_handle_sessions_command lists session names (short names)."""
    adapter = TelegramAdapter(
        bot_token="fake:token",
        allowed_users={"alex": 111},
    )
    context = _make_context()
    adapter._context = context
    adapter._commands = SlashCommandHandler(context)
    adapter._connected = True

    update = _make_update("/sessions", user_id=111, chat_id=111)
    update.effective_chat.id = 111
    await adapter._handle_sessions_command(update, None)

    call_args = update.message.reply_text.call_args[0][0]
    assert "default" in call_args
    assert "work" in call_args


def test_session_for_private_chat_uses_canonical_name():
    adapter = TelegramAdapter(
        bot_token="fake:token",
        allowed_users={"alex": 111},
    )
    chat = MagicMock()
    chat.type = "private"
    chat.id = 111
    user = MagicMock()
    user.id = 111
    assert adapter._session_for_message(chat, user) == "dm-alex"


def test_session_for_group_chat_uses_slug():
    adapter = TelegramAdapter(
        bot_token="fake:token",
        allowed_users={"alex": 111},
    )
    chat = MagicMock()
    chat.type = "group"
    chat.id = 999
    chat.title = "My Team"
    user = MagicMock()
    user.id = 111
    assert adapter._session_for_message(chat, user) == "tg-my-team"


# --- /clear command tests ---


@pytest.mark.asyncio
async def test_handle_clear_command_unauthorized():
    adapter = TelegramAdapter(bot_token="t", allowed_users={"alex": 111})
    ctx = _make_context()
    adapter._context = ctx
    update = _make_update("/clear", user_id=999)

    await adapter._handle_clear_command(update, MagicMock())

    update.message.reply_text.assert_awaited_once()
    reply = update.message.reply_text.call_args[0][0]
    assert "999" in reply


@pytest.mark.asyncio
async def test_handle_clear_command_sends_wait_then_result():
    adapter = TelegramAdapter(bot_token="t", allowed_users={"alex": 111})
    ctx = _make_context()
    adapter._context = ctx

    commands_mock = MagicMock()
    commands_mock.handle = AsyncMock(return_value=CommandResult(response="Done. Your next message starts a fresh context."))
    adapter._commands = commands_mock

    bot = AsyncMock()
    adapter._app = MagicMock()
    adapter._app.bot = bot

    update = _make_update("/clear", user_id=111)

    await adapter._handle_clear_command(update, MagicMock())

    update.message.reply_text.assert_awaited_once()
    ack = update.message.reply_text.call_args[0][0]
    assert "please wait" in ack.lower()

    bot.send_message.assert_awaited_once()
    done_msg = bot.send_message.call_args[0][1]
    assert "fresh context" in done_msg.lower()


def test_session_for_private_chat_unknown_user_falls_back():
    adapter = TelegramAdapter(
        bot_token="fake:token",
        allowed_users={"alex": 111},
    )
    chat = MagicMock()
    chat.type = "private"
    chat.id = 999
    user = MagicMock()
    user.id = 999  # not in allowed_users
    session = adapter._session_for_message(chat, user)
    assert session == "tg-dm-999"


# --- Forum group / topic thread routing tests ---


@pytest.mark.asyncio
async def test_thread_id_passed_to_send_for_topic_message():
    """message_thread_id is passed to _send_to_agent when a message arrives from a forum topic."""
    adapter = TelegramAdapter(bot_token="t", allowed_users={"alex": 111})
    ctx = _make_context()
    adapter._context = ctx
    update = _make_update(
        "hello", user_id=111, chat_id=999,
        chat_type="supergroup", chat_title="My Group",
    )
    update.message.message_thread_id = 42

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_message(update, MagicMock())
        await asyncio.sleep(0)

    mock_send.assert_awaited_once()
    assert mock_send.call_args[0][3] == 42


@pytest.mark.asyncio
async def test_no_thread_id_for_non_topic_message():
    """Non-topic messages (message_thread_id=None) pass thread_id=None to _send_to_agent."""
    adapter = TelegramAdapter(bot_token="t", allowed_users={"alex": 111})
    ctx = _make_context()
    adapter._context = ctx
    update = _make_update("hello", user_id=111, chat_id=999,
                          chat_type="supergroup", chat_title="My Group")
    # message_thread_id is already None from _make_update

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_message(update, MagicMock())
        await asyncio.sleep(0)

    mock_send.assert_awaited_once()
    assert mock_send.call_args[0][3] is None


@pytest.mark.asyncio
async def test_stream_text_uses_thread_id():
    """send_message for initial draft includes message_thread_id for forum topics."""
    adapter, bot = _make_bot_adapter()

    await adapter._stream_text(111, "hello", thread_id=42)

    bot.send_message.assert_awaited_once()
    call_kwargs = bot.send_message.call_args[1]
    assert call_kwargs.get("message_thread_id") == 42


@pytest.mark.asyncio
async def test_stream_text_no_thread_id_kwarg_for_non_topic():
    """send_message for non-topic chats has no message_thread_id keyword arg."""
    adapter, bot = _make_bot_adapter()
    # No _chat_thread entry for chat 111

    await adapter._stream_text(111, "hello")

    bot.send_message.assert_awaited_once()
    call_kwargs = bot.send_message.call_args[1]
    assert "message_thread_id" not in call_kwargs


@pytest.mark.asyncio
async def test_typing_uses_thread_id():
    """send_chat_action (typing indicator) includes message_thread_id for forum topics."""
    adapter, bot = _make_bot_adapter()

    await adapter._start_typing(111, thread_id=42)
    adapter._stop_typing(111)

    bot.send_chat_action.assert_awaited()
    first_call_kwargs = bot.send_chat_action.call_args_list[0][1]
    assert first_call_kwargs.get("message_thread_id") == 42


@pytest.mark.asyncio
async def test_status_message_uses_thread_id():
    """Status send_message includes message_thread_id for forum topics."""
    adapter, bot = _make_bot_adapter()

    await adapter._update_status(111, "WebSearch", thread_id=42)

    bot.send_message.assert_awaited_once()
    call_kwargs = bot.send_message.call_args[1]
    assert call_kwargs.get("message_thread_id") == 42


@pytest.mark.asyncio
async def test_flush_continuation_uses_thread_id():
    """Continuation chunks sent during flush_draft include message_thread_id."""
    adapter, bot = _make_bot_adapter()
    long_text = "\n\n".join([f"Paragraph {i}. " + "x" * 200 for i in range(30)])
    adapter._draft_messages[111] = 99
    adapter._draft_text[111] = long_text
    adapter._last_edit[111] = 0

    await adapter._flush_draft(111, thread_id=42)

    assert bot.send_message.call_count >= 1
    for call in bot.send_message.call_args_list:
        assert call[1].get("message_thread_id") == 42


# --- Topic-per-session tests ---


@pytest.mark.asyncio
async def test_supergroup_topic_message_gets_topic_session():
    """A message in a forum topic gets a session name with the thread_id suffix."""
    adapter = TelegramAdapter(bot_token="t", allowed_users={"alex": 111})
    ctx = _make_context()
    adapter._context = ctx
    update = _make_update(
        "hello", user_id=111, chat_id=999,
        chat_type="supergroup", chat_title="My Group",
    )
    update.message.message_thread_id = 42

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_message(update, MagicMock())
        await asyncio.sleep(0)

    session = mock_send.call_args[0][1]
    assert session == "tg-my-group-t42"


@pytest.mark.asyncio
async def test_different_topics_in_same_group_get_different_sessions():
    """Two different topics in the same supergroup map to separate sessions."""
    adapter = TelegramAdapter(bot_token="t", allowed_users={"alex": 111})
    ctx = _make_context()
    adapter._context = ctx

    sessions_seen = []

    async def capture(chat_id, session, text, thread_id=None):
        sessions_seen.append(session)

    with patch.object(adapter, "_send_to_agent", new=capture):
        for thread_id in (10, 20):
            update = _make_update(
                "msg", user_id=111, chat_id=999,
                chat_type="supergroup", chat_title="My Group",
            )
            update.message.message_thread_id = thread_id
            await adapter._handle_message(update, MagicMock())
            await asyncio.sleep(0)

    assert sessions_seen[0] != sessions_seen[1]
    assert "t10" in sessions_seen[0]
    assert "t20" in sessions_seen[1]


@pytest.mark.asyncio
async def test_non_topic_supergroup_session_unchanged():
    """A supergroup message without a thread_id uses the original group session name."""
    adapter = TelegramAdapter(bot_token="t", allowed_users={"alex": 111})
    ctx = _make_context()
    adapter._context = ctx
    update = _make_update(
        "hello", user_id=111, chat_id=999,
        chat_type="supergroup", chat_title="My Group",
    )
    # message_thread_id is None (default in _make_update)

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_message(update, MagicMock())
        await asyncio.sleep(0)

    session = mock_send.call_args[0][1]
    assert session == "tg-my-group"


@pytest.mark.asyncio
async def test_topic_session_stable_across_messages():
    """Same topic ID always maps to the same session name."""
    adapter = TelegramAdapter(bot_token="t", allowed_users={"alex": 111})
    ctx = _make_context()
    adapter._context = ctx

    sessions_seen = []

    async def capture(chat_id, session, text, thread_id=None):
        sessions_seen.append(session)

    with patch.object(adapter, "_send_to_agent", new=capture):
        for _ in range(2):
            update = _make_update(
                "msg", user_id=111, chat_id=999,
                chat_type="supergroup", chat_title="My Group",
            )
            update.message.message_thread_id = 42
            await adapter._handle_message(update, MagicMock())
            await asyncio.sleep(0)

    assert sessions_seen == ["tg-my-group-t42", "tg-my-group-t42"]


@pytest.mark.asyncio
async def test_topic_session_persisted(tmp_path):
    """Topic session mappings are saved to telegram_sessions.json."""
    import json

    data_dir = tmp_path / "data"
    adapter = TelegramAdapter(
        bot_token="t", allowed_users={"alex": 111}, data_dir=data_dir,
    )
    ctx = _make_context()
    adapter._context = ctx
    update = _make_update(
        "hello", user_id=111, chat_id=999,
        chat_type="supergroup", chat_title="My Group",
    )
    update.message.message_thread_id = 42

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock):
        await adapter._handle_message(update, MagicMock())
        await asyncio.sleep(0)

    sessions_file = data_dir / "telegram_sessions.json"
    data = json.loads(sessions_file.read_text())
    assert data.get("999:42") == "tg-my-group-t42"


@pytest.mark.asyncio
async def test_topic_session_loaded_from_persistence(tmp_path):
    """A previously-saved topic session is restored from telegram_sessions.json."""
    import json

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    sessions_file = data_dir / "telegram_sessions.json"
    sessions_file.write_text(json.dumps({"999:42": "tg-my-group-t42"}))

    adapter = TelegramAdapter(
        bot_token="t", allowed_users={"alex": 111}, data_dir=data_dir,
    )
    adapter._load_chat_sessions()

    assert adapter._topic_sessions[(999, 42)] == "tg-my-group-t42"


def test_session_for_topic_uses_thread_suffix():
    """_session_for_message with thread_id produces a topic-scoped session name."""
    adapter = TelegramAdapter(bot_token="t", allowed_users={"alex": 111})
    chat = MagicMock()
    chat.type = "supergroup"
    chat.id = 999
    chat.title = "Fera HQ"
    user = MagicMock()
    user.id = 111
    assert adapter._session_for_message(chat, user, thread_id=7) == "tg-fera-hq-t7"


def test_session_for_group_no_thread_unchanged():
    """_session_for_message without thread_id is unchanged for group chats."""
    adapter = TelegramAdapter(bot_token="t", allowed_users={"alex": 111})
    chat = MagicMock()
    chat.type = "group"
    chat.id = 999
    chat.title = "Fera HQ"
    user = MagicMock()
    user.id = 111
    assert adapter._session_for_message(chat, user) == "tg-fera-hq"


# --- Topic routing correctness (Bug B: thread_id in callback closure) ---


@pytest.mark.asyncio
async def test_topic_response_routes_to_correct_thread():
    """Responses for a topic session go to that topic's thread_id via the callback
    closure, not via a shared _chat_thread lookup that can be stomped."""
    adapter, bot = _make_bot_adapter()

    event = {
        "event": "agent.text",
        "session": "main/tg-forum-t7",
        "turn_source": "telegram",
        "data": {"text": "response for topic 7"},
    }
    # _on_event must accept thread_id so the callback can close over it.
    await adapter._on_event(event, chat_id=111, thread_id=7)

    send_kwargs = bot.send_message.call_args[1]
    assert send_kwargs.get("message_thread_id") == 7


# --- Command handlers use topic session (Bug A: missing thread_id passthrough) ---


@pytest.mark.asyncio
async def test_status_command_in_forum_topic_uses_topic_session():
    """/status sent from a forum topic queries the topic-scoped session."""
    adapter = TelegramAdapter(bot_token="t", allowed_users={"alex": 111})
    adapter._topic_sessions[(1000, 7)] = "tg-forum-t7"

    ctx = _make_context()
    adapter._context = ctx
    commands = MagicMock()
    commands.handle = AsyncMock(return_value=MagicMock(response="ok"))
    adapter._commands = commands

    update = _make_update(
        "/status", user_id=111, chat_id=1000,
        chat_type="supergroup", chat_title="Forum",
    )
    update.message.message_thread_id = 7

    await adapter._handle_status_command(update, MagicMock())

    session_arg = commands.handle.call_args[0][1]
    assert session_arg == "tg-forum-t7"


@pytest.mark.asyncio
async def test_clear_command_in_forum_topic_uses_topic_session():
    """/clear sent from a forum topic clears the topic-scoped session."""
    adapter = TelegramAdapter(bot_token="t", allowed_users={"alex": 111})
    adapter._topic_sessions[(1000, 7)] = "tg-forum-t7"

    ctx = _make_context()
    adapter._context = ctx

    commands_mock = MagicMock()
    commands_mock.handle = AsyncMock(return_value=CommandResult(response="Done. Your next message starts a fresh context."))
    adapter._commands = commands_mock

    bot = AsyncMock()
    bot.send_message = AsyncMock()
    adapter._app = MagicMock()
    adapter._app.bot = bot

    update = _make_update(
        "/clear", user_id=111, chat_id=1000,
        chat_type="supergroup", chat_title="Forum",
    )
    update.message.message_thread_id = 7

    await adapter._handle_clear_command(update, MagicMock())

    commands_mock.handle.assert_awaited_once_with("/clear", "tg-forum-t7")
    bot.send_message.assert_awaited_once()
    msg = bot.send_message.call_args[0][1]
    assert "fresh context" in msg.lower()


# --- Filename sanitization tests ---


@pytest.mark.asyncio
async def test_handle_media_document_strips_path_traversal_from_filename(tmp_path):
    """Document filename with path traversal components is saved under inbox using only the basename."""
    adapter = TelegramAdapter(
        bot_token="t",
        allowed_users={"alex": 111},
        workspace_dir=tmp_path,
    )
    ctx = _make_context()
    adapter._context = ctx

    update, _ = _make_document_update(user_id=111, filename="../../etc/passwd")

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_media(update, MagicMock())
        await asyncio.sleep(0)

    mock_send.assert_awaited_once()
    text = mock_send.call_args[0][2]
    # Notification path must not contain traversal sequences
    assert ".." not in text
    # Sanitized basename should appear
    assert "passwd" in text


@pytest.mark.asyncio
async def test_handle_media_audio_strips_path_traversal_from_filename(tmp_path):
    """Audio filename with path traversal components is saved under inbox using only the basename."""
    adapter = TelegramAdapter(
        bot_token="t",
        allowed_users={"alex": 111},
        workspace_dir=tmp_path,
    )
    ctx = _make_context()
    adapter._context = ctx

    update, _ = _make_audio_update(user_id=111, filename="../../etc/shadow")

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_media(update, MagicMock())
        await asyncio.sleep(0)

    mock_send.assert_awaited_once()
    text = mock_send.call_args[0][2]
    assert ".." not in text
    assert "shadow" in text


# --- Group chat authorization and sender identification ---


@pytest.mark.asyncio
async def test_group_message_accepts_non_allowlisted_user():
    """Messages from users not in allowed_users are accepted in group chats."""
    adapter = TelegramAdapter(bot_token="t", allowed_users={"alex": 111})
    ctx = _make_context()
    adapter._context = ctx
    update = _make_update(
        "hello from forge",
        user_id=999,
        chat_id=888,
        first_name="Forge",
        chat_type="group",
        chat_title="Agents",
    )

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_message(update, MagicMock())
        await asyncio.sleep(0)

    mock_send.assert_awaited_once()
    update.message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_dm_still_rejects_non_allowlisted_user():
    """DMs from users not in allowed_users are still rejected."""
    adapter = TelegramAdapter(bot_token="t", allowed_users={"alex": 111})
    ctx = _make_context()
    adapter._context = ctx
    update = _make_update("hello", user_id=999, chat_type="private")

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_message(update, MagicMock())
        await asyncio.sleep(0)

    mock_send.assert_not_awaited()
    update.message.reply_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_group_message_includes_sender_untrusted():
    """In untrusted group chats, messages include sender= in the untrusted tag."""
    adapter = TelegramAdapter(bot_token="t", allowed_users={"alex": 111}, trusted=False)
    ctx = _make_context()
    adapter._context = ctx
    update = _make_update(
        "hello", user_id=111, chat_id=888,
        first_name="Alex", chat_type="group", chat_title="Agents",
    )

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_message(update, MagicMock())
        await asyncio.sleep(0)

    text = mock_send.call_args[0][2]
    assert 'sender="Alex"' in text


@pytest.mark.asyncio
async def test_group_message_includes_sender_trusted():
    """In trusted group chats, messages are prefixed with [sender]."""
    adapter = TelegramAdapter(bot_token="t", allowed_users={"alex": 111}, trusted=True)
    ctx = _make_context()
    adapter._context = ctx
    update = _make_update(
        "hello", user_id=111, chat_id=888,
        first_name="Alex", chat_type="group", chat_title="Agents",
    )

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_message(update, MagicMock())
        await asyncio.sleep(0)

    text = mock_send.call_args[0][2]
    assert text == "[Alex] hello"


@pytest.mark.asyncio
async def test_dm_message_no_sender():
    """DM messages don't include sender information."""
    adapter = TelegramAdapter(bot_token="t", allowed_users={"alex": 111}, trusted=False)
    ctx = _make_context()
    adapter._context = ctx
    update = _make_update("hello", user_id=111, chat_type="private")

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_message(update, MagicMock())
        await asyncio.sleep(0)

    text = mock_send.call_args[0][2]
    assert "sender" not in text


@pytest.mark.asyncio
async def test_group_voice_accepts_non_allowlisted_user(tmp_path):
    """Voice notes from non-allowlisted users are accepted in group chats."""
    adapter = TelegramAdapter(
        bot_token="t", allowed_users={"alex": 111}, workspace_dir=tmp_path,
    )
    ctx = _make_context()
    adapter._context = ctx

    seg = MagicMock()
    seg.text = " Hello "
    mock_transcriber = MagicMock()
    mock_transcriber.transcribe.return_value = ([seg], MagicMock())
    adapter._transcriber = mock_transcriber

    update, _ = _make_voice_update(user_id=999)
    update.effective_chat.type = "group"
    update.effective_chat.title = "Agents"

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_voice(update, MagicMock())
        await asyncio.sleep(0)

    mock_send.assert_awaited_once()
    update.message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_command_accepts_non_allowlisted_user():
    """Commands from non-allowlisted users are accepted in group chats."""
    adapter = TelegramAdapter(bot_token="t", allowed_users={"alex": 111})
    ctx = _make_context()
    adapter._context = ctx
    adapter._commands = SlashCommandHandler(ctx)
    update = _make_update(
        "/status", user_id=999, chat_id=888,
        chat_type="group", chat_title="Agents",
    )

    await adapter._handle_status_command(update, MagicMock())

    # Should get a response, not "Not authorized"
    update.message.reply_text.assert_awaited_once()
    reply = update.message.reply_text.call_args[0][0]
    assert "999" not in reply  # no "Your user ID is: 999"


# --- /stop command ---


@pytest.mark.asyncio
async def test_stop_command_calls_interrupt():
    adapter = TelegramAdapter(bot_token="t", allowed_users={"alex": 111})
    ctx = _make_context()
    adapter._context = ctx

    commands_mock = MagicMock()
    commands_mock.handle = AsyncMock(return_value=CommandResult(response="Interrupted."))
    adapter._commands = commands_mock

    update = _make_update("/stop", user_id=111)
    await adapter._handle_stop_command(update, MagicMock())

    commands_mock.handle.assert_awaited_once()
    assert commands_mock.handle.call_args[0][0] == "/stop"
    update.message.reply_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_command_unauthorized():
    adapter = TelegramAdapter(bot_token="t", allowed_users={"alex": 111})
    ctx = _make_context()
    adapter._context = ctx
    update = _make_update("/stop", user_id=999)

    await adapter._handle_stop_command(update, MagicMock())

    update.message.reply_text.assert_awaited_once()
    reply = update.message.reply_text.call_args[0][0]
    assert "999" in reply


# --- AskUserQuestion inline keyboard tests ---


@pytest.mark.asyncio
async def test_on_event_user_question_sends_inline_keyboard():
    """agent.user_question event sends a message with inline keyboard buttons."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    adapter, bot = _make_bot_adapter()

    event = {
        "event": "agent.user_question",
        "session": "main/dm-alex",
        "data": {
            "question_id": "main/dm-alex:q1",
            "questions": [
                {
                    "question": "Which database?",
                    "header": "DB",
                    "options": [
                        {"label": "Postgres", "description": "SQL"},
                        {"label": "SQLite", "description": "Embedded"},
                    ],
                    "multiSelect": False,
                }
            ],
        },
    }
    await adapter._on_event(event, chat_id=111)

    bot.send_message.assert_awaited_once()
    call_kwargs = bot.send_message.call_args
    assert "reply_markup" in call_kwargs[1]
    markup = call_kwargs[1]["reply_markup"]
    assert isinstance(markup, InlineKeyboardMarkup)
    # Two option buttons
    buttons = markup.inline_keyboard
    assert len(buttons) >= 2


@pytest.mark.asyncio
async def test_on_event_user_question_multi_question():
    """Multiple questions in a single event each get their own keyboard."""
    adapter, bot = _make_bot_adapter()

    event = {
        "event": "agent.user_question",
        "session": "main/dm-alex",
        "data": {
            "question_id": "main/dm-alex:q1",
            "questions": [
                {
                    "question": "Which DB?",
                    "header": "DB",
                    "options": [
                        {"label": "Postgres", "description": "SQL"},
                        {"label": "SQLite", "description": "Embedded"},
                    ],
                    "multiSelect": False,
                },
                {
                    "question": "Which cache?",
                    "header": "Cache",
                    "options": [
                        {"label": "Redis", "description": "In-memory"},
                        {"label": "Memcached", "description": "Distributed"},
                    ],
                    "multiSelect": False,
                },
            ],
        },
    }
    await adapter._on_event(event, chat_id=111)

    # Should send one message per question
    assert bot.send_message.call_count == 2


@pytest.mark.asyncio
async def test_handle_question_callback_answers_question():
    """Pressing an inline keyboard button calls ctx.answer_question."""
    adapter = TelegramAdapter(bot_token="t", allowed_users={"alex": 111})
    ctx = _make_context()
    ctx.answer_question = AsyncMock()
    adapter._context = ctx

    # Store pending question state
    adapter._pending_questions = {}
    adapter._pending_questions["main/dm-alex:q1"] = {
        "questions": [
            {
                "question": "Which DB?",
                "options": [
                    {"label": "Postgres", "description": "SQL"},
                    {"label": "SQLite", "description": "Embedded"},
                ],
                "multiSelect": False,
            }
        ],
        "answers": {},
        "session": "dm-alex",
    }

    # Simulate button press
    query = AsyncMock()
    query.data = "ask:main/dm-alex:q1:0:0"  # question_id:q_index:option_index
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query

    await adapter._handle_question_callback(update, MagicMock())

    query.answer.assert_awaited()
    ctx.answer_question.assert_awaited_once()
    call_args = ctx.answer_question.call_args[0]
    assert call_args[0] == "dm-alex"
    assert call_args[1] == "main/dm-alex:q1"
    answers = call_args[2]
    assert answers["Which DB?"] == "Postgres"
