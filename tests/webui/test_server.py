import asyncio
import json
import threading

import pytest
from starlette.testclient import TestClient

from fera.webui.server import create_app


@pytest.fixture
def static_dir(tmp_path):
    (tmp_path / "index.html").write_text("<!DOCTYPE html><html><body>Fera</body></html>")
    (tmp_path / "app.js").write_text("console.log('fera');")
    return tmp_path


def test_config_endpoint_returns_proxy_ws_url(static_dir):
    """Config should point to the webui's own /ws proxy, not the gateway directly."""
    app = create_app(str(static_dir), gateway_port=8389)
    client = TestClient(app)
    response = client.get("/config.json")
    assert response.status_code == 200
    data = response.json()
    # URL should point to the webui server's own /ws path
    assert data["gateway_ws"] == "ws://testserver/ws"


def test_serves_index(static_dir):
    app = create_app(str(static_dir), 8389)
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "Fera" in response.text


def test_serves_static_files(static_dir):
    app = create_app(str(static_dir), 8389)
    client = TestClient(app)
    response = client.get("/app.js")
    assert response.status_code == 200
    assert "fera" in response.text


def _start_ws_server(handler):
    """Start a websockets server in a background thread, return (port, stop_fn)."""
    import websockets

    ready = threading.Event()
    port_holder = []
    loop = None
    server = None

    def run():
        nonlocal loop, server
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def start():
            nonlocal server
            server = await websockets.serve(handler, "127.0.0.1", 0)
            port_holder.append(server.sockets[0].getsockname()[1])
            ready.set()
            # Keep running until cancelled
            await asyncio.Future()

        try:
            loop.run_until_complete(start())
        except asyncio.CancelledError:
            pass

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    ready.wait(timeout=5)

    def stop():
        if loop and loop.is_running():
            async def shutdown():
                server.close()
                await server.wait_closed()

            asyncio.run_coroutine_threadsafe(shutdown(), loop).result(timeout=2)
            # Cancel the main future to let the loop exit
            for task in asyncio.all_tasks(loop):
                task.cancel()
            thread.join(timeout=2)

    return port_holder[0], stop


def test_ws_proxy_forwards_messages(static_dir):
    """Proxy should forward messages between browser and gateway."""
    received = []

    async def handler(ws):
        async for msg in ws:
            received.append(msg)
            await ws.send(msg)

    port, stop = _start_ws_server(handler)
    try:
        app = create_app(str(static_dir), gateway_port=port)
        client = TestClient(app)

        frame = {"type": "req", "id": "1", "method": "connect", "params": {}}
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps(frame))
            response = json.loads(ws.receive_text())
            assert response == frame  # echo server returns same message

        assert len(received) == 1
        assert json.loads(received[0]) == frame
    finally:
        stop()


def test_ws_proxy_forwards_gateway_events(static_dir):
    """Proxy should forward unsolicited events from gateway to browser."""
    event = {"type": "event", "event": "agent.text", "session": "main/default",
             "data": {"text": "hello"}}

    async def handler(ws):
        # Send an unsolicited event, then echo
        await ws.send(json.dumps(event))
        async for msg in ws:
            await ws.send(msg)

    port, stop = _start_ws_server(handler)
    try:
        app = create_app(str(static_dir), gateway_port=port)
        client = TestClient(app)

        with client.websocket_connect("/ws") as ws:
            # Should receive the unsolicited event first
            first = json.loads(ws.receive_text())
            assert first == event

            # Then a round-trip should still work
            req = {"type": "req", "id": "1", "method": "ping", "params": {}}
            ws.send_text(json.dumps(req))
            reply = json.loads(ws.receive_text())
            assert reply == req
    finally:
        stop()
