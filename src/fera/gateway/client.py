"""WebSocket client for connecting to the Fera gateway."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

import websockets

from fera.gateway.protocol import make_request, parse_frame


class GatewayClient:
    """WebSocket client for connecting to the Fera gateway."""

    def __init__(self, url: str, auth_token: str | None = None):
        self._url = url
        self._auth_token = auth_token
        self._ws = None
        self._pending: dict[str, asyncio.Future] = {}
        self._event_handler: Callable[[dict], None] | None = None
        self._reader_task: asyncio.Task | None = None
        self._state_handler: Callable[[bool], None] | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._should_reconnect = False
        self.max_reconnect_delay: float = 30.0

    @property
    def connected(self) -> bool:
        return self._ws is not None and self._ws.state.name == "OPEN"

    def on_event(self, handler: Callable[[dict], None]) -> None:
        """Set a callback for incoming events."""
        self._event_handler = handler

    def on_connection_state(self, handler: Callable[[bool], None]) -> None:
        """Set a callback for connection state changes."""
        self._state_handler = handler

    def _backoff_delay(self, attempt: int) -> float:
        """Exponential backoff: 0.5s, 1s, 2s, 4s, ... capped at max."""
        return min(0.5 * (2 ** attempt), self.max_reconnect_delay)

    def _connect_params(self) -> dict:
        """Build params for the connect handshake."""
        params = {}
        if self._auth_token:
            params["token"] = self._auth_token
        return params

    async def connect(self) -> dict:
        """Connect to the gateway and perform handshake. Returns snapshot."""
        self._ws = await websockets.connect(self._url)
        self._should_reconnect = True
        self._reader_task = asyncio.create_task(self._read_loop())
        snapshot = await self._request("connect", self._connect_params())
        if self._state_handler:
            self._state_handler(True)
        return snapshot

    async def disconnect(self) -> None:
        """Disconnect from the gateway."""
        self._should_reconnect = False
        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        for future in self._pending.values():
            if not future.done():
                future.set_exception(RuntimeError("disconnected"))
        self._pending.clear()
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def send_message(self, text: str, session: str = "default") -> None:
        """Send a chat message. Events arrive via the on_event callback."""
        await self._request("chat.send", {"text": text, "session": session})

    async def list_sessions(self) -> list[dict]:
        """List all sessions."""
        result = await self._request("session.list")
        return result["sessions"]

    async def create_session(self, name: str) -> dict:
        """Create a new session."""
        return await self._request("session.create", {"name": name})

    async def interrupt(self, session: str) -> None:
        """Interrupt the active agent turn for a session."""
        await self._request("chat.interrupt", {"session": session})

    async def _request(self, method: str, params: dict | None = None) -> Any:
        """Send a request and wait for the matching response."""
        frame = make_request(method, params)
        request_id = frame["id"]
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._ws.send(json.dumps(frame))
        try:
            return await asyncio.wait_for(future, timeout=30)
        finally:
            self._pending.pop(request_id, None)

    async def _read_loop(self) -> None:
        """Background task that reads frames and dispatches them."""
        try:
            async for raw in self._ws:
                frame = parse_frame(raw)
                if frame is None:
                    continue
                if frame["type"] == "res":
                    request_id = frame["id"]
                    future = self._pending.pop(request_id, None)
                    if future and not future.done():
                        if frame["ok"]:
                            future.set_result(frame.get("payload"))
                        else:
                            future.set_exception(
                                RuntimeError(frame.get("error", "request failed"))
                            )
                elif frame["type"] == "event":
                    if self._event_handler:
                        self._event_handler(frame)
        except websockets.ConnectionClosed:
            pass
        except asyncio.CancelledError:
            raise

        # Connection lost — trigger reconnection
        if self._should_reconnect:
            self._ws = None
            if self._state_handler:
                self._state_handler(False)
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        """Attempt to reconnect with exponential backoff."""
        attempt = 0
        while self._should_reconnect:
            delay = self._backoff_delay(attempt)
            await asyncio.sleep(delay)
            try:
                self._ws = await websockets.connect(self._url)
                self._reader_task = asyncio.create_task(self._read_loop())
                await self._request("connect", self._connect_params())
                if self._state_handler:
                    self._state_handler(True)
                return
            except (OSError, websockets.WebSocketException):
                attempt += 1
                continue
