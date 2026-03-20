import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fera.adapters.mattermost import MattermostAdapter
from fera.adapters.base import AdapterContext, AdapterStatus
from fera.adapters.bus import EventBus
from fera.adapters.commands import CommandResult


def _make_adapter(**kwargs):
    defaults = dict(
        url="https://mm.example.com",
        bot_token="fake-token",
        allowed_users={"alex": "alex"},
    )
    defaults.update(kwargs)
    return MattermostAdapter(**defaults)


def _make_context():
    bus = EventBus()
    runner = MagicMock()
    async def _empty(*a, **kw):
        return
        yield
    runner.run_turn = _empty
    runner.active_session.return_value = False
    sm = MagicMock()
    sm.sessions_for_agent.return_value = []
    return AdapterContext(bus=bus, runner=runner, sessions=sm)


def test_adapter_name():
    assert _make_adapter().name == "mattermost"


def test_adapter_status_before_start():
    adapter = _make_adapter()
    assert adapter.status().connected is False


def test_derive_session_dm():
    adapter = _make_adapter(allowed_users={"alex": "alex"})
    adapter._user_id_to_canonical["uid1"] = "alex"
    post = {"root_id": "", "user_id": "uid1", "channel_id": "ch1"}
    session = adapter._derive_session(post, "D", "", "@alex")
    assert session == "dm-alex"


def test_derive_session_dm_unknown_user_fallback():
    """DM from user not in canonical map falls back to sender_name."""
    adapter = _make_adapter()
    post = {"root_id": "", "user_id": "unknown-uid", "channel_id": "ch1"}
    session = adapter._derive_session(post, "D", "", "@alice")
    assert session == "dm-alice"


def test_derive_session_dm_thread():
    """DM thread gets its own session with root_id suffix."""
    adapter = _make_adapter(allowed_users={"alex": "alex"})
    adapter._user_id_to_canonical["uid1"] = "alex"
    post = {"root_id": "root456", "user_id": "uid1", "channel_id": "ch1"}
    session = adapter._derive_session(post, "D", "", "@alex")
    assert session == "dm-alex-t-root456"


def test_derive_session_dm_thread_unknown_user():
    """DM thread from unknown user also gets thread suffix."""
    adapter = _make_adapter()
    post = {"root_id": "root789", "user_id": "unknown-uid", "channel_id": "ch1"}
    session = adapter._derive_session(post, "D", "", "@alice")
    assert session == "dm-alice-t-root789"


def test_derive_session_channel():
    adapter = _make_adapter()
    post = {"root_id": "", "user_id": "uid1", "channel_id": "ch1"}
    session = adapter._derive_session(post, "O", "Town Square", "@alex")
    assert session == "mm-town-square"


def test_derive_session_channel_slugifies():
    adapter = _make_adapter()
    post = {"root_id": "", "user_id": "uid1", "channel_id": "ch1"}
    session = adapter._derive_session(post, "O", "My Work & Projects!", "@alex")
    assert session == "mm-my-work-projects"


def test_derive_session_thread():
    adapter = _make_adapter()
    post = {"root_id": "root123", "user_id": "uid1", "channel_id": "ch1"}
    session = adapter._derive_session(post, "O", "Town Square", "@alex")
    assert session == "mm-town-square-t-root123"


def test_derive_session_private_channel():
    adapter = _make_adapter()
    post = {"root_id": "", "user_id": "uid1", "channel_id": "ch1"}
    session = adapter._derive_session(post, "P", "Secret", "@alex")
    assert session == "mm-secret"


def test_derive_session_empty_title_fallback():
    adapter = _make_adapter()
    post = {"root_id": "", "user_id": "uid1", "channel_id": "ch1"}
    session = adapter._derive_session(post, "O", "!!!", "@alex")
    assert session == "mm-channel"


def test_session_key_top_level():
    adapter = _make_adapter()
    assert adapter._session_key("ch1", "") == "ch1"


def test_session_key_thread():
    adapter = _make_adapter()
    assert adapter._session_key("ch1", "root123") == "ch1:root123"


def test_trusted_defaults_to_false():
    assert _make_adapter().trusted is False


def test_trusted_flag():
    assert _make_adapter(trusted=True).trusted is True


import asyncio
import json as _json


def _make_driver_mock(bot_user_id="bot123", allowed_user_id="uid1", username="alex"):
    """Return a mock Driver with enough wired to pass startup."""
    driver = MagicMock()
    driver.login = MagicMock(return_value=None)
    driver.users.get_user = MagicMock(return_value={"id": bot_user_id})
    driver.users.get_user_by_username = MagicMock(return_value={"id": allowed_user_id})
    return driver


def _make_ws_mock():
    """Return a Websocket mock whose connect() returns immediately."""
    ws = MagicMock()
    ws.connect = AsyncMock(return_value=None)
    return ws


def _make_ws_event(
    message="hello",
    user_id="uid1",
    channel_id="ch1",
    channel_type="O",
    channel_display_name="Town Square",
    root_id="",
    sender_name="@alex",
    bot_user_id="bot123",
):
    post = {
        "id": "post1",
        "message": message,
        "user_id": user_id,
        "channel_id": channel_id,
        "root_id": root_id,
    }
    return _json.dumps({
        "event": "posted",
        "data": {
            "channel_display_name": channel_display_name,
            "channel_type": channel_type,
            "post": _json.dumps(post),
            "sender_name": sender_name,
        },
    })


@pytest.mark.asyncio
async def test_on_ws_message_ignores_non_posted_events():
    adapter = _make_adapter()
    adapter._bot_user_id = "bot123"
    adapter._allowed_user_ids = {"uid1"}
    adapter._context = _make_context()

    raw = _json.dumps({"event": "typing", "data": {}})
    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._on_ws_message(raw)
    mock_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_ws_message_ignores_own_posts():
    adapter = _make_adapter()
    adapter._bot_user_id = "bot123"
    adapter._allowed_user_ids = {"uid1"}
    adapter._context = _make_context()

    raw = _make_ws_event(user_id="bot123")
    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._on_ws_message(raw)
    mock_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_ws_message_rejects_non_allowlisted_user_in_dm():
    """DMs from users not in allowed_user_ids are rejected."""
    adapter = _make_adapter()
    adapter._bot_user_id = "bot123"
    adapter._allowed_user_ids = {"uid1"}
    adapter._context = _make_context()

    raw = _make_ws_event(user_id="stranger99", channel_type="D")
    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._on_ws_message(raw)
    mock_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_ws_message_accepts_non_allowlisted_user_in_group_chat():
    """Messages from users not in allowed_user_ids are accepted in group channels."""
    adapter = _make_adapter()
    adapter._bot_user_id = "bot123"
    adapter._allowed_user_ids = {"uid1"}
    adapter._context = _make_context()

    raw = _make_ws_event(
        message="hello from forge",
        user_id="forge_bot_id",
        channel_type="O",
        channel_display_name="Agents",
        sender_name="@forge",
    )
    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._on_ws_message(raw)
        for _ in range(3):
            await asyncio.sleep(0)
    mock_send.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_ws_message_dispatches_authorized():
    adapter = _make_adapter()
    adapter._bot_user_id = "bot123"
    adapter._allowed_user_ids = {"uid1"}
    adapter._context = _make_context()

    raw = _make_ws_event(message="hello", user_id="uid1")
    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._on_ws_message(raw)
        # drain pending tasks
        for _ in range(3):
            await asyncio.sleep(0)
    mock_send.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_ws_message_passes_correct_session():
    adapter = _make_adapter()
    adapter._bot_user_id = "bot123"
    adapter._allowed_user_ids = {"uid1"}
    adapter._context = _make_context()

    raw = _make_ws_event(
        message="hello", user_id="uid1",
        channel_display_name="Town Square", channel_type="O",
    )
    calls = []
    async def capture(*args):
        calls.append(args)
    with patch.object(adapter, "_send_to_agent", new=capture):
        await adapter._on_ws_message(raw)
        # drain pending tasks
        for _ in range(3):
            await asyncio.sleep(0)
    assert any("mm-town-square" in str(c) for c in calls)


def _make_websocket_cm(messages=()):
    """Return a mock async context manager for websockets.connect.

    Yields each item from messages via __anext__, then StopAsyncIteration.
    Captures anything sent via ws.send() in ws.sent_messages.
    """
    sent = []

    class FakeWs:
        sent_messages = sent

        async def send(self, data):
            sent.append(data)

        def __aiter__(self):
            return self

        async def __anext__(self):
            if messages:
                return messages[0]  # simplistic single-shot
            raise StopAsyncIteration

    ws = FakeWs()

    class FakeCM:
        async def __aenter__(self):
            return ws

        async def __aexit__(self, *_):
            return False

    return FakeCM(), ws


@pytest.mark.asyncio
async def test_run_websocket_connects_with_websockets_and_sends_auth():
    """_run_websocket uses websockets.connect (not mattermostdriver internals)
    and sends an authentication_challenge on connect.
    """
    adapter = _make_adapter()
    adapter._driver = MagicMock()
    adapter._driver.options = {
        "url": "mm.example.com", "scheme": "https",
        "port": 443, "basepath": "/api/v4", "verify": True,
    }
    adapter._driver.client.token = "test-token"

    cm, ws = _make_websocket_cm()
    connected = asyncio.Event()

    def fake_websockets_connect(url, ssl=None, **kw):
        connected.set()
        return cm

    with patch("fera.adapters.mattermost.websockets.connect", side_effect=fake_websockets_connect):
        task = asyncio.create_task(adapter._run_websocket())
        await asyncio.wait_for(connected.wait(), timeout=1.0)
        await asyncio.sleep(0)  # let send() run
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert any("authentication_challenge" in m for m in ws.sent_messages)
    assert any("test-token" in m for m in ws.sent_messages)


@pytest.mark.asyncio
async def test_start_resolves_allowed_users():
    adapter = _make_adapter(allowed_users={"alex": "alex"})
    driver = _make_driver_mock(bot_user_id="bot1", allowed_user_id="uid1", username="alex")

    cm, _ = _make_websocket_cm()
    with patch("fera.adapters.mattermost.Driver", return_value=driver), \
         patch("fera.adapters.mattermost.websockets.connect", return_value=cm):
        await adapter.start(_make_context())
        await asyncio.sleep(0)

    assert "uid1" in adapter._allowed_user_ids
    assert adapter._bot_user_id == "bot1"
    assert adapter._connected is True
    assert adapter._user_id_to_canonical["uid1"] == "alex"

    await adapter.stop()


def _make_streaming_adapter():
    """Adapter with a mocked driver, ready for streaming tests."""
    adapter = _make_adapter()
    adapter._bot_user_id = "bot123"
    adapter._allowed_user_ids = {"uid1"}
    adapter._context = _make_context()
    driver = MagicMock()
    driver.posts.create_post = MagicMock(return_value={"id": "post42"})
    driver.posts.update_post = MagicMock(return_value={"id": "post42"})
    adapter._driver = driver
    return adapter, driver


@pytest.mark.asyncio
async def test_stream_text_first_chunk_creates_post():
    adapter, driver = _make_streaming_adapter()
    await adapter._stream_text("ch1", "ch1", "", "Hello")
    driver.posts.create_post.assert_called_once()
    opts = driver.posts.create_post.call_args[1]["options"]
    assert opts["message"] == "Hello\n\u2026"
    assert adapter._draft_posts["ch1"] == "post42"


@pytest.mark.asyncio
async def test_stream_text_second_chunk_updates_post():
    adapter, driver = _make_streaming_adapter()
    await adapter._stream_text("ch1", "ch1", "", "Hello")
    adapter._last_edit["ch1"] = 0.0  # force throttle to allow edit
    await adapter._stream_text("ch1", "ch1", "", "Hello\n\nworld")
    driver.posts.update_post.assert_called_once()
    opts = driver.posts.update_post.call_args[1]["options"]
    assert opts["message"] == "Hello\n\nworld\n\u2026"


@pytest.mark.asyncio
async def test_stream_text_throttles_edits():
    adapter, driver = _make_streaming_adapter()
    await adapter._stream_text("ch1", "ch1", "", "Hello")
    # last_edit is fresh (not 0), so next chunk should not trigger update
    await adapter._stream_text("ch1", "ch1", "", "Hello world")
    # create_post called once, update_post not called yet (throttled)
    assert driver.posts.create_post.call_count == 1
    assert driver.posts.update_post.call_count == 0


@pytest.mark.asyncio
async def test_flush_draft_sends_final_text():
    adapter, driver = _make_streaming_adapter()
    adapter._draft_posts["ch1"] = "post42"
    adapter._draft_text["ch1"] = "Complete response"
    await adapter._flush_draft("ch1")
    driver.posts.update_post.assert_called_once_with(
        "post42", options={"id": "post42", "message": "Complete response"}
    )


@pytest.mark.asyncio
async def test_flush_draft_clears_state():
    adapter, driver = _make_streaming_adapter()
    adapter._draft_posts["ch1"] = "post42"
    adapter._draft_text["ch1"] = "text"
    adapter._last_edit["ch1"] = 1.0
    await adapter._flush_draft("ch1")
    assert "ch1" not in adapter._draft_posts
    assert "ch1" not in adapter._draft_text
    assert "ch1" not in adapter._last_edit


@pytest.mark.asyncio
async def test_flush_draft_noop_when_no_draft():
    adapter, driver = _make_streaming_adapter()
    # Should not raise
    await adapter._flush_draft("ch1")
    driver.posts.update_post.assert_not_called()


@pytest.mark.asyncio
async def test_stream_text_in_thread_uses_root_id():
    adapter, driver = _make_streaming_adapter()
    # Thread: state_key = "ch1:root1", channel_id = "ch1", root_id = "root1"
    await adapter._stream_text("ch1:root1", "ch1", "root1", "Thread reply")
    call_kwargs = driver.posts.create_post.call_args[1]["options"]
    assert call_kwargs["root_id"] == "root1"
    assert call_kwargs["channel_id"] == "ch1"


@pytest.mark.asyncio
async def test_start_typing_creates_task():
    adapter, driver = _make_streaming_adapter()
    driver.client.make_request = MagicMock(return_value=None)
    await adapter._start_typing("ch1")
    assert "ch1" in adapter._typing_tasks
    task = adapter._typing_tasks["ch1"]
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_stop_typing_cancels_task():
    adapter, driver = _make_streaming_adapter()
    adapter._typing_tasks["ch1"] = asyncio.create_task(asyncio.sleep(999))
    adapter._stop_typing("ch1")
    assert "ch1" not in adapter._typing_tasks


@pytest.mark.asyncio
async def test_on_event_agent_text_calls_stream():
    adapter, driver = _make_streaming_adapter()
    adapter._typing_tasks["ch1"] = asyncio.create_task(asyncio.sleep(999))

    event = {
        "event": "agent.text",
        "turn_source": "mattermost",
        "data": {"text": "Hello!"},
    }
    with patch.object(adapter, "_stream_text", new_callable=AsyncMock) as mock_stream:
        await adapter._on_event(event, "ch1", "")
    mock_stream.assert_awaited_once()
    assert "ch1" not in adapter._typing_tasks  # typing stopped


@pytest.mark.asyncio
async def test_on_event_agent_done_flushes():
    adapter, driver = _make_streaming_adapter()
    adapter._draft_posts["ch1"] = "post42"
    adapter._draft_text["ch1"] = "Final text"

    event = {"event": "agent.done", "turn_source": "mattermost", "data": {}}
    await adapter._on_event(event, "ch1", "")
    driver.posts.update_post.assert_called_once()


@pytest.mark.asyncio
async def test_on_event_agent_error_posts_error():
    adapter, driver = _make_streaming_adapter()
    event = {
        "event": "agent.error",
        "turn_source": "mattermost",
        "data": {"error": "something went wrong"},
    }
    with patch.object(adapter, "_post_reply", new_callable=AsyncMock) as mock_reply:
        await adapter._on_event(event, "ch1", "")
    assert any("something went wrong" in str(c) for c in mock_reply.call_args_list)


@pytest.mark.asyncio
async def test_on_event_ignores_events_targeted_at_other_adapter():
    """Events with target_adapter set to a different adapter are ignored."""
    adapter, driver = _make_streaming_adapter()
    event = {
        "event": "agent.text",
        "turn_source": "heartbeat",
        "target_adapter": "telegram",
        "data": {"text": "hello"},
    }
    await adapter._on_event(event, "ch1", "")
    driver.posts.create_post.assert_not_called()


@pytest.mark.asyncio
async def test_on_event_processes_events_targeted_at_self():
    """Events with target_adapter matching this adapter are processed."""
    adapter, driver = _make_streaming_adapter()
    adapter._typing_tasks["ch1"] = asyncio.create_task(asyncio.sleep(999))
    event = {
        "event": "agent.text",
        "turn_source": "heartbeat",
        "target_adapter": "mattermost",
        "data": {"text": "Your morning briefing"},
    }
    await adapter._on_event(event, "ch1", "")
    # Verify it wasn't filtered — _stream_text was called, creating draft text
    assert "ch1" in adapter._draft_text


@pytest.mark.asyncio
async def test_on_event_ignores_other_turn_sources():
    adapter, driver = _make_streaming_adapter()
    event = {
        "event": "agent.text",
        "turn_source": "telegram",
        "data": {"text": "from telegram"},
    }
    with patch.object(adapter, "_stream_text", new_callable=AsyncMock) as mock_stream:
        await adapter._on_event(event, "ch1", "")
    mock_stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_event_passes_through_untagged_turn():
    adapter, driver = _make_streaming_adapter()
    event = {
        "event": "agent.text",
        # no turn_source
        "data": {"text": "untagged"},
    }
    with patch.object(adapter, "_stream_text", new_callable=AsyncMock) as mock_stream:
        await adapter._on_event(event, "ch1", "")
    mock_stream.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["cron", "heartbeat"])
async def test_on_event_passes_through_proactive_sources(source):
    """Cron and heartbeat events are delivered even though they're not Mattermost-sourced."""
    adapter, driver = _make_streaming_adapter()
    event = {
        "event": "agent.text",
        "turn_source": source,
        "data": {"text": "proactive message"},
    }
    with patch.object(adapter, "_stream_text", new_callable=AsyncMock) as mock_stream:
        await adapter._on_event(event, "ch1", "")
    mock_stream.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_ws_message_always_uses_derived_session():
    """_on_ws_message always uses the derived session (no override dict)."""
    adapter = _make_adapter()
    adapter._bot_user_id = "bot123"
    adapter._allowed_user_ids = {"uid1"}
    adapter._context = _make_context()

    raw = _make_ws_event(message="hello", user_id="uid1", channel_id="ch1", channel_type="O", channel_display_name="Town Square")
    calls = []
    async def capture(*args):
        calls.append(args)
    with patch.object(adapter, "_send_to_agent", new=capture):
        await adapter._on_ws_message(raw)
        for _ in range(3):
            await asyncio.sleep(0)
    assert any("mm-town-square" in str(c) for c in calls)


@pytest.mark.asyncio
async def test_ensure_subscribed_persists_session_channel_mapping(tmp_path):
    """_ensure_subscribed saves the session→channel mapping to mattermost_sessions.json."""
    import json

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    adapter = _make_adapter(data_dir=data_dir)
    adapter._context = _make_context()

    adapter._ensure_subscribed("ch1", "ch1", "", "mm-town-square")

    saved = json.loads((data_dir / "mattermost_sessions.json").read_text())
    assert saved == {"mm-town-square": {"channel_id": "ch1", "root_id": ""}}


@pytest.mark.asyncio
async def test_ensure_subscribed_strips_root_id_from_non_thread_session(tmp_path):
    """_ensure_subscribed forces root_id to '' for non-thread sessions."""
    import json

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    adapter = _make_adapter(data_dir=data_dir)
    adapter._context = _make_context()

    # Simulate a buggy call that passes a root_id for a parent session
    adapter._ensure_subscribed("ch1:root123", "ch1", "root123", "dm-alex")

    saved = json.loads((data_dir / "mattermost_sessions.json").read_text())
    assert saved["dm-alex"]["root_id"] == ""


@pytest.mark.asyncio
async def test_ensure_subscribed_keeps_root_id_for_thread_session(tmp_path):
    """_ensure_subscribed preserves root_id for thread sessions."""
    import json

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    adapter = _make_adapter(data_dir=data_dir)
    adapter._context = _make_context()

    adapter._ensure_subscribed("ch1:root123", "ch1", "root123", "dm-alex-t-root123")

    saved = json.loads((data_dir / "mattermost_sessions.json").read_text())
    assert saved["dm-alex-t-root123"]["root_id"] == "root123"


def test_load_session_channels_sanitizes_contaminated_parent(tmp_path):
    """Loading a contaminated mattermost_sessions.json strips root_id from parent sessions."""
    import json

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "mattermost_sessions.json").write_text(json.dumps({
        "dm-alex": {"channel_id": "ch1", "root_id": "bad_root_id"},
        "dm-alex-t-root456": {"channel_id": "ch1", "root_id": "root456"},
        "mm-general": {"channel_id": "ch2", "root_id": ""},
    }))

    adapter = _make_adapter(data_dir=data_dir)
    adapter._context = _make_context()
    adapter._load_session_channels()

    # Parent session should be sanitized
    assert adapter._session_channels["dm-alex"]["root_id"] == ""
    # Thread session should be untouched
    assert adapter._session_channels["dm-alex-t-root456"]["root_id"] == "root456"
    # Clean session should be untouched
    assert adapter._session_channels["mm-general"]["root_id"] == ""

    # File should be re-saved with the fix
    saved = json.loads((data_dir / "mattermost_sessions.json").read_text())
    assert saved["dm-alex"]["root_id"] == ""


def test_load_session_channels_no_save_when_clean(tmp_path):
    """Loading a clean mattermost_sessions.json does not rewrite the file."""
    import json

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    original = json.dumps({
        "dm-alex": {"channel_id": "ch1", "root_id": ""},
        "mm-general": {"channel_id": "ch2", "root_id": ""},
    })
    path = data_dir / "mattermost_sessions.json"
    path.write_text(original)
    mtime_before = path.stat().st_mtime

    adapter = _make_adapter(data_dir=data_dir)
    adapter._context = _make_context()

    import time
    time.sleep(0.01)  # ensure mtime would differ if file were rewritten
    adapter._load_session_channels()

    mtime_after = path.stat().st_mtime
    assert mtime_before == mtime_after


def _make_ws_event_with_files(
    message="",
    file_ids=None,
    user_id="uid1",
    channel_id="ch1",
    channel_type="O",
    channel_display_name="Town Square",
    root_id="",
    sender_name="@alex",
):
    post = {
        "id": "post1",
        "message": message,
        "user_id": user_id,
        "channel_id": channel_id,
        "root_id": root_id,
    }
    if file_ids:
        post["file_ids"] = file_ids
    return _json.dumps({
        "event": "posted",
        "data": {
            "channel_display_name": channel_display_name,
            "channel_type": channel_type,
            "post": _json.dumps(post),
            "sender_name": sender_name,
        },
    })


@pytest.mark.asyncio
async def test_resubscribe_loaded_sessions_restores_subscriptions_from_file(tmp_path):
    """Sessions persisted to mattermost_sessions.json are re-subscribed on the bus after restart."""
    import json

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "mattermost_sessions.json").write_text(
        json.dumps({"mm-town-square": {"channel_id": "ch1", "root_id": ""}})
    )

    adapter = _make_adapter(data_dir=data_dir)
    ctx = _make_context()
    adapter._context = ctx
    adapter._load_session_channels()
    adapter._resubscribe_loaded_sessions()

    assert ctx._bus._subscribers.get("main/mm-town-square")


# --- File attachment handling ---


def test_constructor_accepts_workspace_dir(tmp_path):
    adapter = _make_adapter(workspace_dir=tmp_path)
    assert adapter._workspace_dir == tmp_path


@pytest.mark.asyncio
async def test_on_ws_message_with_file_ids_and_no_text_dispatches_to_agent():
    """A post with file_ids but no message text still dispatches to the agent."""
    adapter = _make_adapter()
    adapter._bot_user_id = "bot123"
    adapter._allowed_user_ids = {"uid1"}
    adapter._context = _make_context()

    raw = _make_ws_event_with_files(message="", file_ids=["file1"])
    with patch.object(adapter, "_handle_file_attachments", new_callable=AsyncMock) as mock_files:
        await adapter._on_ws_message(raw)
        for _ in range(3):
            await asyncio.sleep(0)
    mock_files.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_ws_message_with_no_text_and_no_file_ids_is_ignored():
    """A post with no message and no file_ids is silently dropped."""
    adapter = _make_adapter()
    adapter._bot_user_id = "bot123"
    adapter._allowed_user_ids = {"uid1"}
    adapter._context = _make_context()

    raw = _make_ws_event_with_files(message="", file_ids=[])
    with patch.object(adapter, "_handle_file_attachments", new_callable=AsyncMock) as mock_files, \
         patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._on_ws_message(raw)
        for _ in range(3):
            await asyncio.sleep(0)
    mock_files.assert_not_awaited()
    mock_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_file_attachments_saves_files_to_dated_inbox(tmp_path):
    """Files are downloaded and saved under workspace/inbox/YYYY-MM-DD/mattermost/."""
    adapter = _make_adapter(workspace_dir=tmp_path)
    adapter._bot_user_id = "bot123"
    adapter._allowed_user_ids = {"uid1"}
    adapter._context = _make_context()

    driver = MagicMock()
    driver.files.get_file_info = MagicMock(return_value={"name": "report.pdf"})
    driver.files.get_file = MagicMock(return_value=b"PDF content")
    adapter._driver = driver

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock):
        await adapter._handle_file_attachments("ch1", "", "ch1", "mm-town-square", "", ["file1"])

    saved = list(tmp_path.rglob("report.pdf"))
    assert len(saved) == 1
    assert "inbox" in str(saved[0])
    assert "mattermost" in str(saved[0])
    assert saved[0].read_bytes() == b"PDF content"


@pytest.mark.asyncio
async def test_handle_file_attachments_notifies_agent_with_saved_path(tmp_path):
    """Agent receives a [File saved to ...] notification with the relative path."""
    adapter = _make_adapter(workspace_dir=tmp_path)
    adapter._bot_user_id = "bot123"
    adapter._allowed_user_ids = {"uid1"}
    adapter._context = _make_context()

    driver = MagicMock()
    driver.files.get_file_info = MagicMock(return_value={"name": "notes.txt"})
    driver.files.get_file = MagicMock(return_value=b"hello")
    adapter._driver = driver

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_file_attachments("ch1", "", "ch1", "mm-town-square", "", ["file1"])

    mock_send.assert_awaited_once()
    text = mock_send.call_args[0][4]
    assert "notes.txt" in text
    assert "inbox" in text


@pytest.mark.asyncio
async def test_handle_file_attachments_strips_path_traversal_from_filename(tmp_path):
    """Filenames with path traversal components are reduced to the basename only."""
    adapter = _make_adapter(workspace_dir=tmp_path)
    adapter._bot_user_id = "bot123"
    adapter._allowed_user_ids = {"uid1"}
    adapter._context = _make_context()

    driver = MagicMock()
    driver.files.get_file_info = MagicMock(return_value={"name": "../../etc/passwd"})
    driver.files.get_file = MagicMock(return_value=b"root:x:0:0")
    adapter._driver = driver

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_file_attachments("ch1", "", "ch1", "mm-town-square", "", ["file1"])

    mock_send.assert_awaited_once()
    text = mock_send.call_args[0][4]
    assert ".." not in text
    assert "passwd" in text
    # File must be inside workspace, not escaped
    saved = list(tmp_path.rglob("passwd"))
    assert len(saved) == 1
    assert tmp_path in saved[0].parents


@pytest.mark.asyncio
async def test_handle_file_attachments_handles_filename_collision(tmp_path):
    """If a file with the same name already exists, a numbered suffix is added."""
    adapter = _make_adapter(workspace_dir=tmp_path)
    adapter._context = _make_context()

    driver = MagicMock()
    driver.files.get_file_info = MagicMock(return_value={"name": "doc.pdf"})
    driver.files.get_file = MagicMock(return_value=b"data")
    adapter._driver = driver

    # Pre-create the file to trigger collision
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    # We can't know the exact date subdir, so pre-create the collision via two calls
    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock):
        await adapter._handle_file_attachments("ch1", "", "ch1", "mm-town-square", "", ["file1"])
        await adapter._handle_file_attachments("ch1", "", "ch1", "mm-town-square", "", ["file2"])

    saved = list(tmp_path.rglob("doc*.pdf"))
    assert len(saved) == 2
    names = {f.name for f in saved}
    assert "doc.pdf" in names
    assert any(n.startswith("doc-") for n in names)


@pytest.mark.asyncio
async def test_handle_file_attachments_includes_caption_in_notification(tmp_path):
    """When a file post also has a text caption, it's appended to the agent notification."""
    adapter = _make_adapter(workspace_dir=tmp_path)
    adapter._context = _make_context()

    driver = MagicMock()
    driver.files.get_file_info = MagicMock(return_value={"name": "chart.png"})
    driver.files.get_file = MagicMock(return_value=b"png")
    adapter._driver = driver

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_file_attachments("ch1", "", "ch1", "mm-town-square", "see attached", ["file1"])

    text = mock_send.call_args[0][4]
    assert "chart.png" in text
    assert "see attached" in text


@pytest.mark.asyncio
async def test_handle_file_attachments_wraps_notification_for_untrusted(tmp_path):
    """Untrusted adapter wraps the file notification in an <untrusted> tag."""
    adapter = _make_adapter(workspace_dir=tmp_path, trusted=False)
    adapter._context = _make_context()

    driver = MagicMock()
    driver.files.get_file_info = MagicMock(return_value={"name": "file.txt"})
    driver.files.get_file = MagicMock(return_value=b"x")
    adapter._driver = driver

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_file_attachments("ch1", "", "ch1", "mm-town-square", "", ["file1"])

    text = mock_send.call_args[0][4]
    assert "<untrusted" in text
    assert 'source="mattermost"' in text


@pytest.mark.asyncio
async def test_handle_file_attachments_trusted_does_not_wrap(tmp_path):
    """Trusted adapter does NOT wrap the file notification."""
    adapter = _make_adapter(workspace_dir=tmp_path, trusted=True)
    adapter._context = _make_context()

    driver = MagicMock()
    driver.files.get_file_info = MagicMock(return_value={"name": "file.txt"})
    driver.files.get_file = MagicMock(return_value=b"x")
    adapter._driver = driver

    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_file_attachments("ch1", "", "ch1", "mm-town-square", "", ["file1"])

    text = mock_send.call_args[0][4]
    assert "<untrusted" not in text


@pytest.mark.asyncio
async def test_handle_file_attachments_no_workspace_does_not_crash():
    """When workspace_dir is None, file handling is skipped without error."""
    adapter = _make_adapter(workspace_dir=None)
    adapter._context = _make_context()
    adapter._driver = MagicMock()

    # Should not raise; agent is not contacted
    with patch.object(adapter, "_send_to_agent", new_callable=AsyncMock) as mock_send:
        await adapter._handle_file_attachments("ch1", "", "ch1", "mm-town-square", "", ["file1"])
    mock_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_clear_command_posts_ack_then_result():
    """/clear posts a 'please wait' ack before the result."""
    adapter = _make_adapter(allowed_users={"alex": "uid1"})
    adapter._allowed_user_ids = {"uid1"}
    ctx = _make_context()
    adapter._context = ctx

    commands_mock = MagicMock()
    commands_mock.match = MagicMock(return_value=True)
    commands_mock.handle = AsyncMock(return_value=CommandResult(response="Done. Your next message starts a fresh context."))
    adapter._commands = commands_mock

    posted = []

    async def fake_post_reply(channel_id, root_id, text):
        posted.append(text)

    adapter._post_reply = fake_post_reply

    post_dict = {"user_id": "uid1", "channel_id": "ch1", "root_id": "", "message": "/clear", "file_ids": []}
    data = {
        "post": _json.dumps(post_dict),
        "channel_type": "D",
        "channel_display_name": "",
        "sender_name": "@alex",
    }
    await adapter._handle_post(data, post_dict)

    assert len(posted) == 2
    assert "please wait" in posted[0].lower()
    assert "fresh context" in posted[1].lower()


@pytest.mark.asyncio
async def test_non_clear_command_posts_single_result():
    """/status posts a single result with no ack."""
    adapter = _make_adapter(allowed_users={"alex": "uid1"})
    adapter._allowed_user_ids = {"uid1"}
    ctx = _make_context()
    adapter._context = ctx

    commands_mock = MagicMock()
    commands_mock.match = MagicMock(return_value=True)
    commands_mock.handle = AsyncMock(return_value=CommandResult(response="Session: dm-alex"))
    adapter._commands = commands_mock

    posted = []

    async def fake_post_reply(channel_id, root_id, text):
        posted.append(text)

    adapter._post_reply = fake_post_reply

    post_dict = {"user_id": "uid1", "channel_id": "ch1", "root_id": "", "message": "/status", "file_ids": []}
    data = {
        "post": _json.dumps(post_dict),
        "channel_type": "D",
        "channel_display_name": "",
        "sender_name": "@alex",
    }
    await adapter._handle_post(data, post_dict)

    assert len(posted) == 1
    assert "dm-alex" in posted[0]


# --- Group chat sender identification ---


@pytest.mark.asyncio
async def test_group_chat_message_includes_sender_in_untrusted_wrap():
    """In untrusted group chats, messages include sender= in the untrusted tag."""
    adapter = _make_adapter(trusted=False)
    adapter._bot_user_id = "bot123"
    adapter._allowed_user_ids = {"uid1"}
    adapter._context = _make_context()

    calls = []
    async def capture(*args):
        calls.append(args)

    post = {"user_id": "uid1", "channel_id": "ch1", "root_id": "", "message": "hello"}
    data = {
        "channel_type": "O",
        "channel_display_name": "Agents",
        "sender_name": "@alex",
    }
    with patch.object(adapter, "_send_to_agent", new=capture):
        await adapter._handle_post(data, post)
        for _ in range(3):
            await asyncio.sleep(0)

    assert len(calls) == 1
    text = calls[0][4]
    assert 'sender="alex"' in text


@pytest.mark.asyncio
async def test_group_chat_message_includes_sender_prefix_trusted():
    """In trusted group chats, messages are prefixed with [sender]."""
    adapter = _make_adapter(trusted=True)
    adapter._bot_user_id = "bot123"
    adapter._allowed_user_ids = {"uid1"}
    adapter._context = _make_context()

    calls = []
    async def capture(*args):
        calls.append(args)

    post = {"user_id": "uid1", "channel_id": "ch1", "root_id": "", "message": "hello"}
    data = {
        "channel_type": "O",
        "channel_display_name": "Agents",
        "sender_name": "@alex",
    }
    with patch.object(adapter, "_send_to_agent", new=capture):
        await adapter._handle_post(data, post)
        for _ in range(3):
            await asyncio.sleep(0)

    assert len(calls) == 1
    text = calls[0][4]
    assert text == "[alex] hello"


@pytest.mark.asyncio
async def test_dm_message_does_not_include_sender():
    """DM messages don't include sender information."""
    adapter = _make_adapter(trusted=False)
    adapter._bot_user_id = "bot123"
    adapter._allowed_user_ids = {"uid1"}
    adapter._user_id_to_canonical["uid1"] = "alex"
    adapter._context = _make_context()

    calls = []
    async def capture(*args):
        calls.append(args)

    post = {"user_id": "uid1", "channel_id": "ch1", "root_id": "", "message": "hello"}
    data = {
        "channel_type": "D",
        "channel_display_name": "",
        "sender_name": "@alex",
    }
    with patch.object(adapter, "_send_to_agent", new=capture):
        await adapter._handle_post(data, post)
        for _ in range(3):
            await asyncio.sleep(0)

    assert len(calls) == 1
    text = calls[0][4]
    assert "sender" not in text


# --- /stop command ---


@pytest.mark.asyncio
async def test_stop_command_posts_result():
    """/stop is handled as a command and posts the result."""
    adapter = _make_adapter(allowed_users={"alex": "uid1"})
    adapter._allowed_user_ids = {"uid1"}
    ctx = _make_context()
    adapter._context = ctx

    commands_mock = MagicMock()
    commands_mock.match = MagicMock(return_value=True)
    commands_mock.handle = AsyncMock(return_value=CommandResult(response="Interrupted."))
    adapter._commands = commands_mock

    posted = []

    async def fake_post_reply(channel_id, root_id, text):
        posted.append(text)

    adapter._post_reply = fake_post_reply

    post_dict = {"user_id": "uid1", "channel_id": "ch1", "root_id": "", "message": "/stop", "file_ids": []}
    data = {
        "post": _json.dumps(post_dict),
        "channel_type": "D",
        "channel_display_name": "",
        "sender_name": "@alex",
    }
    await adapter._handle_post(data, post_dict)

    assert len(posted) == 1
    assert "interrupt" in posted[0].lower()


# --- Tool use event handling ---


@pytest.mark.asyncio
async def test_on_event_tool_use_streams_blockquote():
    """agent.tool_use event creates a blockquote line in the draft post."""
    adapter, driver = _make_streaming_adapter()
    event = {
        "event": "agent.tool_use",
        "turn_source": "mattermost",
        "data": {"id": "t1", "name": "Bash", "input": {"command": "ls /tmp"}},
    }
    await adapter._on_event(event, "ch1", "")
    assert "ch1" in adapter._draft_text
    assert "> \U0001f5a5\ufe0f Bash \u00b7 ls /tmp" in adapter._draft_text["ch1"]


@pytest.mark.asyncio
async def test_on_event_consecutive_tools_no_blank_line():
    """Consecutive tool_use events are separated by newline, not blank line."""
    adapter, driver = _make_streaming_adapter()
    adapter._last_edit["ch1"] = 0.0  # allow edits
    tool1 = {
        "event": "agent.tool_use",
        "turn_source": "mattermost",
        "data": {"id": "t1", "name": "Bash", "input": {"command": "ls"}},
    }
    tool2 = {
        "event": "agent.tool_use",
        "turn_source": "mattermost",
        "data": {"id": "t2", "name": "Read", "input": {"file_path": "/tmp/f.txt"}},
    }
    await adapter._on_event(tool1, "ch1", "")
    await adapter._on_event(tool2, "ch1", "")
    text = adapter._draft_text["ch1"]
    assert text == "> \U0001f5a5\ufe0f Bash \u00b7 ls\n> \U0001f4d6 Read \u00b7 /tmp/f.txt"


@pytest.mark.asyncio
async def test_on_event_text_after_tool_has_blank_line():
    """Text after tool_use is separated by a blank line."""
    adapter, driver = _make_streaming_adapter()
    adapter._last_edit["ch1"] = 0.0
    tool = {
        "event": "agent.tool_use",
        "turn_source": "mattermost",
        "data": {"id": "t1", "name": "Read", "input": {"file_path": "/tmp/f.txt"}},
    }
    text_event = {
        "event": "agent.text",
        "turn_source": "mattermost",
        "data": {"text": "Here's the content."},
    }
    await adapter._on_event(tool, "ch1", "")
    await adapter._on_event(text_event, "ch1", "")
    text = adapter._draft_text["ch1"]
    assert text == "> \U0001f4d6 Read \u00b7 /tmp/f.txt\n\nHere's the content."


@pytest.mark.asyncio
async def test_on_event_tool_after_text_has_blank_line():
    """Tool after text starts a new blockquote section with blank line."""
    adapter, driver = _make_streaming_adapter()
    adapter._last_edit["ch1"] = 0.0
    text_event = {
        "event": "agent.text",
        "turn_source": "mattermost",
        "data": {"text": "Let me check."},
    }
    tool = {
        "event": "agent.tool_use",
        "turn_source": "mattermost",
        "data": {"id": "t1", "name": "Bash", "input": {"command": "grep foo"}},
    }
    await adapter._on_event(text_event, "ch1", "")
    await adapter._on_event(tool, "ch1", "")
    text = adapter._draft_text["ch1"]
    assert text == "Let me check.\n\n> \U0001f5a5\ufe0f Bash \u00b7 grep foo"


@pytest.mark.asyncio
async def test_on_event_full_interleaved_sequence():
    """Full realistic sequence: tools, text, more tools, more text."""
    adapter, driver = _make_streaming_adapter()
    adapter._last_edit["ch1"] = 0.0
    events = [
        {"event": "agent.tool_use", "turn_source": "mattermost",
         "data": {"id": "t1", "name": "Bash", "input": {"command": "grep -r foo"}}},
        {"event": "agent.tool_use", "turn_source": "mattermost",
         "data": {"id": "t2", "name": "Read", "input": {"file_path": "/tmp/config.json"}}},
        {"event": "agent.text", "turn_source": "mattermost",
         "data": {"text": "Found it."}},
        {"event": "agent.tool_use", "turn_source": "mattermost",
         "data": {"id": "t3", "name": "WebSearch", "input": {"query": "fera docs"}}},
        {"event": "agent.text", "turn_source": "mattermost",
         "data": {"text": "Here's more info."}},
    ]
    for e in events:
        await adapter._on_event(e, "ch1", "")
    expected = (
        "> \U0001f5a5\ufe0f Bash \u00b7 grep -r foo\n"
        "> \U0001f4d6 Read \u00b7 /tmp/config.json\n"
        "\n"
        "Found it.\n"
        "\n"
        "> \U0001f50d WebSearch \u00b7 fera docs\n"
        "\n"
        "Here's more info."
    )
    assert adapter._draft_text["ch1"] == expected


@pytest.mark.asyncio
async def test_on_event_tool_use_ignored_for_other_adapter():
    """tool_use events targeted at other adapters are ignored."""
    adapter, driver = _make_streaming_adapter()
    event = {
        "event": "agent.tool_use",
        "turn_source": "heartbeat",
        "target_adapter": "telegram",
        "data": {"id": "t1", "name": "Bash", "input": {"command": "ls"}},
    }
    await adapter._on_event(event, "ch1", "")
    assert "ch1" not in adapter._draft_text


@pytest.mark.asyncio
async def test_flush_draft_clears_tool_state():
    """Flushing the draft also clears the _last_was_tool tracker."""
    adapter, driver = _make_streaming_adapter()
    adapter._draft_posts["ch1"] = "post42"
    adapter._draft_text["ch1"] = "> \U0001f5a5\ufe0f Bash \u00b7 ls"
    adapter._last_was_tool["ch1"] = True
    adapter._last_edit["ch1"] = 1.0
    await adapter._flush_draft("ch1")
    assert "ch1" not in adapter._last_was_tool


# --- User question handling ---


@pytest.mark.asyncio
async def test_on_event_user_question_posts_numbered_list():
    """agent.user_question event posts a numbered list of options."""
    adapter = MattermostAdapter(
        url="https://mm.example.com", bot_token="tok",
        allowed_users={"alex": "alex"},
    )
    adapter._driver = MagicMock()
    adapter._driver.posts.create_post = MagicMock(return_value={"id": "post1"})

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
    await adapter._on_event(event, channel_id="ch1", root_id="")

    adapter._driver.posts.create_post.assert_called_once()
    post_opts = adapter._driver.posts.create_post.call_args[1]["options"]
    msg = post_opts["message"]
    assert "Which database?" in msg
    assert "1." in msg or "1)" in msg
    assert "Postgres" in msg
    assert "SQLite" in msg


@pytest.mark.asyncio
async def test_handle_question_answer_by_number():
    """User replies with a number to answer a pending question."""
    adapter = MattermostAdapter(
        url="https://mm.example.com", bot_token="tok",
        allowed_users={"alex": "alex"},
    )
    adapter._driver = MagicMock()
    adapter._driver.posts.create_post = MagicMock(return_value={"id": "post1"})
    ctx = MagicMock()
    ctx.answer_question = AsyncMock()
    adapter._context = ctx

    adapter._pending_questions = {}
    adapter._pending_questions["main/dm-alex:q1"] = {
        "questions": [
            {
                "question": "Which database?",
                "options": [
                    {"label": "Postgres", "description": "SQL"},
                    {"label": "SQLite", "description": "Embedded"},
                ],
                "multiSelect": False,
            }
        ],
        "answers": {},
        "session": "dm-alex",
        "channel_id": "ch1",
        "root_id": "",
    }

    # Simulate user replying with "1" (selects Postgres)
    data = {"channel_type": "D", "sender_name": "alex"}
    post = {
        "message": "1",
        "channel_id": "ch1",
        "root_id": "",
        "user_id": "u1",
    }
    await adapter._handle_post(data, post)

    ctx.answer_question.assert_awaited_once()
    call_args = ctx.answer_question.call_args[0]
    assert call_args[0] == "dm-alex"
    assert call_args[1] == "main/dm-alex:q1"
    answers = call_args[2]
    assert answers["Which database?"] == "Postgres"


@pytest.mark.asyncio
async def test_pending_question_consumes_invalid_message():
    """Non-matching message while question pending posts hint, doesn't reach agent."""
    adapter = MattermostAdapter(
        url="https://mm.example.com", bot_token="tok",
        allowed_users={"alex": "alex"},
    )
    adapter._driver = MagicMock()
    adapter._driver.posts.create_post = MagicMock(return_value={"id": "p1"})
    ctx = MagicMock()
    ctx.answer_question = AsyncMock()
    ctx.send_message = AsyncMock()
    adapter._context = ctx

    adapter._pending_questions["main/dm-alex:q1"] = {
        "questions": [
            {
                "question": "Which database?",
                "options": [
                    {"label": "Postgres", "description": "SQL"},
                    {"label": "SQLite", "description": "Embedded"},
                ],
                "multiSelect": False,
            }
        ],
        "answers": {},
        "session": "dm-alex",
        "channel_id": "ch1",
        "root_id": "",
    }

    data = {"channel_type": "D", "sender_name": "alex"}
    post = {"message": "yes please", "channel_id": "ch1", "root_id": "", "user_id": "u1"}
    await adapter._handle_post(data, post)

    # Should NOT have sent message to agent
    ctx.send_message.assert_not_awaited()
    # Should have posted a hint
    call_args = adapter._driver.posts.create_post.call_args
    posted_msg = call_args[1]["options"]["message"]
    assert "1\u20132" in posted_msg


@pytest.mark.asyncio
async def test_pending_question_consumes_out_of_range():
    """Out-of-range number while question pending posts hint."""
    adapter = MattermostAdapter(
        url="https://mm.example.com", bot_token="tok",
        allowed_users={"alex": "alex"},
    )
    adapter._driver = MagicMock()
    adapter._driver.posts.create_post = MagicMock(return_value={"id": "p1"})
    ctx = MagicMock()
    ctx.answer_question = AsyncMock()
    ctx.send_message = AsyncMock()
    adapter._context = ctx

    adapter._pending_questions["main/dm-alex:q1"] = {
        "questions": [
            {
                "question": "Which database?",
                "options": [
                    {"label": "Postgres", "description": "SQL"},
                    {"label": "SQLite", "description": "Embedded"},
                ],
                "multiSelect": False,
            }
        ],
        "answers": {},
        "session": "dm-alex",
        "channel_id": "ch1",
        "root_id": "",
    }

    data = {"channel_type": "D", "sender_name": "alex"}
    post = {"message": "1407", "channel_id": "ch1", "root_id": "", "user_id": "u1"}
    await adapter._handle_post(data, post)

    ctx.send_message.assert_not_awaited()
    call_args = adapter._driver.posts.create_post.call_args
    posted_msg = call_args[1]["options"]["message"]
    assert "1\u20132" in posted_msg


@pytest.mark.asyncio
async def test_pending_question_multiselect_answer():
    """Multi-select answer like '1,3' records both labels."""
    adapter = MattermostAdapter(
        url="https://mm.example.com", bot_token="tok",
        allowed_users={"alex": "alex"},
    )
    adapter._driver = MagicMock()
    adapter._driver.posts.create_post = MagicMock(return_value={"id": "p1"})
    ctx = MagicMock()
    ctx.answer_question = AsyncMock()
    adapter._context = ctx

    adapter._pending_questions["main/dm-alex:q1"] = {
        "questions": [
            {
                "question": "Which features?",
                "options": [
                    {"label": "Auth", "description": ""},
                    {"label": "Logging", "description": ""},
                    {"label": "Caching", "description": ""},
                ],
                "multiSelect": True,
            }
        ],
        "answers": {},
        "session": "dm-alex",
        "channel_id": "ch1",
        "root_id": "",
    }

    data = {"channel_type": "D", "sender_name": "alex"}
    post = {"message": "1, 3", "channel_id": "ch1", "root_id": "", "user_id": "u1"}
    await adapter._handle_post(data, post)

    ctx.answer_question.assert_awaited_once()
    answers = ctx.answer_question.call_args[0][2]
    assert answers["Which features?"] == "Auth, Caching"


# --- Message splitting ---


from fera.adapters.mattermost import MAX_POST_SIZE, split_message


def test_split_message_short_text_unchanged():
    """Text under the limit returns a single chunk."""
    assert split_message("hello") == ["hello"]


def test_split_message_empty_text():
    assert split_message("") == [""]


def test_split_message_at_paragraph_boundary():
    """Long text is split at paragraph (double-newline) boundaries."""
    para1 = "A" * 100
    para2 = "B" * 100
    text = para1 + "\n\n" + para2
    chunks = split_message(text, max_size=150)
    assert len(chunks) == 2
    assert chunks[0] == para1
    assert chunks[1] == para2


def test_split_message_at_line_boundary():
    """Falls back to single-newline boundary when no paragraph break fits."""
    line1 = "A" * 100
    line2 = "B" * 100
    text = line1 + "\n" + line2
    chunks = split_message(text, max_size=150)
    assert len(chunks) == 2
    assert chunks[0] == line1
    assert chunks[1] == line2


def test_split_message_hard_split():
    """Splits mid-text when no newline boundary exists."""
    text = "A" * 300
    chunks = split_message(text, max_size=100)
    assert len(chunks) == 3
    assert all(len(c) <= 100 for c in chunks)
    assert "".join(chunks) == text


def test_split_message_multiple_paragraphs():
    """Multiple paragraphs are packed into chunks efficiently."""
    paras = [f"Para {i}: " + "x" * 40 for i in range(10)]
    text = "\n\n".join(paras)
    chunks = split_message(text, max_size=200)
    reassembled = "\n\n".join(chunks)
    assert reassembled == text
    assert all(len(c) <= 200 for c in chunks)


@pytest.mark.asyncio
async def test_post_reply_splits_long_message():
    """_post_reply sends multiple posts when text exceeds limit."""
    adapter = _make_adapter()
    driver = MagicMock()
    driver.posts.create_post = MagicMock(return_value={"id": "p1"})
    adapter._driver = driver

    long_text = ("A" * 100 + "\n\n") * 200  # ~20k chars
    await adapter._post_reply("ch1", "root1", long_text)

    call_count = driver.posts.create_post.call_count
    assert call_count > 1, f"Should split into multiple posts, got {call_count}"
    # All posts have the root_id
    for call in driver.posts.create_post.call_args_list:
        opts = call[1]["options"]
        assert opts["root_id"] == "root1"
        assert len(opts["message"]) <= MAX_POST_SIZE


@pytest.mark.asyncio
async def test_flush_draft_splits_long_text():
    """_flush_draft splits long text: updates existing post with first chunk, creates new posts for rest."""
    adapter, driver = _make_streaming_adapter()
    long_text = ("B" * 100 + "\n\n") * 200
    adapter._draft_posts["ch1:root1"] = "post42"
    adapter._draft_text["ch1:root1"] = long_text
    adapter._last_edit["ch1:root1"] = 1.0

    await adapter._flush_draft("ch1:root1")

    # Existing post updated with first chunk
    driver.posts.update_post.assert_called_once()
    update_msg = driver.posts.update_post.call_args[1]["options"]["message"]
    assert len(update_msg) <= MAX_POST_SIZE

    # Remaining chunks posted as new messages
    assert driver.posts.create_post.call_count >= 1


@pytest.mark.asyncio
async def test_stream_text_skips_update_when_over_limit():
    """_stream_text skips the API call when accumulated text exceeds limit."""
    adapter, driver = _make_streaming_adapter()
    # Create initial post
    await adapter._stream_text("ch1", "ch1", "", "Hello")
    assert driver.posts.create_post.call_count == 1

    # Now try streaming text that exceeds the limit
    adapter._last_edit["ch1"] = 0.0  # force throttle to allow edit
    huge_text = "X" * (MAX_POST_SIZE + 1000)
    await adapter._stream_text("ch1", "ch1", "", huge_text)

    # Should not have attempted update_post
    assert driver.posts.update_post.call_count == 0


@pytest.mark.asyncio
async def test_stream_text_appends_ellipsis():
    """Draft post text includes trailing ellipsis during streaming."""
    adapter, driver = _make_streaming_adapter()
    await adapter._stream_text("ch1", "ch1", "", "Hello")

    call_args = driver.posts.create_post.call_args[1]["options"]
    assert call_args["message"] == "Hello\n\u2026"


@pytest.mark.asyncio
async def test_stream_text_update_appends_ellipsis():
    """Updated draft post includes trailing ellipsis."""
    adapter, driver = _make_streaming_adapter()
    await adapter._stream_text("ch1", "ch1", "", "Hello")
    adapter._last_edit["ch1"] = 0.0  # force throttle to allow edit
    await adapter._stream_text("ch1", "ch1", "", "Hello world")

    call_args = driver.posts.update_post.call_args[1]["options"]
    assert call_args["message"] == "Hello world\n\u2026"


# --- WebSocket reconnection ---


def _make_failing_cm():
    """Context manager that raises ConnectionRefusedError on enter."""
    class CM:
        async def __aenter__(self):
            raise ConnectionRefusedError("server down")

        async def __aexit__(self, *args):
            pass

    return CM()


def _make_normal_close_cm():
    """Context manager that simulates a WS connection that opens and immediately closes."""
    class FakeWS:
        async def send(self, msg):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class CM:
        async def __aenter__(self):
            return FakeWS()

        async def __aexit__(self, *args):
            pass

    return CM()


@pytest.mark.asyncio
async def test_websocket_reconnects_with_backoff_after_errors():
    """WebSocket reconnects with increasing delays, never giving up."""
    adapter = _make_adapter()
    adapter._driver = MagicMock()
    adapter._driver.options = {
        "url": "mm.example.com", "scheme": "https",
        "port": 443, "basepath": "/api/v4", "verify": True,
    }
    adapter._driver.client.token = "test-token"

    attempt_count = 0
    slept_delays: list[float] = []

    def fail_connect(*args, **kwargs):
        nonlocal attempt_count
        attempt_count += 1
        return _make_failing_cm()

    original_sleep = asyncio.sleep

    async def capture_sleep(delay):
        slept_delays.append(delay)
        if len(slept_delays) >= 6:
            raise asyncio.CancelledError()
        await original_sleep(0)

    with patch("fera.adapters.mattermost.websockets.connect", side_effect=fail_connect), \
         patch("asyncio.sleep", side_effect=capture_sleep):
        try:
            await adapter._run_websocket()
        except asyncio.CancelledError:
            pass

    # Should have tried multiple times without giving up
    assert attempt_count >= 6
    # Delays should increase (exponential backoff)
    assert slept_delays[0] < slept_delays[-1]
    # Should be capped at 1800 (30 minutes)
    assert all(d <= 1800 for d in slept_delays)


@pytest.mark.asyncio
async def test_websocket_resets_backoff_after_successful_connection():
    """After a successful connection, backoff resets to the initial delay."""
    adapter = _make_adapter()
    adapter._driver = MagicMock()
    adapter._driver.options = {
        "url": "mm.example.com", "scheme": "https",
        "port": 443, "basepath": "/api/v4", "verify": True,
    }
    adapter._driver.client.token = "test-token"

    call_count = 0
    slept_delays: list[float] = []
    original_sleep = asyncio.sleep

    def alternating_connect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            return _make_failing_cm()
        if call_count == 4:
            return _make_normal_close_cm()
        return _make_failing_cm()

    async def capture_sleep(delay):
        slept_delays.append(delay)
        if len(slept_delays) >= 5:
            raise asyncio.CancelledError()
        await original_sleep(0)

    with patch("fera.adapters.mattermost.websockets.connect", side_effect=alternating_connect), \
         patch("asyncio.sleep", side_effect=capture_sleep):
        try:
            await adapter._run_websocket()
        except asyncio.CancelledError:
            pass

    # After the successful connection (call 4), the next failure (call 5)
    # should use a small initial delay, not the grown backoff
    assert len(slept_delays) >= 4
    # The delay after the reset should be the initial value
    assert slept_delays[-1] <= 10


# --- Ellipsis draft lifecycle ---


@pytest.mark.asyncio
async def test_send_to_agent_creates_ellipsis_draft():
    """_send_to_agent creates an initial '…' draft post before calling send_message."""
    adapter = _make_adapter()
    driver = MagicMock()
    driver.posts.create_post = MagicMock(return_value={"id": "draft1"})
    driver.client.make_request = MagicMock()  # typing indicator
    adapter._driver = driver
    ctx = _make_context()
    adapter._context = ctx

    adapter._ensure_subscribed("ch1:", "ch1", "", "dm-alex")
    await adapter._send_to_agent("ch1", "", "ch1:", "dm-alex", "hello")

    # First create_post call should be the ellipsis
    first_call = driver.posts.create_post.call_args_list[0]
    assert first_call[1]["options"]["message"] == "\u2026"
    # Draft post should be stored
    assert adapter._draft_posts.get("ch1:") == "draft1"


@pytest.mark.asyncio
async def test_flush_draft_deletes_empty_turn():
    """_flush_draft deletes the post if no text was produced (e.g. HEARTBEAT_OK)."""
    adapter = _make_adapter()
    driver = MagicMock()
    driver.posts.delete_post = MagicMock()
    adapter._driver = driver

    adapter._draft_posts["ch1:"] = "draft1"
    # No text in _draft_text — empty turn

    await adapter._flush_draft("ch1:")

    driver.posts.delete_post.assert_called_once_with("draft1")


@pytest.mark.asyncio
async def test_flush_draft_removes_ellipsis():
    """_flush_draft final update does NOT include the ellipsis."""
    adapter = _make_adapter()
    driver = MagicMock()
    driver.posts.update_post = MagicMock()
    adapter._driver = driver

    adapter._draft_posts["ch1:"] = "draft1"
    adapter._draft_text["ch1:"] = "Final answer"

    await adapter._flush_draft("ch1:")

    call_args = driver.posts.update_post.call_args[1]["options"]
    assert call_args["message"] == "Final answer"
    assert "\u2026" not in call_args["message"]


# --- parse_answer helper ---


from fera.adapters.mattermost import parse_answer


class TestParseAnswer:
    """Tests for parse_answer helper."""

    def test_single_valid_number(self):
        assert parse_answer("2", num_options=4, multi=False) == [2]

    def test_single_with_whitespace(self):
        assert parse_answer("  3  ", num_options=4, multi=False) == [3]

    def test_single_out_of_range(self):
        assert parse_answer("5", num_options=4, multi=False) is None

    def test_single_zero(self):
        assert parse_answer("0", num_options=4, multi=False) is None

    def test_non_numeric(self):
        assert parse_answer("yes", num_options=4, multi=False) is None

    def test_large_number_single_select(self):
        """'1407' should be rejected for a 4-option question."""
        assert parse_answer("1407", num_options=4, multi=False) is None

    def test_multi_comma_separated(self):
        assert parse_answer("1,3", num_options=4, multi=True) == [1, 3]

    def test_multi_space_separated(self):
        assert parse_answer("1 3", num_options=4, multi=True) == [1, 3]

    def test_multi_comma_and_space(self):
        assert parse_answer("1, 3, 4", num_options=4, multi=True) == [1, 3, 4]

    def test_multi_with_and(self):
        """'1 and 1' should parse the valid numbers."""
        assert parse_answer("1 and 1", num_options=4, multi=True) == [1]

    def test_multi_deduplicates(self):
        assert parse_answer("1, 1, 2", num_options=4, multi=True) == [1, 2]

    def test_multi_out_of_range_filtered(self):
        assert parse_answer("1, 5", num_options=4, multi=True) == [1]

    def test_multi_all_out_of_range(self):
        assert parse_answer("5, 6", num_options=4, multi=True) is None

    def test_multi_non_numeric_tokens_ignored(self):
        assert parse_answer("1 and 3", num_options=4, multi=True) == [1, 3]

    def test_empty_string(self):
        assert parse_answer("", num_options=4, multi=False) is None

    def test_single_valid_for_multi(self):
        """Single number is valid for multi-select too."""
        assert parse_answer("2", num_options=4, multi=True) == [2]

    def test_single_rejects_multiple_valid_numbers(self):
        """Single-select rejects when multiple valid numbers are given."""
        assert parse_answer("2 3", num_options=4, multi=False) is None

    def test_whitespace_only(self):
        assert parse_answer("   ", num_options=4, multi=False) is None


# --- Draft post skipped when session is busy ---


@pytest.mark.asyncio
async def test_send_to_agent_skips_draft_when_session_busy():
    """No ellipsis draft is created when a turn is already running for the session."""
    adapter = _make_adapter()
    driver = MagicMock()
    driver.posts.create_post = MagicMock(return_value={"id": "draft1"})
    driver.client.make_request = MagicMock()
    adapter._driver = driver
    ctx = _make_context()
    ctx.is_session_busy = MagicMock(return_value=True)
    adapter._context = ctx

    adapter._ensure_subscribed("ch1:", "ch1", "", "dm-alex")
    await adapter._send_to_agent("ch1", "", "ch1:", "dm-alex", "hello")

    # No draft post should have been created
    driver.posts.create_post.assert_not_called()
    assert "ch1:" not in adapter._draft_posts


@pytest.mark.asyncio
async def test_send_to_agent_creates_draft_when_session_free():
    """Ellipsis draft is created normally when no turn is running."""
    adapter = _make_adapter()
    driver = MagicMock()
    driver.posts.create_post = MagicMock(return_value={"id": "draft1"})
    driver.client.make_request = MagicMock()
    adapter._driver = driver
    ctx = _make_context()
    ctx.is_session_busy = MagicMock(return_value=False)
    adapter._context = ctx

    adapter._ensure_subscribed("ch1:", "ch1", "", "dm-alex")
    await adapter._send_to_agent("ch1", "", "ch1:", "dm-alex", "hello")

    # Draft post should have been created
    first_call = driver.posts.create_post.call_args_list[0]
    assert first_call[1]["options"]["message"] == "\u2026"
    assert adapter._draft_posts.get("ch1:") == "draft1"


# --- DM thread session forking ---


@pytest.mark.asyncio
async def test_send_to_agent_forks_new_dm_thread():
    """First message in a DM thread forks from the parent DM session."""
    adapter = _make_adapter()
    driver = MagicMock()
    driver.posts.create_post = MagicMock(return_value={"id": "draft1"})
    driver.client.make_request = MagicMock()
    adapter._driver = driver
    ctx = _make_context()
    ctx.is_session_busy = MagicMock(return_value=False)
    # Parent session exists, thread session does not
    ctx.session_exists = MagicMock(side_effect=lambda s: s == "dm-alex")
    ctx.send_message = AsyncMock()
    adapter._context = ctx

    await adapter._send_to_agent("ch1", "root123", "ch1:root123", "dm-alex-t-root123", "hello")

    ctx.send_message.assert_called_once_with(
        "dm-alex-t-root123", "hello", source="mattermost", fork_from="dm-alex",
    )


@pytest.mark.asyncio
async def test_send_to_agent_no_fork_existing_dm_thread():
    """Subsequent messages in a DM thread do NOT fork."""
    adapter = _make_adapter()
    driver = MagicMock()
    driver.posts.create_post = MagicMock(return_value={"id": "draft1"})
    driver.client.make_request = MagicMock()
    adapter._driver = driver
    ctx = _make_context()
    ctx.is_session_busy = MagicMock(return_value=False)
    # Both parent and thread sessions exist (thread already has history)
    ctx.session_exists = MagicMock(return_value=True)
    ctx.send_message = AsyncMock()
    adapter._context = ctx

    await adapter._send_to_agent("ch1", "root123", "ch1:root123", "dm-alex-t-root123", "hello")

    ctx.send_message.assert_called_once_with(
        "dm-alex-t-root123", "hello", source="mattermost", fork_from=None,
    )


@pytest.mark.asyncio
async def test_send_to_agent_no_fork_channel_thread():
    """Channel threads do NOT fork (only DM threads do)."""
    adapter = _make_adapter()
    driver = MagicMock()
    driver.posts.create_post = MagicMock(return_value={"id": "draft1"})
    driver.client.make_request = MagicMock()
    adapter._driver = driver
    ctx = _make_context()
    ctx.is_session_busy = MagicMock(return_value=False)
    ctx.session_exists = MagicMock(return_value=False)
    ctx.send_message = AsyncMock()
    adapter._context = ctx

    await adapter._send_to_agent("ch1", "root123", "ch1:root123", "mm-general-t-root123", "hello")

    ctx.send_message.assert_called_once_with(
        "mm-general-t-root123", "hello", source="mattermost", fork_from=None,
    )


@pytest.mark.asyncio
async def test_send_to_agent_no_fork_main_dm():
    """Main DM (not a thread) does NOT fork."""
    adapter = _make_adapter()
    driver = MagicMock()
    driver.posts.create_post = MagicMock(return_value={"id": "draft1"})
    driver.client.make_request = MagicMock()
    adapter._driver = driver
    ctx = _make_context()
    ctx.is_session_busy = MagicMock(return_value=False)
    ctx.session_exists = MagicMock(return_value=False)
    ctx.send_message = AsyncMock()
    adapter._context = ctx

    await adapter._send_to_agent("ch1", "", "ch1:", "dm-alex", "hello")

    ctx.send_message.assert_called_once_with(
        "dm-alex", "hello", source="mattermost", fork_from=None,
    )
