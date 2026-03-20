import pytest
import pytest_asyncio

from fera.gateway.server import Gateway
from fera.gateway.client import GatewayClient


@pytest_asyncio.fixture
async def gateway(tmp_path):
    gw = Gateway(host="127.0.0.1", port=0, fera_home=tmp_path)
    await gw.start()
    yield gw
    await gw.stop()


@pytest.mark.asyncio
async def test_connect_and_handshake(gateway):
    client = GatewayClient(f"ws://127.0.0.1:{gateway.port}")
    snapshot = await client.connect()
    assert "version" in snapshot
    assert "sessions" in snapshot
    await client.disconnect()


@pytest.mark.asyncio
async def test_list_sessions(gateway):
    client = GatewayClient(f"ws://127.0.0.1:{gateway.port}")
    await client.connect()
    sessions = await client.list_sessions()
    assert isinstance(sessions, list)
    await client.disconnect()


@pytest.mark.asyncio
async def test_create_session(gateway):
    client = GatewayClient(f"ws://127.0.0.1:{gateway.port}")
    await client.connect()
    info = await client.create_session("test-session")
    assert info["name"] == "test-session"
    sessions = await client.list_sessions()
    names = [s["name"] for s in sessions]
    assert "test-session" in names
    await client.disconnect()


@pytest.mark.asyncio
async def test_interrupt(gateway):
    client = GatewayClient(f"ws://127.0.0.1:{gateway.port}")
    await client.connect()
    # Interrupt with no active turn — should succeed (no-op)
    await client.interrupt("default")
    await client.disconnect()


@pytest.mark.asyncio
async def test_disconnect_and_reconnect(gateway):
    client = GatewayClient(f"ws://127.0.0.1:{gateway.port}")
    await client.connect()
    await client.disconnect()
    assert not client.connected
    snapshot = await client.connect()
    assert "version" in snapshot
    await client.disconnect()


# --- Auth token support ---

@pytest_asyncio.fixture
async def auth_gateway(tmp_path):
    gw = Gateway(host="127.0.0.1", port=0, fera_home=tmp_path, auth_token="tui-test-token")
    await gw.start()
    yield gw
    await gw.stop()


@pytest.mark.asyncio
async def test_connect_with_auth_token(auth_gateway):
    client = GatewayClient(
        f"ws://127.0.0.1:{auth_gateway.port}",
        auth_token="tui-test-token",
    )
    snapshot = await client.connect()
    assert "version" in snapshot
    await client.disconnect()


@pytest.mark.asyncio
async def test_connect_wrong_token_raises(auth_gateway):
    client = GatewayClient(
        f"ws://127.0.0.1:{auth_gateway.port}",
        auth_token="wrong-token",
    )
    with pytest.raises(RuntimeError, match="[Aa]uth"):
        await client.connect()
