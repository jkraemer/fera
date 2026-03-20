from __future__ import annotations

import asyncio
import json
import logging
import re
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path

import websockets

from fera.adapters.base import AdapterContext, AdapterStatus, ChannelAdapter, format_tool_summary
from fera.adapters.commands import SlashCommandHandler
from fera.sanitize import wrap_untrusted

try:
    from mattermostdriver import Driver
except ImportError:
    Driver = None  # type: ignore[assignment,misc]

log = logging.getLogger(__name__)

def parse_answer(
    text: str, num_options: int, *, multi: bool = False,
) -> list[int] | None:
    """Parse a user's answer to an AskUserQuestion.

    Returns a deduplicated list of valid 1-based option numbers in input
    order, or None if no valid numbers were found.
    """
    tokens = re.split(r"[,\s]+", text.strip())
    nums: list[int] = []
    seen: set[int] = set()
    for tok in tokens:
        if tok.isdecimal():
            n = int(tok)
            if 1 <= n <= num_options and n not in seen:
                nums.append(n)
                seen.add(n)
    if not nums:
        return None
    if not multi and len(nums) != 1:
        return None
    return nums


EDIT_THROTTLE = 1.0
MAX_POST_SIZE = 16383
_RECONNECT_INITIAL_DELAY = 5
_RECONNECT_MAX_DELAY = 1800


def split_message(text: str, max_size: int = MAX_POST_SIZE) -> list[str]:
    """Split text into chunks at paragraph boundaries.

    Tries double-newline first, then single-newline, then hard-splits.
    """
    if not text or len(text) <= max_size:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_size:
            chunks.append(remaining)
            break
        # Try paragraph boundary
        split_pos = remaining.rfind("\n\n", 0, max_size)
        if split_pos <= 0:
            # Try line boundary
            split_pos = remaining.rfind("\n", 0, max_size)
        if split_pos <= 0:
            # Hard split
            split_pos = max_size
        chunks.append(remaining[:split_pos])
        remaining = remaining[split_pos:].lstrip("\n")

    return chunks


def _slug_from_title(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "channel"


class MattermostAdapter(ChannelAdapter):
    def __init__(
        self,
        url: str,
        bot_token: str,
        allowed_users: dict[str, str],  # canonical_name -> mm_username
        trusted: bool = False,
        agent_name: str = "main",
        data_dir: Path | None = None,
        workspace_dir: Path | None = None,
    ):
        self._url = url
        self._bot_token = bot_token
        self._allowed_usernames: list[str] = list(allowed_users.values())
        self._canonical_to_username: dict[str, str] = dict(allowed_users)
        self._trusted = trusted
        self._agent_name = agent_name
        self._data_dir = data_dir
        self._workspace_dir = workspace_dir
        self._driver = None
        self._context: AdapterContext | None = None
        self._bot_user_id: str | None = None
        self._allowed_user_ids: set[str] = set()
        self._user_id_to_name: dict[str, str] = {}
        self._user_id_to_canonical: dict[str, str] = {}
        self._commands: SlashCommandHandler | None = None
        self._connected = False
        self._ws_task: asyncio.Task | None = None
        self._draft_posts: dict[str, str] = {}
        self._draft_text: dict[str, str] = {}
        self._last_edit: dict[str, float] = {}
        self._last_was_tool: dict[str, bool] = {}
        self._typing_tasks: dict[str, asyncio.Task] = {}
        self._callbacks: dict[str, object] = {}
        self._session_channels: dict[str, dict] = {}  # session -> {channel_id, root_id}
        self._pending_questions: dict[str, dict] = {}

    @property
    def name(self) -> str:
        return "mattermost"

    @property
    def agent_name(self) -> str:
        return self._agent_name

    @property
    def trusted(self) -> bool:
        return self._trusted

    def status(self) -> AdapterStatus:
        return AdapterStatus(
            connected=self._connected,
            detail="websocket" if self._connected else "stopped",
        )

    def _derive_session(
        self,
        post: dict,
        channel_type: str,
        channel_display_name: str,
        sender_name: str,
    ) -> str:
        root_id = post.get("root_id", "")
        if channel_type == "D":
            user_id = post.get("user_id", "")
            canonical = self._user_id_to_canonical.get(user_id)
            base = f"dm-{canonical}" if canonical else f"dm-{sender_name.lstrip('@') or 'unknown'}"
            if root_id:
                return f"{base}-t-{root_id}"
            return base
        slug = _slug_from_title(channel_display_name)
        if root_id:
            return f"mm-{slug}-t-{root_id}"
        return f"mm-{slug}"

    def _session_key(self, channel_id: str, root_id: str) -> str:
        if root_id:
            return f"{channel_id}:{root_id}"
        return channel_id

    async def start(self, context: AdapterContext) -> None:
        self._context = context
        self._commands = SlashCommandHandler(context)
        self._load_session_channels()
        self._resubscribe_loaded_sessions()

        parsed_url = self._url.replace("https://", "").replace("http://", "").rstrip("/")
        self._driver = Driver({
            "url": parsed_url,
            "token": self._bot_token,
            "scheme": "https",
            "port": 443,
        })
        await asyncio.to_thread(self._driver.login)

        me = await asyncio.to_thread(self._driver.users.get_user, "me")
        self._bot_user_id = me["id"]

        username_to_canonical = {v: k for k, v in self._canonical_to_username.items()}

        for username in self._allowed_usernames:
            try:
                user = await asyncio.to_thread(
                    self._driver.users.get_user_by_username, username
                )
                uid = user["id"]
                self._allowed_user_ids.add(uid)
                self._user_id_to_name[uid] = username
                canonical = username_to_canonical.get(username)
                if canonical:
                    self._user_id_to_canonical[uid] = canonical
            except Exception:
                log.warning("Could not resolve Mattermost user: %s", username)

        self._ws_task = asyncio.create_task(self._run_websocket())
        self._connected = True
        log.info("Mattermost adapter started [agent=%s]", self._agent_name)

    async def stop(self) -> None:
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        if self._driver:
            try:
                await asyncio.to_thread(self._driver.logout)
            except Exception:
                pass
        self._connected = False
        log.info("Mattermost adapter stopped")

    async def _run_websocket(self) -> None:
        opts = self._driver.options
        scheme = opts.get("scheme", "https")
        basepath = opts.get("basepath", "/api/v4")
        ws_url = f"{'wss' if scheme == 'https' else 'ws'}://{opts['url']}:{opts['port']}{basepath}/websocket"

        ssl_ctx: ssl.SSLContext | None = None
        if scheme == "https":
            ssl_ctx = ssl.create_default_context()
            if not opts.get("verify", True):
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE

        delay = _RECONNECT_INITIAL_DELAY
        while True:
            try:
                async with websockets.connect(ws_url, ssl=ssl_ctx) as ws:
                    await ws.send(json.dumps({
                        "seq": 1,
                        "action": "authentication_challenge",
                        "data": {"token": self._driver.client.token},
                    }))
                    async for raw in ws:
                        await self._on_ws_message(raw)
                # normal disconnect — reset backoff and reconnect immediately
                log.info("Mattermost WebSocket closed, reconnecting")
                delay = _RECONNECT_INITIAL_DELAY
                await asyncio.sleep(0)
                continue
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Mattermost WebSocket disconnected")
            log.info("Mattermost WebSocket reconnecting in %ds", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, _RECONNECT_MAX_DELAY)

    async def _on_ws_message(self, raw: str) -> None:
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            return
        if event.get("event") != "posted":
            return
        data = event.get("data", {})
        try:
            post = json.loads(data.get("post", "{}"))
        except json.JSONDecodeError:
            return

        user_id = post.get("user_id", "")
        if user_id == self._bot_user_id:
            return

        channel_type = data.get("channel_type", "")
        if channel_type == "D" and user_id not in self._allowed_user_ids:
            return

        await self._handle_post(data, post)

    async def _handle_post(self, data: dict, post: dict) -> None:
        message = post.get("message", "").strip()
        file_ids: list[str] = post.get("file_ids") or []
        if not message and not file_ids:
            return

        channel_id = post.get("channel_id", "")
        root_id = post.get("root_id", "")

        # Check if there's a pending question for this channel — consume ALL messages
        for qid, pending in list(self._pending_questions.items()):
            if pending["channel_id"] == channel_id:
                for q in pending["questions"]:
                    question_text = q.get("question", "")
                    if question_text not in pending["answers"]:
                        options = q.get("options", [])
                        is_multi = q.get("multiSelect", False)
                        nums = parse_answer(message, len(options), multi=is_multi)
                        if nums is None:
                            n = len(options)
                            if is_multi:
                                hint = f"Reply with one or more numbers (1\u2013{n}), e.g. 1,3"
                            else:
                                hint = f"Reply with a number (1\u2013{n})"
                            await self._post_reply(channel_id, root_id, hint)
                        else:
                            labels = [options[i - 1].get("label", "") for i in nums]
                            pending["answers"][question_text] = ", ".join(labels)
                            await self._post_reply(
                                channel_id, root_id, f"\u2713 {', '.join(labels)}",
                            )
                            if len(pending["answers"]) >= len(pending["questions"]):
                                session = pending["session"]
                                answers = pending["answers"]
                                del self._pending_questions[qid]
                                await self._context.answer_question(
                                    session, qid, answers,
                                )
                        return

        channel_type = data.get("channel_type", "")
        channel_display_name = data.get("channel_display_name", channel_id)
        sender_name = data.get("sender_name", "")

        session = self._derive_session(post, channel_type, channel_display_name, sender_name)
        state_key = self._session_key(channel_id, root_id)

        if self._commands and message and self._commands.match(message):
            if message == "/clear":
                await self._post_reply(channel_id, root_id, "Saving memory and clearing session, please wait...")
            result = await self._commands.handle(message, session)
            if result:
                await self._post_reply(channel_id, root_id, result.response)
        elif file_ids:
            self._ensure_subscribed(state_key, channel_id, root_id, session)
            asyncio.create_task(
                self._handle_file_attachments(channel_id, root_id, state_key, session, message, file_ids)
            )
        else:
            text = message
            sender = sender_name.lstrip("@") if sender_name else ""
            is_group = channel_type != "D"
            if not self._trusted:
                if is_group and sender:
                    text = wrap_untrusted(text, source="mattermost", sender=sender)
                else:
                    text = wrap_untrusted(text, source="mattermost")
            elif is_group and sender:
                text = f"[{sender}] {text}"
            self._ensure_subscribed(state_key, channel_id, root_id, session)
            asyncio.create_task(
                self._send_to_agent(channel_id, root_id, state_key, session, text)
            )

    async def _handle_file_attachments(
        self,
        channel_id: str,
        root_id: str,
        state_key: str,
        session: str,
        caption: str,
        file_ids: list[str],
    ) -> None:
        if not self._workspace_dir:
            return

        now = datetime.now(timezone.utc)
        inbox = self._workspace_dir / "inbox" / now.strftime("%Y-%m-%d") / "mattermost"
        inbox.mkdir(parents=True, exist_ok=True)

        notifications = []
        for file_id in file_ids:
            try:
                info = await asyncio.to_thread(self._driver.files.get_file_info, file_id)
                raw_name = info.get("name") or f"file-{now.strftime('%H%M%S')}"
                filename = Path(raw_name).name or f"file-{now.strftime('%H%M%S')}"

                data = await asyncio.to_thread(self._driver.files.get_file, file_id)

                dest = inbox / filename
                counter = 1
                while dest.exists():
                    stem = Path(filename).stem
                    suffix = Path(filename).suffix
                    dest = inbox / f"{stem}-{counter}{suffix}"
                    counter += 1

                dest.write_bytes(data)
                rel_path = dest.relative_to(self._workspace_dir)
                notifications.append(f"[File saved to {rel_path}]")
            except Exception:
                log.exception("Failed to download Mattermost file %s", file_id)

        if not notifications:
            return

        parts = notifications + ([caption] if caption else [])
        text = "\n".join(parts)
        if not self._trusted:
            text = wrap_untrusted(text, source="mattermost")

        await self._send_to_agent(channel_id, root_id, state_key, session, text)

    async def _post_reply(self, channel_id: str, root_id: str, text: str) -> None:
        for chunk in split_message(text):
            options: dict = {"channel_id": channel_id, "message": chunk}
            if root_id:
                options["root_id"] = root_id
            try:
                await asyncio.to_thread(self._driver.posts.create_post, options=options)
            except Exception:
                log.exception("Failed to post reply to channel %s", channel_id)

    def _make_callback(self, channel_id: str, root_id: str):
        key = self._session_key(channel_id, root_id)
        if key not in self._callbacks:
            async def callback(event, _ch=channel_id, _rt=root_id):
                await self._on_event(event, _ch, _rt)
            self._callbacks[key] = callback
        return self._callbacks[key]

    @property
    def _sessions_file(self) -> Path | None:
        if self._data_dir is None:
            return None
        return self._data_dir / "mattermost_sessions.json"

    def _load_session_channels(self) -> None:
        path = self._sessions_file
        if not path or not path.exists():
            return
        try:
            self._session_channels = json.loads(path.read_text())
        except Exception:
            log.warning("Failed to load mattermost_sessions.json, starting with empty mapping")
            return
        # Sanitize: non-thread sessions must never carry a root_id
        dirty = False
        for session, info in self._session_channels.items():
            if "-t-" not in session and info.get("root_id"):
                log.warning(
                    "Sanitizing contaminated session %s (root_id=%s)",
                    session, info["root_id"],
                )
                info["root_id"] = ""
                dirty = True
        if dirty:
            self._save_session_channels()

    def _save_session_channels(self) -> None:
        path = self._sessions_file
        if not path:
            return
        path.write_text(json.dumps(self._session_channels))

    def _resubscribe_loaded_sessions(self) -> None:
        """Subscribe to all sessions previously loaded from the sessions file."""
        for session, info in self._session_channels.items():
            channel_id = info["channel_id"]
            root_id = info["root_id"]
            state_key = self._session_key(channel_id, root_id)
            self._ensure_subscribed(state_key, channel_id, root_id, session)

    def _ensure_subscribed(
        self, state_key: str, channel_id: str, root_id: str, session: str
    ) -> None:
        # Guard: non-thread sessions must never carry a root_id
        if "-t-" not in session:
            root_id = ""
        cb = self._make_callback(channel_id, root_id)
        self._context.unsubscribe(session, cb)
        self._context.subscribe(session, cb)
        self._session_channels[session] = {"channel_id": channel_id, "root_id": root_id}
        self._save_session_channels()

    async def _send_to_agent(
        self, channel_id: str, root_id: str, state_key: str, session: str, text: str
    ) -> None:
        # Skip draft post when a turn is already running — the message will
        # be queued and processed as part of a combined follow-up turn whose
        # events will create a new draft via _stream_text.
        if not self._context.is_session_busy(session):
            await self._start_typing(channel_id)
            options: dict = {"channel_id": channel_id, "message": "\u2026"}
            if root_id:
                options["root_id"] = root_id
            try:
                post = await asyncio.to_thread(
                    self._driver.posts.create_post, options=options,
                )
                self._draft_posts[state_key] = post["id"]
            except Exception:
                log.exception("Failed to create ellipsis draft for %s", state_key)
        # Fork from parent DM session on first message in a new thread
        fork_from = None
        if "-t-" in session and session.startswith("dm-"):
            parent = session.rsplit("-t-", 1)[0]
            if (
                not self._context.session_exists(session)
                and self._context.session_exists(parent)
            ):
                fork_from = parent
        try:
            await self._context.send_message(
                session, text, source=self.name, fork_from=fork_from,
            )
        except Exception:
            log.exception("Agent turn failed for channel %s", channel_id)
            self._stop_typing(channel_id)
            await self._post_reply(channel_id, root_id, "Error: agent turn failed")

    async def _typing_loop(self, channel_id: str) -> None:
        try:
            while True:
                await asyncio.sleep(4)
                try:
                    await asyncio.to_thread(
                        self._driver.client.make_request,
                        "post",
                        f"/channels/{channel_id}/typing",
                    )
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass

    async def _start_typing(self, channel_id: str) -> None:
        self._stop_typing(channel_id)
        try:
            await asyncio.to_thread(
                self._driver.client.make_request,
                "post",
                f"/channels/{channel_id}/typing",
            )
        except Exception:
            pass
        self._typing_tasks[channel_id] = asyncio.create_task(
            self._typing_loop(channel_id)
        )

    def _stop_typing(self, channel_id: str) -> None:
        task = self._typing_tasks.pop(channel_id, None)
        if task:
            task.cancel()

    async def _stream_text(
        self, state_key: str, channel_id: str, root_id: str, text: str
    ) -> None:
        # Skip streaming updates that exceed the post size limit.
        # The full text will be properly split on flush.
        if len(text) > MAX_POST_SIZE:
            return

        display_text = text + "\n\u2026"

        now = time.monotonic()
        last = self._last_edit.get(state_key, 0)

        if state_key not in self._draft_posts:
            options: dict = {
                "channel_id": channel_id,
                "message": display_text,
            }
            if root_id:
                options["root_id"] = root_id
            try:
                post = await asyncio.to_thread(
                    self._driver.posts.create_post, options=options
                )
                self._draft_posts[state_key] = post["id"]
                self._last_edit[state_key] = now
            except Exception:
                log.exception("Failed to create draft post for %s", state_key)
        elif now - last >= EDIT_THROTTLE:
            post_id = self._draft_posts[state_key]
            try:
                await asyncio.to_thread(
                    self._driver.posts.update_post,
                    post_id,
                    options={"id": post_id, "message": display_text},
                )
                self._last_edit[state_key] = now
            except Exception:
                log.exception("Failed to update draft post %s", post_id)

    async def _flush_draft(self, state_key: str) -> None:
        self._stop_typing_for_key(state_key)
        if state_key in self._draft_text:
            text = self._draft_text[state_key]
            post_id = self._draft_posts.get(state_key)
            chunks = split_message(text)
            if post_id and chunks:
                try:
                    await asyncio.to_thread(
                        self._driver.posts.update_post,
                        post_id,
                        options={"id": post_id, "message": chunks[0]},
                    )
                except Exception:
                    log.exception("Failed to flush draft post %s", post_id)
                if len(chunks) > 1:
                    channel_id = state_key.split(":")[0]
                    root_id = state_key.split(":", 1)[1] if ":" in state_key else ""
                    for chunk in chunks[1:]:
                        await self._post_reply(channel_id, root_id, chunk)
            elif not post_id and text:
                log.warning("Flush for %s has text but no post ID — dropping", state_key)
        elif state_key in self._draft_posts:
            # Empty turn (e.g. HEARTBEAT_OK) — delete the ellipsis post
            post_id = self._draft_posts[state_key]
            try:
                await asyncio.to_thread(self._driver.posts.delete_post, post_id)
            except Exception:
                log.exception("Failed to delete empty draft post %s", post_id)
        self._draft_posts.pop(state_key, None)
        self._draft_text.pop(state_key, None)
        self._last_edit.pop(state_key, None)
        self._last_was_tool.pop(state_key, None)

    def _stop_typing_for_key(self, state_key: str) -> None:
        channel_id = state_key.split(":")[0]
        self._stop_typing(channel_id)

    async def _on_event(self, event: dict, channel_id: str, root_id: str) -> None:
        event_type = event.get("event", "")

        # Security alerts bypass adapter/source filters
        if event_type == "security.alert":
            data = event.get("data", {})
            source = data.get("source", "unknown")
            patterns = ", ".join(data.get("patterns", []))
            excerpt = data.get("excerpt", "")
            alert_text = (
                f"\u26a0\ufe0f Injection pattern detected\n"
                f"Source: {source}\n"
                f"Patterns: {patterns}\n"
                f"Excerpt: {excerpt[:200]}"
            )
            try:
                await self._post_reply(channel_id, root_id, alert_text)
            except Exception:
                log.exception("Failed to send security alert to channel %s", channel_id)
            return

        if event_type == "agent.user_question":
            # Flush any in-progress draft so post-answer text starts a new message
            state_key = self._session_key(channel_id, root_id)
            await self._flush_draft(state_key)
            data = event.get("data", {})
            question_id = data.get("question_id", "")
            questions = data.get("questions", [])
            session = event.get("session", "")
            self._pending_questions[question_id] = {
                "questions": questions,
                "answers": {},
                "session": session,
                "channel_id": channel_id,
                "root_id": root_id,
            }
            for q in questions:
                question_text = q.get("question", "")
                options = q.get("options", [])
                lines = [f"**{question_text}**", ""]
                for i, opt in enumerate(options, 1):
                    label = opt.get("label", f"Option {i}")
                    desc = opt.get("description", "")
                    lines.append(f"{i}. **{label}** — {desc}" if desc else f"{i}. **{label}**")
                lines.append("")
                lines.append("_Reply with the number of your choice._")
                msg = "\n".join(lines)
                await self._post_reply(channel_id, root_id, msg)
            return

        target = event.get("target_adapter")
        if target and target != self.name:
            return

        # Security alerts bypass turn_source filter
        if event_type == "agent.alert":
            data = event.get("data", {})
            message = data.get("message", "Security alert")
            severity = data.get("severity", "medium")
            icon = "\U0001f6a8" if severity == "critical" else "\u26a0\ufe0f"
            state_key = self._session_key(channel_id, root_id)
            await self._flush_draft(state_key)
            await self._post_reply(channel_id, root_id, f"{icon} {message}")
            return

        turn_source = event.get("turn_source", "")
        if turn_source and turn_source not in (self.name, "cron", "heartbeat"):
            return

        state_key = self._session_key(channel_id, root_id)

        if event_type == "agent.tool_use":
            tool_name = event.get("data", {}).get("name", "")
            if not tool_name:
                return
            tool_input = event.get("data", {}).get("input")
            line = format_tool_summary(tool_name, tool_input)
            existing = self._draft_text.get(state_key, "")
            if existing:
                sep = "\n" if self._last_was_tool.get(state_key) else "\n\n"
                self._draft_text[state_key] = existing + sep + line
            else:
                self._draft_text[state_key] = line
            self._last_was_tool[state_key] = True
            await self._stream_text(state_key, channel_id, root_id, self._draft_text[state_key])
        elif event_type == "agent.text":
            text = event.get("data", {}).get("text", "")
            if not text:
                return
            self._stop_typing(channel_id)
            existing = self._draft_text.get(state_key, "")
            if existing:
                self._draft_text[state_key] = existing + "\n\n" + text
            else:
                self._draft_text[state_key] = text
            self._last_was_tool[state_key] = False
            await self._stream_text(state_key, channel_id, root_id, self._draft_text[state_key])
        elif event_type == "agent.done":
            await self._flush_draft(state_key)
        elif event_type == "agent.error":
            error = event.get("data", {}).get("error", "Unknown error")
            await self._flush_draft(state_key)
            await self._post_reply(channel_id, root_id, f"Error: {error}")
