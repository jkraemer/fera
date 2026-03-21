from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator
from uuid import uuid4

import logging

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, query
from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny, ResultMessage, ToolResultBlock, ToolUseBlock, UserMessage

from fera.agent import agent_plugins, build_allowed_tools, build_continue_hook, build_mcp_servers, build_tool_deny_hook, ensure_dirs, extra_mcp_servers, merge_hooks
from fera.config import DEFAULT_AGENT, FERA_HOME, load_agent_config, resolve_model, workspace_dir
from fera.gateway.lanes import LaneManager
from fera.gateway.memory_writer import COMPACT_NOTIFICATION, MemoryWriter
from fera.gateway.protocol import make_event, strip_silent_suffix
from fera.gateway.sessions import SessionManager
from fera.logger import get_logger
from fera.prompt import truncate_content

log = logging.getLogger(__name__)

RESPONSE_INACTIVITY_TIMEOUT = 300  # 5 minutes
QUESTION_INACTIVITY_TIMEOUT = 86_400  # 24 hours — generous limit for pending AskUserQuestion

MAX_USER_MESSAGE_BYTES = 500_000

_TRUNCATION_NOTE = (
    "[This message was truncated from {original} characters"
    " because it exceeded the maximum message size ({limit} bytes)."
    " The beginning and end are preserved with a gap in the middle.]\n\n"
)


def _truncate_user_message(text: str) -> str:
    """Truncate a user message if it exceeds MAX_USER_MESSAGE_BYTES."""
    if len(text.encode("utf-8")) <= MAX_USER_MESSAGE_BYTES:
        return text
    original_len = len(text)
    truncated = truncate_content(text, MAX_USER_MESSAGE_BYTES)
    note = _TRUNCATION_NOTE.format(original=original_len, limit=MAX_USER_MESSAGE_BYTES)
    return note + truncated

if TYPE_CHECKING:
    from fera.gateway.pool import ClientPool


def translate_message(msg: Any, *, session: str) -> list[dict]:
    """Translate an SDK message into a list of gateway protocol events."""
    events = []

    # SystemMessage — has .subtype and .data, no .content list, no .is_error
    if hasattr(msg, "subtype") and hasattr(msg, "data") and not hasattr(msg, "content") and not hasattr(msg, "is_error"):
        if msg.subtype == "compact_boundary":
            meta = (msg.data or {}).get("compactMetadata", {})
            events.append(make_event("agent.compact", session=session, data={
                "trigger": meta.get("trigger"),
                "pre_tokens": meta.get("preTokens"),
            }))
        return events

    # ResultMessage — signals end of turn
    if hasattr(msg, "is_error") and hasattr(msg, "num_turns"):
        if msg.is_error:
            error_detail = getattr(msg, "result", None) or "Agent turn failed"
            events.append(make_event("agent.error", session=session, data={"error": str(error_detail)}))
        else:
            usage = getattr(msg, "usage", None) or {}
            events.append(make_event("agent.done", session=session, data={
                "duration_ms": getattr(msg, "duration_ms", None),
                "duration_api_ms": getattr(msg, "duration_api_ms", None),
                "model": getattr(msg, "model", None),
                "input_tokens": usage.get("input_tokens") if isinstance(usage, dict) else None,
                "output_tokens": usage.get("output_tokens") if isinstance(usage, dict) else None,
                "cache_creation_input_tokens": usage.get("cache_creation_input_tokens") if isinstance(usage, dict) else None,
                "cache_read_input_tokens": usage.get("cache_read_input_tokens") if isinstance(usage, dict) else None,
                "cost_usd": getattr(msg, "total_cost_usd", None),
                "num_turns": getattr(msg, "num_turns", None),
            }))
        return events

    # Skip UserMessage — only assistant content should produce events (#1379)
    if isinstance(msg, UserMessage):
        return []

    # AssistantMessage — has content blocks
    if hasattr(msg, "content") and isinstance(msg.content, list):
        for block in msg.content:
            if hasattr(block, "text") and not hasattr(block, "thinking"):
                cleaned = strip_silent_suffix(block.text)
                if cleaned:
                    events.append(make_event(
                        "agent.text", session=session, data={
                            "text": cleaned,
                            "model": getattr(msg, "model", None),
                        },
                    ))
            elif hasattr(block, "name") and hasattr(block, "input"):
                events.append(make_event(
                    "agent.tool_use", session=session,
                    data={"id": block.id, "name": block.name, "input": block.input},
                ))
            elif hasattr(block, "tool_use_id") and hasattr(block, "content"):
                events.append(make_event(
                    "agent.tool_result", session=session,
                    data={
                        "tool_use_id": block.tool_use_id,
                        "content": block.content,
                        "is_error": getattr(block, "is_error", False),
                    },
                ))

    return events


class AgentRunner:
    """Manages agent invocations via ClaudeSDKClient."""

    def __init__(
        self,
        sessions: SessionManager,
        lanes: LaneManager,
        pool: ClientPool | None = None,
        memory_url: str = "http://127.0.0.1:8390/sse",
        global_mcp_servers: dict | None = None,
        memory_writer: MemoryWriter | None = None,
        fera_home: Path = FERA_HOME,
        bus: Any | None = None,
        transcript_logger: Any | None = None,
    ):
        self._sessions = sessions
        self._lanes = lanes
        self._pool = pool
        self._memory_url = memory_url
        self._global_mcp_servers: dict = global_mcp_servers or {}
        self._memory_writer = memory_writer
        self._fera_home = fera_home
        self._bus = bus
        self._transcript_logger = transcript_logger
        self._active_clients: dict[str, ClaudeSDKClient] = {}
        self._session_models: dict[str, str] = {}
        self._pending_questions: dict[str, asyncio.Future] = {}
        self._question_events: dict[str, asyncio.Event] = {}

    def active_session(self, session_name: str) -> bool:
        """Check if a session has an active agent turn."""
        return session_name in self._active_clients

    async def interrupt(self, session_name: str) -> None:
        """Interrupt the active agent turn for a session."""
        self.cancel_pending_questions(session_name)
        client = self._active_clients.get(session_name)
        if client:
            await client.interrupt()

    async def interrupt_all(self) -> None:
        """Interrupt all active agent turns."""
        for session_name in list(self._active_clients):
            await self.interrupt(session_name)

    async def answer_question(self, session: str, question_id: str, answers: dict) -> None:
        """Resolve a pending AskUserQuestion Future with the user's answers."""
        fut = self._pending_questions.pop(question_id, None)
        if fut and not fut.done():
            fut.set_result(answers)

    def cancel_pending_questions(self, session: str) -> None:
        """Cancel all pending question Futures for a session."""
        prefix = session + ":"
        to_cancel = [qid for qid in self._pending_questions if qid.startswith(prefix)]
        for qid in to_cancel:
            fut = self._pending_questions.pop(qid)
            if not fut.done():
                fut.cancel()
        self._question_events.pop(session, None)

    def _has_pending_questions(self, session_name: str) -> bool:
        """Check if any AskUserQuestion Futures are pending for a session."""
        prefix = session_name + ":"
        return any(qid.startswith(prefix) for qid in self._pending_questions)

    def _question_event(self, session_name: str) -> asyncio.Event:
        """Get or create an asyncio.Event for question-registered signals."""
        if session_name not in self._question_events:
            self._question_events[session_name] = asyncio.Event()
        return self._question_events[session_name]

    def _build_can_use_tool(self, session_name: str):
        """Build a can_use_tool callback for a session.

        For AskUserQuestion: publishes an ``agent.user_question`` event on the
        bus and awaits a Future that an adapter resolves via answer_question().
        For all other tools: auto-approves (we run in bypassPermissions mode).
        """
        async def _can_use_tool(tool_name, input_data, context):
            if tool_name != "AskUserQuestion":
                return PermissionResultAllow(updated_input=input_data)

            question_id = f"{session_name}:{uuid4()}"
            fut = asyncio.get_event_loop().create_future()
            self._pending_questions[question_id] = fut
            self._question_event(session_name).set()

            if self._bus:
                await self._bus.publish(make_event(
                    "agent.user_question",
                    session=session_name,
                    data={
                        "question_id": question_id,
                        "questions": input_data.get("questions", []),
                    },
                ))

            try:
                answers = await fut
            except asyncio.CancelledError:
                return PermissionResultDeny(message="Question cancelled (session cleared)")

            return PermissionResultAllow(updated_input={
                "questions": input_data.get("questions", []),
                "answers": answers,
            })

        return _can_use_tool

    async def set_model(self, session_name: str, model: str) -> None:
        """Switch the model for a session.

        Stores the override so the next turn picks it up, and also applies
        it immediately if a pooled client already exists.
        """
        resolved = resolve_model(model, self._fera_home)
        self._session_models[session_name] = resolved
        client = self._active_clients.get(session_name)
        if client is None and self._pool:
            client = self._pool._clients.get(session_name)
        if client is not None:
            await client.set_model(resolved)

    async def clear_session(self, session_name: str) -> None:
        """Clear a session's SDK state — the next turn will start fresh.

        Removes the stored SDK session ID so the runner won't attempt to resume
        the old conversation. Also releases the pool client if pooling is enabled,
        so it doesn't try to resume on the next pooled turn.
        """
        self.cancel_pending_questions(session_name)
        self._sessions.clear_sdk_session_id(session_name)
        if self._pool:
            await self._pool.release(session_name)

    async def deactivate_session(self, session_name: str) -> None:
        """Disconnect the pool client without clearing the SDK session ID.

        The session metadata (including sdk_session_id) is preserved so
        the next run_turn will resume the conversation transparently.
        """
        if self._pool:
            await self._pool.release(session_name)

    def _extra_servers(self, agent_name: str) -> dict:
        """Merge global and per-agent MCP servers, applying env var substitution."""
        return extra_mcp_servers(agent_name, self._global_mcp_servers)

    async def _drain_leftover(self, client, session_name: str) -> int:
        """Drain leftover messages from a pooled client's pipe.

        Background task notifications in the SDK can create mini-turns
        after _drain_response returns.  Their messages accumulate in the
        persistent subprocess pipe and would corrupt subsequent turns
        if not drained.

        Uses a short timeout at turn boundaries (after a ResultMessage or
        at the start) and a longer timeout mid-notification-turn to let
        the agent finish responding to the notification.
        """
        count = 0
        saw_result = True  # treat start as "at boundary" → short timeout
        gen = client.receive_messages().__aiter__()
        try:
            while True:
                timeout = 0.05 if saw_result else 30.0
                try:
                    msg = await asyncio.wait_for(gen.__anext__(), timeout=timeout)
                    count += 1
                    saw_result = isinstance(msg, ResultMessage)
                except asyncio.TimeoutError:
                    break
                except StopAsyncIteration:
                    break
        except Exception:
            log.warning(
                "Error draining leftover messages from %s",
                session_name, exc_info=True,
            )
        finally:
            try:
                await gen.aclose()
            except Exception:
                pass
        if count:
            log.warning(
                "Drained %d leftover message(s) from session %s pipe",
                count, session_name,
            )
        return count

    async def _wait_for_message_or_question(
        self, msg_iter, session_name: str, inactivity_timeout: float,
    ):
        """Wait for the next SDK message, re-evaluating timeout if a question arrives.

        Races the message iterator against the session's question event.
        If the event fires (meaning _can_use_tool just registered a question),
        clears the event and restarts the wait with QUESTION_INACTIVITY_TIMEOUT.
        """
        question_ev = self._question_event(session_name)

        # Fast path: question already pending
        if self._has_pending_questions(session_name):
            return await asyncio.wait_for(
                msg_iter.__anext__(), timeout=QUESTION_INACTIVITY_TIMEOUT,
            )

        # Race: wait for either the next message or the question event
        msg_task = asyncio.ensure_future(msg_iter.__anext__())
        event_task = asyncio.ensure_future(question_ev.wait())

        try:
            done, _ = await asyncio.wait(
                {msg_task, event_task},
                timeout=inactivity_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        except BaseException:
            msg_task.cancel()
            event_task.cancel()
            raise

        if not done:
            # Neither completed — true inactivity timeout
            msg_task.cancel()
            event_task.cancel()
            # Await cancellation so the async generator is released before aclose()
            try:
                await msg_task
            except (asyncio.CancelledError, StopAsyncIteration):
                pass
            raise asyncio.TimeoutError()

        if msg_task in done:
            event_task.cancel()
            return msg_task.result()

        # Question event fired — clear it and wait for the message with long timeout
        event_task.cancel()
        question_ev.clear()
        try:
            return await asyncio.wait_for(
                msg_task, timeout=QUESTION_INACTIVITY_TIMEOUT,
            )
        except BaseException:
            msg_task.cancel()
            raise

    async def _drain_response(
        self, client, session_name: str, canary_token: str | None = None,
        inactivity_timeout: float = RESPONSE_INACTIVITY_TIMEOUT,
    ) -> AsyncIterator[dict]:
        """Drain a client's response stream, yielding protocol events.

        Uses receive_messages() with tool-use tracking to correctly stop
        only at the top-level ResultMessage.  Sub-agent tool invocations
        (Task tool etc.) emit their own intermediate ResultMessage; we
        must continue past those to capture the main agent's final output.

        When *canary_token* is set, scans agent text output for the token.
        On match: yields ``agent.alert``, interrupts the client, and stops.
        A rolling buffer catches tokens split across consecutive chunks.
        """
        import hashlib

        pending_tools: set[str] = set()
        # Rolling tail buffer for cross-chunk canary detection
        tail_buf = ""
        buf_len = (len(canary_token) - 1) if canary_token else 0

        msg_iter = client.receive_messages().__aiter__()
        try:
            while True:
                try:
                    msg = await self._wait_for_message_or_question(
                        msg_iter, session_name, inactivity_timeout,
                    )
                except StopAsyncIteration:
                    return
                except asyncio.TimeoutError:
                    log.error(
                        "No response from SDK for %s in %.0fs — assuming reader is dead",
                        session_name, inactivity_timeout,
                    )
                    raise

                if hasattr(msg, "session_id"):
                    self._sessions.set_sdk_session_id(session_name, msg.session_id)

                # Track tool use nesting to distinguish sub-agent results
                if hasattr(msg, "content") and isinstance(msg.content, list):
                    for block in msg.content:
                        if isinstance(block, ToolUseBlock):
                            pending_tools.add(block.id)
                        elif isinstance(block, ToolResultBlock):
                            pending_tools.discard(block.tool_use_id)

                if isinstance(msg, ResultMessage):
                    if not pending_tools:
                        for event in translate_message(msg, session=session_name):
                            yield event
                        return
                    # Sub-agent result — suppress to avoid spurious agent.done
                    continue

                events = translate_message(msg, session=session_name)

                # Canary scanning on text events
                if canary_token:
                    for event in events:
                        if event["event"] == "agent.text":
                            text = event["data"].get("text", "")
                            search_text = tail_buf + text
                            if canary_token in search_text:
                                token_hash = hashlib.sha256(canary_token.encode()).hexdigest()[:12]
                                log.critical(
                                    "Canary token detected in session %s (hash=%s)",
                                    session_name, token_hash,
                                )
                                yield event  # yield the text that triggered it
                                yield make_event(
                                    "agent.alert", session=session_name, data={
                                        "message": "System prompt exfiltration detected",
                                        "severity": "critical",
                                    },
                                )
                                await client.interrupt()
                                return
                            # Update rolling buffer
                            tail_buf = text[-buf_len:] if buf_len else ""

                for event in events:
                    yield event
        finally:
            await msg_iter.aclose()

    async def run_turn(
        self, session_name: str, text: str, source: str = "",
        prompt_mode: str = "full", model: str | None = None,
        allowed_tools: list[str] | None = None,
        agents: dict | None = None,
        fork_from: str | None = None,
    ) -> AsyncIterator[dict]:
        """Run an agent turn, yielding protocol events as they arrive.

        If the session lane is already busy (another turn is running),
        the message is queued and a ``message.queued`` event is yielded.
        The holder of the lane drains the queue after its turn and runs
        a combined follow-up turn with all queued messages joined by
        newlines.

        When *fork_from* is a qualified session name, the first turn forks
        from that session's SDK conversation history.
        """
        # Queue instead of blocking when another turn is in progress
        if self._lanes.is_locked(session_name):
            self._lanes.enqueue(session_name, text, source)
            yield make_event(
                "message.queued", session=session_name,
                data={"text": text},
            )
            return

        async with self._lanes.acquire(session_name):
            async for event in self._execute_turn(
                session_name, text, source,
                prompt_mode=prompt_mode, model=model,
                allowed_tools=allowed_tools,
                agents=agents,
                fork_from=fork_from,
            ):
                yield event

            # Drain queued messages and run combined follow-up turns
            while queued := self._lanes.drain_queue(session_name):
                combined = "\n".join(t for t, _ in queued)
                q_source = queued[0][1]
                log.info(
                    "Processing %d queued message(s) for %s",
                    len(queued), session_name,
                )
                async for event in self._execute_turn(
                    session_name, combined, q_source,
                    prompt_mode=prompt_mode, model=model,
                    allowed_tools=allowed_tools,
                    agents=agents,
                ):
                    yield event

    async def _execute_turn(
        self, session_name: str, text: str, source: str = "",
        prompt_mode: str = "full", model: str | None = None,
        allowed_tools: list[str] | None = None,
        agents: dict | None = None,
        fork_from: str | None = None,
    ) -> AsyncIterator[dict]:
        """Execute a single agent turn (called with the lane already held)."""
        from claude_agent_sdk._errors import ProcessError

        session_info = self._sessions.get_or_create(session_name)
        sdk_session_id = session_info.get("sdk_session_id")
        agent_name = session_info.get("agent", DEFAULT_AGENT)

        # Fork: on first turn of a new session, copy history from parent
        do_fork = False
        if fork_from and not sdk_session_id:
            parent_info = self._sessions.get(fork_from)
            parent_sdk_id = parent_info.get("sdk_session_id") if parent_info else None
            if parent_sdk_id:
                sdk_session_id = parent_sdk_id
                do_fork = True
                log.info("Forking session %s from %s", session_name, fork_from)

        ensure_dirs(agent_name, self._fera_home)

        text = _truncate_user_message(text)

        if sdk_session_id:
            if logger := get_logger():
                await logger.log("session.resumed", session=session_name, sdk_session_id=sdk_session_id)

        if logger := get_logger():
            await logger.log("turn.started", session=session_name, source=source)

        # Shared state across both the first attempt and the retry loop
        tool_calls: dict[str, tuple[str, float]] = {}  # tool_use_id -> (name, start_time)
        turn_model: list[str] = []  # last model seen in agent.text events

        async def _log_event(event: dict) -> None:
            if not (logger := get_logger()):
                return
            evt = event["event"]
            if evt == "agent.tool_use":
                tool_id = event["data"]["id"]
                tool_name = event["data"]["name"]
                tool_input = event["data"].get("input") or {}
                tool_calls[tool_id] = (tool_name, time.monotonic())
                extra: dict[str, str] = {}
                if tool_name == "WebFetch" and "url" in tool_input:
                    extra["url"] = tool_input["url"]
                elif tool_name == "WebSearch" and "query" in tool_input:
                    extra["query"] = tool_input["query"]
                elif tool_name in ("Read", "Write") and "file_path" in tool_input:
                    extra["path"] = tool_input["file_path"]
                elif tool_name == "Skill" and "skill" in tool_input:
                    extra["skill"] = tool_input["skill"]
                elif tool_name == "Bash" and "command" in tool_input:
                    cmd = tool_input["command"]
                    extra["command"] = cmd
                    summary = cmd.splitlines()[0][:200]
                    log.info("[%s] Bash: %s", session_name, summary)
                await logger.log(
                    "tool.call", session=session_name,
                    tool_name=tool_name,
                    input_size=len(json.dumps(tool_input)),
                    **extra,
                )
            elif evt == "agent.tool_result":
                tool_id = event["data"]["tool_use_id"]
                info = tool_calls.pop(tool_id, None)
                await logger.log(
                    "tool.result", session=session_name,
                    tool_name=info[0] if info else "unknown",
                    duration_ms=int((time.monotonic() - info[1]) * 1000) if info else None,
                    is_error=event["data"].get("is_error", False),
                )
            elif evt == "agent.text":
                model = event["data"].get("model")
                if model:
                    turn_model.clear()
                    turn_model.append(model)
            elif evt == "agent.done":
                await logger.log(
                    "turn.completed", session=session_name,
                    duration_ms=event["data"].get("duration_ms"),
                    model=turn_model[-1] if turn_model else event["data"].get("model"),
                    input_tokens=event["data"].get("input_tokens"),
                    cache_creation_tokens=event["data"].get("cache_creation_input_tokens"),
                    cache_read_tokens=event["data"].get("cache_read_input_tokens"),
                    output_tokens=event["data"].get("output_tokens"),
                )

        try:
            if self._pool:
                gen = self._run_turn_pooled(session_name, text, sdk_session_id, model=model, fork_session=do_fork)
            else:
                gen = self._run_turn_ephemeral(session_name, text, sdk_session_id, prompt_mode=prompt_mode, model=model, allowed_tools=allowed_tools, agents=agents, fork_session=do_fork)

            async for event in gen:
                if source:
                    event["turn_source"] = source
                await _log_event(event)
                yield event
        except ProcessError:
            if not sdk_session_id:
                raise
            log.warning(
                "Session %s resume failed, clearing stale session ID and retrying",
                session_name,
            )
            self._sessions.clear_sdk_session_id(session_name)

            if self._pool:
                gen = self._run_turn_pooled(session_name, text, None, model=model)
            else:
                gen = self._run_turn_ephemeral(session_name, text, None, prompt_mode=prompt_mode, model=model, allowed_tools=allowed_tools, agents=agents)

            async for event in gen:
                if source:
                    event["turn_source"] = source
                await _log_event(event)
                yield event
        except Exception as e:
            if logger := get_logger():
                await logger.log("turn.error", level="error", session=session_name, error=str(e))
            raise

    async def _run_turn_pooled(
        self, session_name: str, text: str, sdk_session_id: str | None,
        model: str | None = None,
        fork_session: bool = False,
    ) -> AsyncIterator[dict]:
        """Run a turn using a pooled client."""
        session_info = self._sessions.get(session_name) or {}
        canary_token = f"CANARY:{session_info['canary_token']}" if session_info.get("canary_token") else None
        client = await self._pool.acquire(
            session_name, sdk_session_id=sdk_session_id,
            fork_session=fork_session,
        )
        self._active_clients[session_name] = client
        self._pool.mark_active(session_name)
        try:
            effective_model = model or self._session_models.get(session_name)
            if effective_model:
                resolved = resolve_model(effective_model, self._fera_home)
                await client.set_model(resolved)
            await self._drain_leftover(client, session_name)
            await client.query(text)
            async for event in self._drain_response(client, session_name, canary_token=canary_token):
                yield event
        except Exception:
            self.cancel_pending_questions(session_name)
            await self._pool.release(session_name)
            raise
        finally:
            self._pool.mark_idle(session_name)
            self._active_clients.pop(session_name, None)

    async def _run_turn_ephemeral(
        self, session_name: str, text: str, sdk_session_id: str | None,
        prompt_mode: str = "full", model: str | None = None,
        allowed_tools: list[str] | None = None,
        agents: dict | None = None,
        fork_session: bool = False,
    ) -> AsyncIterator[dict]:
        """Run a turn with a fresh per-turn client."""
        session_info = self._sessions.get(session_name) or {}
        agent_name = session_info.get("agent", DEFAULT_AGENT)
        ws_dir = Path(session_info["workspace_dir"])
        canary_token = f"CANARY:{session_info['canary_token']}" if session_info.get("canary_token") else None
        agent_cfg = load_agent_config(agent_name, fera_home=self._fera_home)
        resolved_model = resolve_model(
            model or agent_cfg.get("model"), self._fera_home
        )
        extra = self._extra_servers(agent_name)
        from fera.prompt import SystemPromptBuilder
        plugins = agent_plugins(agent_name, ws_dir)
        mcp = build_mcp_servers(self._memory_url, agent_name, extra=extra)
        if allowed_tools is not None:
            tools = allowed_tools
        else:
            tools = build_allowed_tools(
                extra_servers=extra,
                agent_allowed=agent_cfg.get("allowed_tools"),
                agent_disabled=agent_cfg.get("disabled_tools"),
            )
        log.info(
            "Ephemeral client for %s [agent=%s] mcp=[%s] tools=[%s]",
            session_name, agent_name, ", ".join(mcp), ", ".join(tools),
        )
        options = ClaudeAgentOptions(
            system_prompt=SystemPromptBuilder(ws_dir).build(prompt_mode, canary_token=canary_token),
            model=resolved_model,
            mcp_servers=mcp,
            allowed_tools=tools,
            permission_mode="bypassPermissions",
            cwd=str(ws_dir),
            setting_sources=["user", "project"],
            stderr=lambda line: log.warning("agent stderr: %s", line),
            can_use_tool=self._build_can_use_tool(session_name) if self._bus else None,
            **({"agents": agents} if agents else {}),
            **({"plugins": plugins} if plugins else {}),
        )
        deny_hook = build_tool_deny_hook(agent_cfg.get("disabled_tools"))
        compact_hook = None
        if self._memory_writer:
            from claude_agent_sdk import HookMatcher

            on_compact = None
            if self._bus:
                async def _notify_compact():
                    await self._bus.publish(make_event(
                        "agent.text", session=session_name,
                        data={"text": COMPACT_NOTIFICATION},
                    ))
                on_compact = _notify_compact

            tp = str(self._transcript_logger.transcript_path(session_name)) if self._transcript_logger else None
            compact_hook = {
                "PreCompact": [HookMatcher(hooks=[self._memory_writer.make_pre_compact_hook(
                    on_compact=on_compact,
                    transcript_path=tp,
                )])],
            }
        continue_hook = build_continue_hook()
        hooks = merge_hooks(deny_hook, compact_hook, continue_hook)
        if hooks:
            options.hooks = hooks
        if sdk_session_id:
            options.resume = sdk_session_id
            if fork_session:
                options.fork_session = True

        async with ClaudeSDKClient(options) as client:
            self._active_clients[session_name] = client
            try:
                await client.query(text)
                async for event in self._drain_response(client, session_name, canary_token=canary_token):
                    yield event
            finally:
                self.cancel_pending_questions(session_name)
                self._active_clients.pop(session_name, None)

    async def run_oneshot(
        self, text: str, *,
        agent_name: str = DEFAULT_AGENT,
        prompt_mode: str = "minimal",
        model: str | None = None,
        allowed_tools: list[str] | None = None,
    ) -> AsyncIterator[dict]:
        """Run a one-shot query with no session state.

        Uses the SDK's query() function — no session creation, no pool
        interaction, no resume. Ideal for ephemeral jobs like cron.
        """
        ensure_dirs(agent_name, self._fera_home)
        agent_cfg = load_agent_config(agent_name, fera_home=self._fera_home)
        resolved_model = resolve_model(
            model or agent_cfg.get("model"), self._fera_home
        )
        ws_dir = workspace_dir(agent_name, self._fera_home)
        extra = self._extra_servers(agent_name)
        mcp = build_mcp_servers(self._memory_url, agent_name, extra=extra)
        if allowed_tools is not None:
            tools = allowed_tools
        else:
            tools = build_allowed_tools(
                extra_servers=extra,
                agent_allowed=agent_cfg.get("allowed_tools"),
                agent_disabled=agent_cfg.get("disabled_tools"),
            )
        from fera.prompt import SystemPromptBuilder
        options = ClaudeAgentOptions(
            system_prompt=SystemPromptBuilder(ws_dir).build(prompt_mode),
            model=resolved_model,
            mcp_servers=mcp,
            allowed_tools=tools,
            permission_mode="bypassPermissions",
            cwd=str(ws_dir),
            setting_sources=["user", "project"],
            stderr=lambda line: log.warning("oneshot stderr: %s", line),
        )
        deny_hook = build_tool_deny_hook(agent_cfg.get("disabled_tools"))
        if deny_hook:
            options.hooks = deny_hook
        async for msg in query(prompt=text, options=options):
            for event in translate_message(msg, session=""):
                yield event
