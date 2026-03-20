from __future__ import annotations

import asyncio
import hmac
import json
import logging
import traceback
from pathlib import Path

import websockets
from websockets.asyncio.server import ServerConnection

from fera.adapters.base import ChannelAdapter
from fera.adapters.bus import EventBus
from fera.agent import (
    agent_plugins,
    build_allowed_tools,
    build_continue_hook,
    build_mcp_servers,
    build_tool_deny_hook,
    ensure_dirs,
    extra_mcp_servers,
    merge_hooks,
)
from fera.config import (
    DEFAULT_AGENT,
    DEFAULT_CONFIG,
    FERA_HOME,
    load_cron_config,
    substitute_env_vars,
    workspace_dir,
)
from fera.sanitize import set_alert_handler, InjectionMatch
from fera.gateway.dream_cycle import DreamCycleScheduler
from fera.gateway.heartbeat import HeartbeatScheduler
from fera.gateway.lanes import LaneManager
from fera.gateway.memory_writer import MemoryWriter
from fera.gateway.metrics import MetricsCollector
from fera.gateway.transcript import TranscriptLogger
from fera.gateway.pool import ClientPool
from fera.gateway.protocol import make_response, make_event, parse_frame
from fera.gateway.mcp import GatewayMcpManager
from fera.gateway.runner import AgentRunner
from fera.gateway.sessions import SessionManager
from fera.gateway.stats import SessionStats
from fera.logger import get_logger, init_logger
from fera.render import render_html

log = logging.getLogger(__name__)


class Gateway:
    """WebSocket gateway server."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8389,
        fera_home: Path | None = None,
        pool_config: dict | None = None,
        memory_url: str = "http://127.0.0.1:8390/sse",
        global_mcp_servers: dict | None = None,
        auth_token: str | None = None,
        auth_timeout: float = 5.0,
        adapters: list[ChannelAdapter] | None = None,
        log_dir: Path | None = None,
        heartbeat_config: dict | None = None,
        dream_cycle_config: dict | None = None,
        timezone: str | None = None,
        archivist_model: str | None = None,
        alert_session: str | None = None,
        metrics_config: dict | None = None,
    ):
        self._host = host
        self._port = port
        self._fera_home = fera_home or FERA_HOME
        self._log_dir = log_dir or Path.home() / "logs"
        self._memory_url = memory_url
        self._global_mcp_servers: dict = global_mcp_servers or {}
        self._auth_token = auth_token
        self._auth_timeout = auth_timeout
        self._alert_session = alert_session
        self._authenticated: set[ServerConnection] = set()
        self._auth_timers: dict[ServerConnection, asyncio.Task] = {}
        self._sessions = SessionManager(
            self._fera_home / "data" / "sessions.json", fera_home=self._fera_home
        )
        self._lanes = LaneManager()

        if pool_config is not None:
            idle_minutes = pool_config.get("idle_timeout_minutes", 30)
            max_age_hours = pool_config.get("max_age_hours", 10)
            max_age_jitter_hours = pool_config.get("max_age_jitter_hours", 2)
            self._pool = ClientPool(
                factory=self._make_client,
                max_clients=pool_config.get("max_clients", 5),
                idle_timeout=idle_minutes * 60,
                max_age=max_age_hours * 3600,
                max_age_jitter=max_age_jitter_hours * 3600,
            )
        else:
            self._pool = None

        from fera.config import resolve_model

        resolved_archivist = resolve_model(archivist_model, self._fera_home)
        self._memory_writer = MemoryWriter(tz=timezone, model=resolved_archivist)
        self._mcp = GatewayMcpManager()
        self._bus = EventBus()
        metrics_cfg = metrics_config or {}
        self._metrics = MetricsCollector(
            self._fera_home / "data" / "metrics.db",
            retention_days=metrics_cfg.get("retention_days", 365),
        )
        self._transcript = TranscriptLogger(self._fera_home / "data" / "transcripts")
        self._runner = AgentRunner(
            self._sessions,
            self._lanes,
            pool=self._pool,
            memory_url=self._memory_url,
            global_mcp_servers=self._global_mcp_servers,
            memory_writer=self._memory_writer,
            fera_home=self._fera_home,
            bus=self._bus,
            transcript_logger=self._transcript,
        )
        self._adapters: list[ChannelAdapter] = adapters or []
        self._clients: set[ServerConnection] = set()
        self._chat_tasks: set[asyncio.Task] = set()
        self._server = None
        self._stats = SessionStats()
        self._heartbeat_config = {
            **(heartbeat_config or DEFAULT_CONFIG["heartbeat"]),
            "timezone": timezone,
        }
        heartbeat_cfg = self._heartbeat_config
        hb_session_name = heartbeat_cfg.get("session", f"{DEFAULT_AGENT}/default")
        hb_agent = (
            hb_session_name.split("/")[0] if "/" in hb_session_name else DEFAULT_AGENT
        )
        self._heartbeat = HeartbeatScheduler(
            config=heartbeat_cfg,
            runner=self._runner,
            bus=self._bus,
            lanes=self._lanes,
            workspace=workspace_dir(hb_agent, self._fera_home),
            sessions=self._sessions,
        )
        dream_cycle_cfg = {
            **(dream_cycle_config or DEFAULT_CONFIG["dream_cycle"]),
            "timezone": timezone,
        }
        self._dream_cycle = DreamCycleScheduler(
            config=dream_cycle_cfg,
            runner=self._runner,
            sessions=self._sessions,
            memory_writer=self._memory_writer,
            lanes=self._lanes,
            fera_home=self._fera_home,
            transcript_logger=self._transcript,
        )

    async def _make_client(self, session_name: str, sdk_session_id: str | None = None, fork_session: bool = False):
        """Factory for the client pool — creates and connects a ClaudeSDKClient."""
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        session_info = self._sessions.get(session_name) or {}
        agent_name = session_info.get("agent", DEFAULT_AGENT)
        ensure_dirs(agent_name, self._fera_home)
        ws_dir = workspace_dir(agent_name, self._fera_home)
        extra = extra_mcp_servers(agent_name, self._global_mcp_servers)
        from fera.config import load_agent_config, resolve_model

        agent_cfg = load_agent_config(agent_name, fera_home=self._fera_home)
        from fera.prompt import SystemPromptBuilder

        plugins = agent_plugins(agent_name, ws_dir)
        mcp = build_mcp_servers(self._memory_url, agent_name, extra=extra)
        tools = build_allowed_tools(
            extra_servers=extra,
            agent_allowed=agent_cfg.get("allowed_tools"),
            agent_disabled=agent_cfg.get("disabled_tools"),
        )
        resolved_model = resolve_model(agent_cfg.get("model"), self._fera_home)
        log.info(
            "New pooled client for %s [agent=%s] mcp=[%s] tools=[%s]",
            session_name,
            agent_name,
            ", ".join(mcp),
            ", ".join(tools),
        )
        options = ClaudeAgentOptions(
            model=resolved_model,
            system_prompt=SystemPromptBuilder(ws_dir).build("full"),
            mcp_servers=mcp,
            allowed_tools=tools,
            can_use_tool=self._runner._build_can_use_tool(session_name),
            permission_mode="bypassPermissions",
            cwd=str(ws_dir),
            setting_sources=["user", "project"],
            stderr=lambda line: log.warning("agent stderr: %s", line),
            **({"plugins": plugins} if plugins else {}),
        )
        deny_hook = build_tool_deny_hook(agent_cfg.get("disabled_tools"))
        compact_hook = None
        if self._memory_writer:
            from claude_agent_sdk import HookMatcher
            from fera.gateway.memory_writer import COMPACT_NOTIFICATION
            from fera.gateway.protocol import make_event

            async def _notify_compact():
                await self._bus.publish(
                    make_event(
                        "agent.text",
                        session=session_name,
                        data={"text": COMPACT_NOTIFICATION},
                    )
                )

            compact_hook = {
                "PreCompact": [
                    HookMatcher(
                        hooks=[
                            self._memory_writer.make_pre_compact_hook(
                                on_compact=_notify_compact,
                                transcript_path=str(
                                    self._transcript.transcript_path(session_name)
                                ),
                            )
                        ]
                    )
                ],
            }
        continue_hook = build_continue_hook()
        hooks = merge_hooks(deny_hook, compact_hook, continue_hook)
        if hooks:
            options.hooks = hooks
        if sdk_session_id:
            options.resume = sdk_session_id
            if fork_session:
                options.fork_session = True
        client = ClaudeSDKClient(options)
        await client.connect()
        return client

    def _workspace_dir_for(self, params: dict) -> Path:
        """Return the workspace directory for the agent specified in params."""
        agent = params.get("agent", DEFAULT_AGENT)
        if not agent or "/" in agent or ".." in agent:
            raise ValueError(f"Invalid agent name: {agent!r}")
        return self._fera_home / "agents" / agent / "workspace"

    @property
    def port(self) -> int:
        """Actual port (useful when started with port=0)."""
        if self._server and self._server.sockets:
            return self._server.sockets[0].getsockname()[1]
        return self._port

    async def start(self) -> None:
        agents_dir = self._fera_home / "agents"
        if agents_dir.exists():
            for agent_dir in sorted(agents_dir.iterdir()):
                if agent_dir.is_dir() and (agent_dir / "workspace").is_dir():
                    ensure_dirs(agent_dir.name, self._fera_home)
        else:
            ensure_dirs(DEFAULT_AGENT, self._fera_home)
        logger = init_logger(self._log_dir)

        async def _log_broadcast(entry: dict) -> None:
            await self._bus.publish(
                make_event("log.entry", session="$system", data=entry)
            )

        logger.set_broadcast(_log_broadcast)

        # Wire injection alert handler
        if self._alert_session:

            async def _on_injection_alert(
                source: str,
                excerpt: str,
                matches: list[InjectionMatch],
            ) -> None:
                await self._bus.publish(
                    make_event(
                        "security.alert",
                        session=self._alert_session,
                        data={
                            "source": source,
                            "patterns": [m.pattern_name for m in matches],
                            "excerpt": excerpt,
                        },
                    )
                )

            set_alert_handler(_on_injection_alert)
            log.info(
                "Injection alerts will publish to session: %s", self._alert_session
            )

        self._bus.subscribe("*", self._transcript.record_event)
        self._bus.subscribe("*", self._stats_subscriber)
        self._bus.subscribe("*", self._metrics.record)
        self._metrics.prune()
        await logger.log("system.startup", version="0.1.0")

        if self._pool:
            self._pool.start_reaper()
        self._server = await websockets.serve(
            self._handle_connection,
            self._host,
            self._port,
        )
        if self._adapters:
            from fera.adapters.base import AdapterContext

            for adapter in self._adapters:
                ctx = AdapterContext(
                    bus=self._bus,
                    runner=self._runner,
                    sessions=self._sessions,
                    stats=self._stats,
                    agent_name=adapter.agent_name,
                    memory_writer=self._memory_writer,
                    transcript_logger=self._transcript,
                    fera_home=self._fera_home,
                )
                try:
                    await adapter.start(ctx)
                    log.info("Started adapter: %s", adapter.name)
                    if logger := get_logger():
                        await logger.log(
                            "adapter.started",
                            adapter=adapter.name,
                            agent=adapter.agent_name,
                            type=type(adapter).__name__,
                        )
                except Exception as e:
                    log.exception("Failed to start adapter: %s", adapter.name)
                    if logger := get_logger():
                        await logger.log(
                            "adapter.error",
                            level="error",
                            adapter=adapter.name,
                            error=str(e),
                        )
        self._heartbeat.start()
        self._dream_cycle.start()

    async def stop(
        self,
        drain_timeout: float = 30.0,
        interrupt_timeout: float = 5.0,
    ) -> None:
        if logger := get_logger():
            await logger.log("system.shutdown", reason="signal")
        await self._heartbeat.stop()
        await self._dream_cycle.stop()
        self._metrics.close()
        # Phase 0: stop accepting new work
        if self._server:
            self._server.close()
        for adapter in self._adapters:
            try:
                await adapter.stop()
                if logger := get_logger():
                    await logger.log("adapter.stopped", adapter=adapter.name)
            except Exception as e:
                log.exception("Failed to stop adapter: %s", adapter.name)
                if logger := get_logger():
                    await logger.log(
                        "adapter.error",
                        level="error",
                        adapter=adapter.name,
                        error=str(e),
                    )

        # Phase 1: drain — let in-flight turns finish
        if self._chat_tasks:
            log.info("Draining %d in-flight turn(s)…", len(self._chat_tasks))
            _, pending = await asyncio.wait(
                self._chat_tasks,
                timeout=drain_timeout,
            )

            # Phase 2: interrupt — ask active agents to wrap up
            if pending:
                log.info(
                    "Interrupting %d turn(s) still running after drain",
                    len(pending),
                )
                await self._runner.interrupt_all()
                _, still_pending = await asyncio.wait(
                    pending,
                    timeout=interrupt_timeout,
                )

                # Phase 3: force-cancel anything left
                if still_pending:
                    log.warning(
                        "Force-cancelling %d turn(s)",
                        len(still_pending),
                    )
                    for task in still_pending:
                        task.cancel()
                    await asyncio.gather(
                        *still_pending,
                        return_exceptions=True,
                    )

        # Cleanup
        if self._pool:
            await self._pool.shutdown()
        if self._server:
            await self._server.wait_closed()

    async def _handle_connection(self, ws: ServerConnection) -> None:
        self._clients.add(ws)
        remote = ws.remote_address
        remote_addr = f"{remote[0]}:{remote[1]}" if remote else "unknown"
        if logger := get_logger():
            await logger.log("client.connected", remote_addr=remote_addr)

        async def _ws_event_callback(event, ws=ws):
            try:
                if event.get("event") == "agent.text":
                    text = event.get("data", {}).get("text", "")
                    if text:
                        event = dict(event)
                        event["data"] = dict(event["data"])
                        event["data"]["html"] = render_html(text)
                await ws.send(json.dumps(event))
            except websockets.ConnectionClosed:
                self._clients.discard(ws)

        self._bus.subscribe("*", _ws_event_callback)

        if self._auth_token:
            timer = asyncio.create_task(self._auth_timeout_task(ws))
            self._auth_timers[ws] = timer
        try:
            async for raw in ws:
                frame = parse_frame(raw)
                if frame is None:
                    continue
                if frame["type"] == "req":
                    response = await self._handle_request(frame, ws)
                    await ws.send(json.dumps(response))
        except websockets.ConnectionClosed:
            pass
        finally:
            if logger := get_logger():
                await logger.log("client.disconnected", remote_addr=remote_addr)
            self._bus.unsubscribe("*", _ws_event_callback)
            self._clients.discard(ws)
            self._authenticated.discard(ws)
            timer = self._auth_timers.pop(ws, None)
            if timer and not timer.done():
                timer.cancel()

    async def _handle_request(self, frame: dict, ws: ServerConnection) -> dict:
        method = frame.get("method", "")
        params = frame.get("params", {})
        request_id = frame["id"]

        if self._auth_token and ws not in self._authenticated and method != "connect":
            return make_response(request_id, error="Authentication required")

        try:
            if method == "connect":
                return self._handle_connect(request_id, params, ws)
            elif method == "session.list":
                return self._handle_session_list(request_id)
            elif method == "session.create":
                return await self._handle_session_create(request_id, params)
            elif method == "session.deactivate":
                return await self._handle_session_deactivate(request_id, params)
            elif method == "session.delete":
                return await self._handle_session_delete(request_id, params)
            elif method == "chat.send":
                # Acknowledge immediately, then stream events async
                task = asyncio.create_task(self._handle_chat(params, ws))
                self._chat_tasks.add(task)
                task.add_done_callback(self._chat_tasks.discard)
                return make_response(request_id)
            elif method == "chat.interrupt":
                return await self._handle_interrupt(request_id, params)
            elif method == "workspace.list":
                return self._handle_workspace_list(request_id, params)
            elif method == "workspace.get":
                return self._handle_workspace_get(request_id, params)
            elif method == "workspace.set":
                return self._handle_workspace_set(request_id, params)
            elif method == "mcp.list":
                return self._handle_mcp_list(request_id)
            elif method == "logs.list":
                return self._handle_logs_list(request_id)
            elif method == "logs.read":
                return self._handle_logs_read(request_id, params)
            elif method == "session.history":
                return await self._handle_session_history(request_id, params)
            elif method == "session.stats":
                return self._handle_session_stats(request_id, params)
            elif method == "cron.run":
                return await self._handle_cron_run(request_id, params)
            elif method == "agents.list":
                return self._handle_agents_list(request_id)
            elif method == "question.answer":
                return await self._handle_question_answer(request_id, params)
            elif method == "status.summary":
                return self._handle_status_summary(request_id)
            elif method == "status.metrics":
                return self._handle_status_metrics(request_id, params)
            else:
                return make_response(request_id, error=f"Unknown method: {method}")
        except Exception as e:
            if logger := get_logger():
                await logger.log(
                    "exception",
                    level="error",
                    component="gateway._handle_request",
                    error_type=type(e).__name__,
                    message=str(e),
                    traceback=traceback.format_exc(limit=5),
                )
            return make_response(request_id, error=str(e))

    def _handle_connect(
        self, request_id: str, params: dict, ws: ServerConnection
    ) -> dict:
        if self._auth_token:
            provided = params.get("token")
            if not provided or not self._verify_token(provided):
                return make_response(request_id, error="Authentication failed")
            self._authenticated.add(ws)
            timer = self._auth_timers.pop(ws, None)
            if timer and not timer.done():
                timer.cancel()
        return make_response(
            request_id,
            payload={
                "version": "0.1.0",
                "sessions": self._sessions_with_stats(),
                "agents": self._list_agents(),
            },
        )

    def _verify_token(self, provided: str) -> bool:
        return hmac.compare_digest(provided, self._auth_token)

    async def _auth_timeout_task(self, ws: ServerConnection) -> None:
        await asyncio.sleep(self._auth_timeout)
        if ws not in self._authenticated:
            await ws.close(1008, "Authentication timeout")

    def _sessions_with_stats(self) -> list[dict]:
        """Return session list with stats and pool status merged in."""
        sessions = self._sessions.list()
        all_stats = self._stats.get_all()
        for s in sessions:
            s["stats"] = all_stats.get(s["id"], {})
            s["pooled"] = self._pool.has_client(s["id"]) if self._pool else False
        return sessions

    def _list_agents(self) -> list[str]:
        """Return agent names that have a workspace directory, always including DEFAULT_AGENT."""
        agents_dir = self._fera_home / "agents"
        found = set()
        if agents_dir.exists():
            for path in agents_dir.iterdir():
                if path.is_dir() and (path / "workspace").is_dir():
                    found.add(path.name)
        found.add(DEFAULT_AGENT)
        return sorted(found)

    def _handle_session_list(self, request_id: str) -> dict:
        return make_response(
            request_id,
            payload={
                "sessions": self._sessions_with_stats(),
            },
        )

    def _handle_agents_list(self, request_id: str) -> dict:
        return make_response(request_id, payload={"agents": self._list_agents()})

    async def _handle_session_create(self, request_id: str, params: dict) -> dict:
        name = params.get("name", "")
        if not name:
            return make_response(request_id, error="session name required")
        agent = params.get("agent", DEFAULT_AGENT)
        if not agent or "/" in agent or ".." in agent:
            return make_response(request_id, error=f"Invalid agent name: {agent!r}")
        info = self._sessions.create(name, agent=agent)
        if logger := get_logger():
            await logger.log("session.created", session=info["id"])
        return make_response(request_id, payload=info)

    async def _handle_session_deactivate(self, request_id: str, params: dict) -> dict:
        session = params.get("session")
        if not session:
            return make_response(request_id, error="session name required")
        if self._runner.active_session(session):
            return make_response(
                request_id, error="Cannot deactivate: session has an active turn"
            )
        await self._runner.deactivate_session(session)
        return make_response(request_id)

    async def _handle_session_delete(self, request_id: str, params: dict) -> dict:
        session = params.get("session")
        if not session:
            return make_response(request_id, error="session name required")
        if self._runner.active_session(session):
            return make_response(
                request_id, error="Cannot delete: session has an active turn"
            )
        await self._runner.deactivate_session(session)
        self._sessions.delete(session)
        return make_response(request_id)

    async def _handle_interrupt(self, request_id: str, params: dict) -> dict:
        session = params.get("session")
        if not session:
            return make_response(request_id, error="session name required")
        await self._runner.interrupt(session)
        return make_response(request_id)

    def _handle_workspace_list(self, request_id: str, params: dict) -> dict:
        from fera.gateway.workspace import list_files

        try:
            files = list_files(self._workspace_dir_for(params), params.get("path", ""))
            return make_response(request_id, payload={"files": files})
        except (ValueError, FileNotFoundError) as e:
            return make_response(request_id, error=str(e))

    def _handle_workspace_get(self, request_id: str, params: dict) -> dict:
        from fera.gateway.workspace import get_file

        path = params.get("path", "")
        if not path:
            return make_response(request_id, error="path required")
        try:
            content = get_file(self._workspace_dir_for(params), path)
            return make_response(request_id, payload={"path": path, "content": content})
        except (ValueError, FileNotFoundError) as e:
            return make_response(request_id, error=str(e))

    def _handle_workspace_set(self, request_id: str, params: dict) -> dict:
        from fera.gateway.workspace import set_file

        path = params.get("path", "")
        content = params.get("content")
        if not path:
            return make_response(request_id, error="path required")
        if content is None:
            return make_response(request_id, error="content required")
        try:
            set_file(self._workspace_dir_for(params), path, content)
            return make_response(request_id)
        except ValueError as e:
            return make_response(request_id, error=str(e))

    def _handle_mcp_list(self, request_id: str) -> dict:
        servers = self._mcp.list_servers(self._fera_home, agent_names=[DEFAULT_AGENT])
        return make_response(request_id, payload={"servers": servers})

    def _handle_logs_list(self, request_id: str) -> dict:
        dates = []
        if self._log_dir.exists():
            for year_dir in sorted(self._log_dir.iterdir()):
                if not year_dir.is_dir():
                    continue
                for month_dir in sorted(year_dir.iterdir()):
                    if not month_dir.is_dir():
                        continue
                    for log_file in sorted(month_dir.glob("*.jsonl")):
                        dates.append(log_file.stem)
        return make_response(request_id, payload={"dates": sorted(dates)})

    def _handle_logs_read(self, request_id: str, params: dict) -> dict:
        from datetime import date as _date

        date_str = params.get("date", "")
        if not date_str:
            return make_response(request_id, error="date required")
        try:
            d = _date.fromisoformat(date_str)
        except ValueError:
            return make_response(request_id, error=f"invalid date: {date_str!r}")
        path = self._log_dir / str(d.year) / f"{d.month:02d}" / f"{d}.jsonl"
        if not path.exists():
            return make_response(request_id, payload={"entries": []})
        entries = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    log.warning("Skipping malformed JSONL line in %s", path)
        return make_response(request_id, payload={"entries": entries})

    async def _handle_session_history(self, request_id: str, params: dict) -> dict:
        session = params.get("session", "")
        if not session:
            return make_response(request_id, error="session required")
        try:
            limit = min(int(params.get("limit", 500)), 1000)
        except (ValueError, TypeError):
            return make_response(request_id, error="limit must be an integer")
        messages = (
            await self._transcript.load_async(session, limit)
            if self._transcript
            else []
        )
        for msg in messages:
            if msg.get("type") == "agent" and msg.get("text"):
                msg["html"] = render_html(msg["text"])
        return make_response(request_id, payload={"messages": messages})

    def _handle_session_stats(self, request_id: str, params: dict) -> dict:
        session = params.get("session")
        if session:
            return make_response(request_id, payload=self._stats.get(session))
        return make_response(request_id, payload={"sessions": self._stats.get_all()})

    def _handle_status_summary(self, request_id: str) -> dict:
        summary = self._metrics.summary()
        # Add live session count from SessionStats
        all_stats = self._stats.get_all()
        summary["active_sessions"] = len(all_stats)
        # Add adapter statuses
        adapters = {}
        for adapter in self._adapters:
            adapters[adapter.name] = "running"  # TODO: track actual state
        summary["adapters"] = adapters
        return make_response(request_id, payload=summary)

    def _handle_status_metrics(self, request_id: str, params: dict) -> dict:
        import time

        metrics_requested = params.get(
            "metrics", ["turn", "tokens_in", "tokens_out", "cost"]
        )
        range_str = params.get("range", "24h")
        bucket_str = params.get("bucket", "15m")

        # Parse range
        now = time.time()
        range_map = {"24h": 86400, "7d": 7 * 86400}
        range_seconds = range_map.get(range_str, 86400)
        start_ts = now - range_seconds

        # Parse bucket
        bucket_map = {"5m": 300, "15m": 900, "1h": 3600}
        bucket_seconds = bucket_map.get(bucket_str, 900)

        result = {}
        for metric in metrics_requested:
            result[metric] = self._metrics.query(metric, start_ts, now, bucket_seconds)
        return make_response(request_id, payload=result)

    async def _handle_cron_run(self, request_id: str, params: dict) -> dict:
        job_name = params.get("job", "")
        if not job_name:
            return make_response(request_id, error="job name required")

        cron_config = load_cron_config()
        job = cron_config["jobs"].get(job_name)
        if not job:
            return make_response(
                request_id, error=f"Job '{job_name}' not found in cron.json"
            )

        from fera.cron import execute_job

        try:
            result = await execute_job(
                job_name=job_name,
                job=job,
                runner=self._runner,
                bus=self._bus,
                sessions=self._sessions,
            )
            return make_response(request_id, payload=result)
        except Exception as e:
            return make_response(request_id, error=str(e))

    async def _handle_question_answer(self, request_id: str, params: dict) -> dict:
        session = params.get("session", "")
        question_id = params.get("question_id", "")
        answers = params.get("answers", {})
        if not session or not question_id:
            return make_response(request_id, error="session and question_id required")
        await self._runner.answer_question(session, question_id, answers)
        return make_response(request_id)

    async def _handle_chat(self, params: dict, ws: ServerConnection) -> None:
        text = params.get("text", "")
        raw_session = params.get("session", f"{DEFAULT_AGENT}/default")
        session_info = self._sessions.get_or_create(raw_session)
        session = session_info[
            "id"
        ]  # normalize bare names to composite (e.g. "default" -> "main/default")
        await self._bus.publish(
            make_event(
                "user.message", session=session, data={"text": text, "source": "web"}
            )
        )

        try:
            async for event in self._runner.run_turn(session, text, source="web"):
                await self._bus.publish(event)
        except Exception as e:
            error_event = make_event(
                "agent.error", session=session, data={"error": str(e)}
            )
            await self._bus.publish(error_event)

    async def _stats_subscriber(self, event: dict) -> None:
        evt = event.get("event", "")
        session = event.get("session", "")
        if not session or session == "$system":
            return
        if evt == "agent.done":
            self._stats.record_turn(session, event.get("data", {}))
        elif evt == "agent.compact":
            self._stats.record_compact(session, event.get("data", {}).get("pre_tokens"))

    async def run_forever(self) -> None:
        import signal

        await self.start()
        log.info("Gateway listening on %s:%d", self._host, self.port)

        shutdown_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, shutdown_event.set)

        await shutdown_event.wait()
        log.info("Shutdown signal received")
        await self.stop()


def build_adapters_from_config(
    agent_config: dict, workspace_dir: Path | None, agent_name: str = DEFAULT_AGENT
) -> list[ChannelAdapter]:
    """Build channel adapters from per-agent config."""
    adapters: list[ChannelAdapter] = []
    tg = agent_config.get("adapters", {}).get("telegram")
    if tg:
        from fera.adapters.telegram import TelegramAdapter

        data_dir = workspace_dir.parent / "data" if workspace_dir else None
        adapters.append(
            TelegramAdapter(
                bot_token=tg["bot_token"],
                allowed_users=tg["allowed_users"],
                workspace_dir=workspace_dir,
                data_dir=data_dir,
                trusted=tg.get("trusted", False),
                agent_name=agent_name,
            )
        )
    mm = agent_config.get("adapters", {}).get("mattermost")
    if mm:
        from fera.adapters.mattermost import MattermostAdapter

        adapters.append(
            MattermostAdapter(
                url=mm["url"],
                bot_token=mm["bot_token"],
                allowed_users=mm["allowed_users"],
                trusted=mm.get("trusted", False),
                agent_name=agent_name,
                data_dir=workspace_dir.parent / "data" if workspace_dir else None,
            )
        )
    return adapters


def build_all_adapters(agents_dir: Path) -> list[ChannelAdapter]:
    """Build channel adapters for all agents in the agents directory."""
    from fera.config import load_agent_config

    adapters: list[ChannelAdapter] = []
    if not agents_dir.exists():
        return adapters
    for agent_dir in sorted(agents_dir.iterdir()):
        if not agent_dir.is_dir():
            continue
        agent_name = agent_dir.name
        ws_dir = agent_dir / "workspace"
        fera_home = agents_dir.parent
        agent_cfg = load_agent_config(agent_name, fera_home=fera_home)
        adapters.extend(
            build_adapters_from_config(
                agent_cfg, workspace_dir=ws_dir, agent_name=agent_name
            )
        )
    return adapters


def enable_diagnostics():
    """Enable faulthandler so SIGUSR1 dumps all thread tracebacks to stderr."""
    import faulthandler
    import signal

    faulthandler.enable()
    faulthandler.register(signal.SIGUSR1)


def main():
    from fera.config import (
        DEFAULT_AGENT,
        AGENTS_DIR,
        load_config,
        memory_url,
        substitute_env_vars,
        ensure_auth_token,
    )
    from fera.setup import ensure_agent

    enable_diagnostics()
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ensure_agent(DEFAULT_AGENT)
    config = load_config()
    gw_config = config["gateway"]
    auth_token = ensure_auth_token(config)
    log.info("Auth token: %s...%s", auth_token[:4], auth_token[-4:])
    raw_mcp_servers = config.get("mcp_servers", {})
    if raw_mcp_servers:
        log.info("Global MCP servers: %s", ", ".join(raw_mcp_servers.keys()))
    else:
        log.info("No global MCP servers configured")

    # Log per-agent MCP config at startup so misconfiguration is immediately visible.
    substituted_global = substitute_env_vars(raw_mcp_servers)
    if AGENTS_DIR.exists():
        for agent_dir in sorted(AGENTS_DIR.iterdir()):
            if not agent_dir.is_dir():
                continue
            agent_name = agent_dir.name
            try:
                extra = extra_mcp_servers(agent_name, substituted_global)
            except (json.JSONDecodeError, ValueError) as exc:
                log.error("Agent %s: skipping — bad config: %s", agent_name, exc)
                continue
            all_servers = build_mcp_servers(memory_url(config), agent_name, extra=extra)
            tools = build_allowed_tools(extra_servers=extra)
            log.info(
                "Agent %s: mcp=[%s] tools=[%s]",
                agent_name,
                ", ".join(all_servers),
                ", ".join(tools),
            )

    adapters = build_all_adapters(AGENTS_DIR)

    gateway = Gateway(
        host=gw_config["host"],
        port=gw_config["port"],
        pool_config=gw_config.get("pool"),
        memory_url=memory_url(config),
        global_mcp_servers=substitute_env_vars(raw_mcp_servers),
        auth_token=auth_token,
        adapters=adapters,
        heartbeat_config=config["heartbeat"],
        dream_cycle_config=config["dream_cycle"],
        timezone=config.get("timezone"),
        archivist_model=config.get("archivist_model"),
        alert_session=config.get("alert_session"),
        metrics_config=config.get("metrics", {}),
    )
    asyncio.run(gateway.run_forever())
