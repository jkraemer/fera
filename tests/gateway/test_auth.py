import asyncio
import json
import uuid

import pytest
import pytest_asyncio
import websockets

from fera.gateway.server import Gateway

TOKEN = "test-secret-token-abc123"


@pytest_asyncio.fixture
async def auth_gateway(tmp_path):
    gw = Gateway(
        host="127.0.0.1", port=0, fera_home=tmp_path,
        auth_token=TOKEN, auth_timeout=2.0,
    )
    await gw.start()
    yield gw
    await gw.stop()


def _ws_url(gw):
    return f"ws://127.0.0.1:{gw.port}"


def _req(method, params=None):
    return json.dumps({
        "type": "req", "id": str(uuid.uuid4()),
        "method": method, "params": params or {},
    })


@pytest.mark.asyncio
async def test_connect_valid_token(auth_gateway):
    async with websockets.connect(_ws_url(auth_gateway)) as ws:
        await ws.send(_req("connect", {"token": TOKEN}))
        frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert frame["ok"] is True
        assert "version" in frame["payload"]


@pytest.mark.asyncio
async def test_connect_invalid_token(auth_gateway):
    async with websockets.connect(_ws_url(auth_gateway)) as ws:
        await ws.send(_req("connect", {"token": "wrong"}))
        frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert frame["ok"] is False
        assert "auth" in frame["error"].lower()


@pytest.mark.asyncio
async def test_connect_missing_token(auth_gateway):
    async with websockets.connect(_ws_url(auth_gateway)) as ws:
        await ws.send(_req("connect"))
        frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert frame["ok"] is False
        assert "auth" in frame["error"].lower()


@pytest.mark.asyncio
async def test_request_before_auth_rejected(auth_gateway):
    async with websockets.connect(_ws_url(auth_gateway)) as ws:
        await ws.send(_req("session.list"))
        frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert frame["ok"] is False
        assert "auth" in frame["error"].lower()


@pytest.mark.asyncio
async def test_request_after_auth_works(auth_gateway):
    async with websockets.connect(_ws_url(auth_gateway)) as ws:
        await ws.send(_req("connect", {"token": TOKEN}))
        frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert frame["ok"] is True

        await ws.send(_req("session.list"))
        frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert frame["ok"] is True


@pytest.mark.asyncio
async def test_auth_timeout_closes_connection(auth_gateway):
    async with websockets.connect(_ws_url(auth_gateway)) as ws:
        with pytest.raises((websockets.ConnectionClosed, asyncio.TimeoutError)):
            await asyncio.wait_for(ws.recv(), timeout=5)


@pytest.mark.asyncio
async def test_no_token_configured_skips_auth(tmp_path):
    gw = Gateway(host="127.0.0.1", port=0, fera_home=tmp_path)
    await gw.start()
    try:
        async with websockets.connect(f"ws://127.0.0.1:{gw.port}") as ws:
            await ws.send(_req("connect"))
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert frame["ok"] is True
    finally:
        await gw.stop()
