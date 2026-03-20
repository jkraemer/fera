from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from faster_whisper import WhisperModel

from fera.adapters.base import AdapterContext, AdapterStatus, ChannelAdapter
from fera.adapters.commands import SlashCommandHandler
from fera.render import render_telegram_html, split_telegram_html
from fera.sanitize import wrap_untrusted

log = logging.getLogger(__name__)

# Minimum interval between message edits (seconds) to avoid Telegram rate limits
EDIT_THROTTLE = 1.0

_GROUP_CHAT_TYPES = ("private", "group", "supergroup")


def _slug_from_title(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "group"


class TelegramAdapter(ChannelAdapter):
    """Telegram DM adapter using long polling."""

    def __init__(
        self,
        bot_token: str,
        allowed_users: dict[str, int],
        workspace_dir: Path | None = None,
        whisper_model: str = "small",
        data_dir: Path | None = None,
        trusted: bool = False,
        agent_name: str = "main",
    ):
        self._bot_token = bot_token
        self._allowed_users: set[int] = set(allowed_users.values())
        self._user_id_to_canonical: dict[int, str] = {v: k for k, v in allowed_users.items()}
        self._workspace_dir = workspace_dir
        self._whisper_model = whisper_model
        self._data_dir = data_dir
        self._trusted = trusted
        self._agent_name = agent_name
        self._transcriber: WhisperModel | None = None
        self._context: AdapterContext | None = None
        self._commands: SlashCommandHandler | None = None
        self._app: Application | None = None
        self._polling_task: asyncio.Task | None = None
        self._connected = False
        # chat_id -> session name (non-topic groups)
        self._chat_sessions: dict[int, str] = {}
        # (chat_id, thread_id) -> session name (forum topics)
        self._topic_sessions: dict[tuple[int, int], str] = {}
        # chat_id -> message_id of current draft (for streaming edits)
        self._draft_messages: dict[int, int] = {}
        # chat_id -> accumulated text for current draft
        self._draft_text: dict[int, str] = {}
        # chat_id -> last edit timestamp
        self._last_edit: dict[int, float] = {}
        # chat_id -> asyncio.Task for periodic typing indicator
        self._typing_tasks: dict[int, asyncio.Task] = {}
        # chat_id -> message_id of current status message (tool names)
        self._status_messages: dict[int, int] = {}
        # (chat_id, thread_id) -> bound event callback (cached per topic/chat)
        self._callbacks: dict[tuple[int, int | None], object] = {}
        # question_id -> {questions, answers, session}
        self._pending_questions: dict[str, dict] = {}

    @property
    def name(self) -> str:
        return "telegram"

    @property
    def agent_name(self) -> str:
        return self._agent_name

    def _session_for_message(self, chat, user, thread_id: int | None = None) -> str:
        """Derive session for an incoming message."""
        if chat.type in ("group", "supergroup"):
            if thread_id is not None:
                key = (chat.id, thread_id)
                if key in self._topic_sessions:
                    return self._topic_sessions[key]
                title = chat.title or str(chat.id)
                session = f"tg-{_slug_from_title(title)}-t{thread_id}"
                self._topic_sessions[key] = session
                self._save_chat_sessions()
                return session
            if chat.id in self._chat_sessions:
                return self._chat_sessions[chat.id]
            title = chat.title or str(chat.id)
            session = f"tg-{_slug_from_title(title)}"
            self._chat_sessions[chat.id] = session
            self._save_chat_sessions()
            return session
        # Private/DM: derive from canonical user name
        canonical = self._user_id_to_canonical.get(user.id)
        return f"dm-{canonical}" if canonical else f"tg-dm-{user.id}"

    @property
    def _sessions_file(self) -> Path | None:
        if self._data_dir is None:
            return None
        return self._data_dir / "telegram_sessions.json"

    def _load_chat_sessions(self) -> None:
        """Load persisted chat-session mappings from disk."""
        path = self._sessions_file
        if path is None or not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            for raw_key, session in data.items():
                if ":" in raw_key:
                    chat_id_str, thread_id_str = raw_key.split(":", 1)
                    self._topic_sessions[(int(chat_id_str), int(thread_id_str))] = session
                else:
                    self._chat_sessions[int(raw_key)] = session
        except Exception:
            log.warning("Failed to load telegram_sessions.json, starting with empty mapping")

    def _save_chat_sessions(self) -> None:
        """Persist current chat-session mappings to disk."""
        path = self._sessions_file
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, str] = {str(k): v for k, v in self._chat_sessions.items()}
        data.update({f"{cid}:{tid}": v for (cid, tid), v in self._topic_sessions.items()})
        path.write_text(json.dumps(data, indent=2) + "\n")

    async def start(self, context: AdapterContext) -> None:
        self._load_chat_sessions()
        self._context = context
        self._resubscribe_loaded_sessions()
        self._commands = SlashCommandHandler(context)
        self._app = (
            Application.builder()
            .token(self._bot_token)
            .build()
        )
        self._app.add_error_handler(self._handle_telegram_error)
        self._app.add_handler(CommandHandler("clear", self._handle_clear_command))
        self._app.add_handler(CommandHandler("stop", self._handle_stop_command))
        self._app.add_handler(CommandHandler("session", self._handle_command))
        self._app.add_handler(CommandHandler("sessions", self._handle_sessions_command))
        self._app.add_handler(CommandHandler("status", self._handle_status_command))
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )
        self._app.add_handler(
            MessageHandler(filters.PHOTO | filters.Document.ALL | filters.AUDIO, self._handle_media)
        )
        self._transcriber = WhisperModel(
            self._whisper_model, device="cpu", compute_type="int8"
        )
        self._app.add_handler(MessageHandler(filters.VOICE, self._handle_voice))
        self._app.add_handler(CallbackQueryHandler(self._handle_question_callback, pattern=r"^ask:"))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        self._connected = True
        log.info("Telegram adapter started (polling) [agent=%s]", self._agent_name)

    async def stop(self) -> None:
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        self._connected = False
        log.info("Telegram adapter stopped")

    def status(self) -> AdapterStatus:
        return AdapterStatus(
            connected=self._connected,
            detail="polling" if self._connected else "stopped",
        )

    def _is_authorized(self, user_id: int) -> bool:
        return user_id in self._allowed_users

    @staticmethod
    def _thread_kwargs(thread_id: int | None) -> dict:
        """Return message_thread_id kwarg dict for a topic thread, or empty dict."""
        return {"message_thread_id": thread_id} if thread_id is not None else {}

    async def _handle_telegram_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        log.exception("Telegram error (update=%r): %s", update, context.error, exc_info=context.error)

    async def _handle_message(self, update: Update, tg_context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        chat = update.effective_chat
        log.info("_handle_message: chat_type=%s user_id=%s", chat.type if chat else None, user.id if user else None)
        if not user or not chat or chat.type not in _GROUP_CHAT_TYPES:
            return
        thread_id = update.message.message_thread_id
        if chat.type == "private" and not self._is_authorized(user.id):
            await update.message.reply_text(
                f"Not authorized. Your user ID is: {user.id}"
            )
            return
        text = update.message.text
        if not text:
            return
        is_group = chat.type != "private"
        sender = user.first_name or str(user.id)
        if not self._trusted:
            if is_group:
                text = wrap_untrusted(text, source="telegram", sender=sender)
            else:
                text = wrap_untrusted(text, source="telegram")
        elif is_group:
            text = f"[{sender}] {text}"
        session = self._session_for_message(chat, user, thread_id=thread_id)
        self._ensure_subscribed(chat.id, session, thread_id)
        asyncio.create_task(self._send_to_agent(chat.id, session, text, thread_id))

    async def _handle_media(self, update: Update, tg_context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        chat = update.effective_chat
        if not user or not chat or chat.type not in _GROUP_CHAT_TYPES:
            return
        thread_id = update.message.message_thread_id
        if chat.type == "private" and not self._is_authorized(user.id):
            await update.message.reply_text(
                f"Not authorized. Your user ID is: {user.id}"
            )
            return
        if not self._workspace_dir:
            await update.message.reply_text("Media handling not available (no workspace configured)")
            return

        # Build inbox path
        now = datetime.now(timezone.utc)
        inbox = self._workspace_dir / "inbox" / now.strftime("%Y-%m-%d") / "telegram"
        inbox.mkdir(parents=True, exist_ok=True)

        # Download file
        if update.message.photo:
            photo = update.message.photo[-1]  # largest size
            file = await photo.get_file()
            filename = f"photo-{now.strftime('%H%M%S')}.jpg"
        elif update.message.document:
            doc = update.message.document
            file = await doc.get_file()
            raw_name = doc.file_name or f"file-{now.strftime('%H%M%S')}"
            filename = Path(raw_name).name or f"file-{now.strftime('%H%M%S')}"
        elif update.message.audio:
            audio = update.message.audio
            file = await audio.get_file()
            raw_name = audio.file_name or f"audio-{now.strftime('%H%M%S')}"
            filename = Path(raw_name).name or f"audio-{now.strftime('%H%M%S')}"
        else:
            return

        # Handle collisions
        dest = inbox / filename
        counter = 1
        while dest.exists():
            stem = Path(filename).stem
            suffix = Path(filename).suffix
            dest = inbox / f"{stem}-{counter}{suffix}"
            counter += 1

        await file.download_to_drive(str(dest))
        rel_path = dest.relative_to(self._workspace_dir)
        caption = update.message.caption or ""
        text = f"[File saved to {rel_path}]\n{caption}".strip()
        text = wrap_untrusted(text, source="telegram")

        session = self._session_for_message(chat, user, thread_id=thread_id)
        self._ensure_subscribed(chat.id, session, thread_id)
        asyncio.create_task(self._send_to_agent(chat.id, session, text, thread_id))

    def _transcribe(self, path: str) -> str:
        """Synchronous transcription — run via asyncio.to_thread."""
        segments, _ = self._transcriber.transcribe(path)
        return " ".join(seg.text.strip() for seg in segments)

    async def _handle_voice(self, update: Update, tg_context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        chat = update.effective_chat
        if not user or not chat or chat.type not in _GROUP_CHAT_TYPES:
            return
        thread_id = update.message.message_thread_id
        if chat.type == "private" and not self._is_authorized(user.id):
            await update.message.reply_text(
                f"Not authorized. Your user ID is: {user.id}"
            )
            return
        if not self._workspace_dir:
            await update.message.reply_text("Media handling not available (no workspace configured)")
            return

        now = datetime.now(timezone.utc)
        inbox = self._workspace_dir / "inbox" / now.strftime("%Y-%m-%d") / "telegram"
        inbox.mkdir(parents=True, exist_ok=True)

        file = await update.message.voice.get_file()
        filename = f"voice-{now.strftime('%H%M%S')}.ogg"

        dest = inbox / filename
        counter = 1
        while dest.exists():
            stem = Path(filename).stem
            suffix = Path(filename).suffix
            dest = inbox / f"{stem}-{counter}{suffix}"
            counter += 1

        await file.download_to_drive(str(dest))

        transcript = await asyncio.to_thread(self._transcribe, str(dest))
        dest.with_suffix(".txt").write_text(transcript, encoding="utf-8")

        rel_path = dest.relative_to(self._workspace_dir)
        text = f"[Voice note saved to {rel_path}]\nTranscript: {transcript}"
        if not self._trusted:
            text = wrap_untrusted(text, source="telegram")

        session = self._session_for_message(chat, user, thread_id=thread_id)
        self._ensure_subscribed(chat.id, session, thread_id)
        asyncio.create_task(self._send_to_agent(chat.id, session, text, thread_id))

    async def _handle_command(self, update: Update, tg_context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        chat = update.effective_chat
        if not user or not chat:
            return
        if chat.type == "private" and not self._is_authorized(user.id):
            await update.message.reply_text(f"Not authorized. Your user ID is: {user.id}")
            return
        thread_id = update.message.message_thread_id
        session = self._session_for_message(chat, user, thread_id=thread_id)
        result = await self._commands.handle("/session", session)
        if result is None:
            return
        await update.message.reply_text(result.response)

    async def _handle_sessions_command(self, update: Update, tg_context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        chat = update.effective_chat
        if not user or not chat:
            return
        if chat.type == "private" and not self._is_authorized(user.id):
            await update.message.reply_text(f"Not authorized. Your user ID is: {user.id}")
            return
        thread_id = update.message.message_thread_id
        session = self._session_for_message(chat, user, thread_id=thread_id)
        result = await self._commands.handle("/sessions", session)
        await update.message.reply_text(result.response)

    async def _handle_status_command(self, update: Update, tg_context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        chat = update.effective_chat
        if not user or not chat:
            return
        if chat.type == "private" and not self._is_authorized(user.id):
            await update.message.reply_text(f"Not authorized. Your user ID is: {user.id}")
            return
        thread_id = update.message.message_thread_id
        session = self._session_for_message(chat, user, thread_id=thread_id)
        result = await self._commands.handle("/status", session)
        await update.message.reply_text(result.response)

    async def _handle_stop_command(self, update: Update, tg_context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        chat = update.effective_chat
        if not user or not chat:
            return
        if chat.type == "private" and not self._is_authorized(user.id):
            await update.message.reply_text(f"Not authorized. Your user ID is: {user.id}")
            return
        thread_id = update.message.message_thread_id
        session = self._session_for_message(chat, user, thread_id=thread_id)
        result = await self._commands.handle("/stop", session)
        if result is None:
            return
        await update.message.reply_text(result.response)

    async def _handle_clear_command(self, update: Update, tg_context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        chat = update.effective_chat
        if not user or not chat:
            return
        if chat.type == "private" and not self._is_authorized(user.id):
            await update.message.reply_text(f"Not authorized. Your user ID is: {user.id}")
            return
        thread_id = update.message.message_thread_id
        session = self._session_for_message(chat, user, thread_id=thread_id)
        await update.message.reply_text("Saving memory and clearing session, please wait...")
        result = await self._commands.handle("/clear", session)
        if result is None:
            return
        await self._app.bot.send_message(chat.id, result.response, **self._thread_kwargs(thread_id))

    def _resubscribe_loaded_sessions(self) -> None:
        """Subscribe to all sessions previously loaded from the sessions file."""
        for chat_id, session in self._chat_sessions.items():
            self._ensure_subscribed(chat_id, session)
        for (chat_id, thread_id), session in self._topic_sessions.items():
            self._ensure_subscribed(chat_id, session, thread_id)

    def _ensure_subscribed(self, chat_id: int, session: str, thread_id: int | None = None) -> None:
        """Ensure we're subscribed to this session for this chat/topic."""
        cb = self._make_callback(chat_id, thread_id)
        # Idempotent: unsubscribe first (no-op if not subscribed), then subscribe
        self._context.unsubscribe(session, cb)
        self._context.subscribe(session, cb)

    def _make_callback(self, chat_id: int, thread_id: int | None = None):
        """Create a bound callback for a specific chat/topic. Cached per (chat_id, thread_id)."""
        key = (chat_id, thread_id)
        if key not in self._callbacks:
            async def callback(event, _chat_id=chat_id, _thread_id=thread_id):
                await self._on_event(event, _chat_id, _thread_id)
            self._callbacks[key] = callback
        return self._callbacks[key]

    async def _typing_loop(self, chat_id: int, thread_id: int | None = None) -> None:
        """Periodically send typing indicator until cancelled."""
        try:
            while True:
                await self._app.bot.send_chat_action(chat_id, ChatAction.TYPING, **self._thread_kwargs(thread_id))
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass

    async def _start_typing(self, chat_id: int, thread_id: int | None = None) -> None:
        """Send typing indicator immediately and start a keep-alive loop."""
        self._stop_typing(chat_id)
        try:
            await self._app.bot.send_chat_action(chat_id, ChatAction.TYPING, **self._thread_kwargs(thread_id))
        except Exception:
            log.exception("Failed to send typing indicator to chat %d", chat_id)
        self._typing_tasks[chat_id] = asyncio.create_task(self._typing_loop(chat_id, thread_id))

    def _stop_typing(self, chat_id: int) -> None:
        """Cancel the typing indicator loop for this chat."""
        task = self._typing_tasks.pop(chat_id, None)
        if task:
            task.cancel()

    async def _update_status(self, chat_id: int, tool_name: str, thread_id: int | None = None) -> None:
        """Send or edit the status message showing the current tool name."""
        status_text = f"{tool_name}\u2026"
        if chat_id in self._status_messages:
            try:
                await self._app.bot.edit_message_text(
                    status_text, chat_id=chat_id,
                    message_id=self._status_messages[chat_id],
                )
            except BadRequest as e:
                log.debug("Could not edit status in chat %d: %s", chat_id, e)
            except Exception:
                log.exception("Failed to edit status in chat %d", chat_id)
        else:
            try:
                msg = await self._app.bot.send_message(chat_id, status_text, **self._thread_kwargs(thread_id))
                self._status_messages[chat_id] = msg.message_id
            except Exception:
                log.exception("Failed to send status to chat %d", chat_id)

    async def _delete_status(self, chat_id: int) -> None:
        """Delete the status message if one exists."""
        msg_id = self._status_messages.pop(chat_id, None)
        if msg_id is not None:
            try:
                await self._app.bot.delete_message(chat_id, msg_id)
            except BadRequest as e:
                log.debug("Could not delete status in chat %d: %s", chat_id, e)
            except Exception:
                log.exception("Failed to delete status in chat %d", chat_id)

    async def _send_to_agent(self, chat_id: int, session: str, text: str, thread_id: int | None = None) -> None:
        """Run an agent turn (fire-and-forget from handler)."""
        await self._start_typing(chat_id, thread_id)
        try:
            await self._context.send_message(session, text, source=self.name)
        except Exception:
            log.exception("Agent turn failed for chat %d", chat_id)
            self._stop_typing(chat_id)
            try:
                await self._app.bot.send_message(chat_id, "Error: agent turn failed", **self._thread_kwargs(thread_id))
            except Exception:
                pass

    async def _on_event(self, event: dict, chat_id: int, thread_id: int | None = None) -> None:
        """Handle an outbound event by sending/editing Telegram messages."""
        event_type = event.get("event", "")

        # AskUserQuestion: render as inline keyboards
        if event_type == "agent.user_question":
            # Flush any in-progress draft so post-answer text starts a new message
            await self._flush_draft(chat_id, thread_id)
            data = event.get("data", {})
            question_id = data.get("question_id", "")
            questions = data.get("questions", [])
            session = event.get("session", "")
            self._pending_questions[question_id] = {
                "questions": questions,
                "answers": {},
                "session": session,
            }
            for qi, q in enumerate(questions):
                text = q.get("question", "")
                options = q.get("options", [])
                buttons = []
                for oi, opt in enumerate(options):
                    label = opt.get("label", f"Option {oi}")
                    callback_data = f"ask:{question_id}:{qi}:{oi}"
                    buttons.append([InlineKeyboardButton(label, callback_data=callback_data)])
                markup = InlineKeyboardMarkup(buttons)
                try:
                    await self._app.bot.send_message(
                        chat_id, text, reply_markup=markup,
                        **self._thread_kwargs(thread_id),
                    )
                except Exception:
                    log.exception("Failed to send question to chat %d", chat_id)
            return

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
                await self._app.bot.send_message(
                    chat_id, alert_text, **self._thread_kwargs(thread_id),
                )
            except Exception:
                log.exception("Failed to send security alert to chat %d", chat_id)
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
            await self._flush_draft(chat_id, thread_id)
            try:
                await self._app.bot.send_message(
                    chat_id,
                    f"{icon} {message}",
                    **self._thread_kwargs(thread_id),
                )
            except Exception:
                log.exception("Failed to send alert to chat %d", chat_id)
            return

        turn_source = event.get("turn_source", "")
        if turn_source and turn_source not in (self.name, "cron", "heartbeat"):
            return

        if event_type == "agent.text":
            text = event.get("data", {}).get("text", "")
            if not text:
                return
            self._stop_typing(chat_id)
            await self._delete_status(chat_id)
            await self._stream_text(chat_id, text, thread_id)

        elif event_type == "agent.tool_use":
            tool_name = event.get("data", {}).get("name", "")
            if not tool_name:
                return
            await self._start_typing(chat_id, thread_id)
            await self._update_status(chat_id, tool_name, thread_id)

        elif event_type == "agent.done":
            await self._flush_draft(chat_id, thread_id)

        elif event_type == "agent.error":
            error = event.get("data", {}).get("error", "Unknown error")
            await self._flush_draft(chat_id, thread_id)
            try:
                await self._app.bot.send_message(chat_id, f"Error: {error}", **self._thread_kwargs(thread_id))
            except Exception:
                log.exception("Failed to send error to chat %d", chat_id)

    async def _stream_text(self, chat_id: int, text: str, thread_id: int | None = None) -> None:
        """Accumulate text and stream via draft message editing."""
        if chat_id in self._draft_text:
            self._draft_text[chat_id] += "\n\n" + text
        else:
            self._draft_text[chat_id] = text

        now = time.monotonic()
        last = self._last_edit.get(chat_id, 0)

        if chat_id not in self._draft_messages:
            # First chunk — send a new message
            try:
                msg = await self._app.bot.send_message(
                    chat_id, self._draft_text[chat_id],
                    **self._thread_kwargs(thread_id),
                )
                self._draft_messages[chat_id] = msg.message_id
                self._last_edit[chat_id] = now
            except Exception:
                log.exception("Failed to send draft to chat %d", chat_id)
        elif now - last >= EDIT_THROTTLE:
            # Subsequent chunks — edit existing message (throttled)
            try:
                await self._app.bot.edit_message_text(
                    self._draft_text[chat_id],
                    chat_id=chat_id,
                    message_id=self._draft_messages[chat_id],
                )
                self._last_edit[chat_id] = now
            except BadRequest as e:
                log.debug("Could not edit draft in chat %d: %s", chat_id, e)
            except Exception:
                log.exception("Failed to edit draft in chat %d", chat_id)

    async def _flush_draft(self, chat_id: int, thread_id: int | None = None) -> None:
        """Final edit of draft message with rendered markdown, then clear state."""
        self._stop_typing(chat_id)
        await self._delete_status(chat_id)
        if chat_id in self._draft_messages and chat_id in self._draft_text:
            html = render_telegram_html(self._draft_text[chat_id])
            chunks = split_telegram_html(html)

            # First chunk: edit the existing draft message
            first_ok = False
            try:
                await self._app.bot.edit_message_text(
                    chunks[0],
                    chat_id=chat_id,
                    message_id=self._draft_messages[chat_id],
                    parse_mode="HTML",
                )
                first_ok = True
            except BadRequest as e:
                log.debug("Could not flush draft in chat %d: %s", chat_id, e)
            except Exception:
                log.exception("Failed to flush draft in chat %d", chat_id)

            # Remaining chunks: send as new messages (only if first succeeded)
            for chunk in chunks[1:] if first_ok else []:
                try:
                    await self._app.bot.send_message(
                        chat_id, chunk, parse_mode="HTML",
                        **self._thread_kwargs(thread_id),
                    )
                except Exception:
                    log.exception("Failed to send continuation in chat %d", chat_id)

        self._draft_messages.pop(chat_id, None)
        self._draft_text.pop(chat_id, None)
        self._last_edit.pop(chat_id, None)

    async def _handle_question_callback(self, update: Update, tg_context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline keyboard button press for AskUserQuestion."""
        query = update.callback_query
        if not query or not query.data:
            return
        # Format: ask:{question_id}:{q_index}:{option_index}
        # question_id is "session_name:uuid" where session_name may contain /
        # but not colons, and uuid doesn't contain colons either.
        # So callback_data is "ask:SESSION_NAME:UUID:qi:oi" -> 5 parts when split on ":"
        parts = query.data.split(":")
        if len(parts) < 5:
            return
        question_id = f"{parts[1]}:{parts[2]}"  # reconstruct "session:uuid"
        q_index = int(parts[3])
        option_index = int(parts[4])

        pending = self._pending_questions.get(question_id)
        if not pending:
            await query.answer(text="Question expired")
            return

        questions = pending["questions"]
        if q_index >= len(questions):
            await query.answer(text="Invalid question")
            return

        q = questions[q_index]
        options = q.get("options", [])
        if option_index >= len(options):
            await query.answer(text="Invalid option")
            return

        selected = options[option_index]
        question_text = q.get("question", "")
        pending["answers"][question_text] = selected.get("label", "")

        await query.answer()
        await query.edit_message_text(f"\u2713 {selected.get('label', '')}")

        # Check if all questions are answered
        if len(pending["answers"]) >= len(questions):
            session = pending["session"]
            answers = pending["answers"]
            del self._pending_questions[question_id]
            await self._context.answer_question(session, question_id, answers)
