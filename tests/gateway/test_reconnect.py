import asyncio

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
async def test_reconnect_after_server_restart(gateway, tmp_path):
    """Client reconnects after the server goes away and comes back."""
    port = gateway.port  # Capture before stop (port=0 resolves to ephemeral)
    client = GatewayClient(f"ws://127.0.0.1:{port}")
    client.max_reconnect_delay = 0.1  # Speed up for tests

    reconnected = asyncio.Event()
    disconnected = asyncio.Event()

    def on_state(connected: bool):
        if connected:
            reconnected.set()
        else:
            disconnected.set()

    client.on_connection_state(on_state)
    await client.connect()
    reconnected.clear()  # Reset so we can detect the reconnection

    # Kill the server
    await gateway.stop()
    await asyncio.wait_for(disconnected.wait(), timeout=5)
    assert not client.connected

    # Restart the server on the same port
    gw2 = Gateway(host="127.0.0.1", port=port, fera_home=tmp_path)
    await gw2.start()
    try:
        await asyncio.wait_for(reconnected.wait(), timeout=10)
        assert client.connected
    finally:
        await client.disconnect()
        await gw2.stop()


@pytest.mark.asyncio
async def test_backoff_increases():
    """Backoff delay increases on successive failures."""
    client = GatewayClient("ws://127.0.0.1:1")  # Nothing listening
    client.max_reconnect_delay = 1.0
    # We can't easily test the full loop, so just verify the backoff
    # calculation directly
    assert client._backoff_delay(0) == 0.5
    assert client._backoff_delay(1) == 1.0
    assert client._backoff_delay(5) <= client.max_reconnect_delay
