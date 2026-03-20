import json
import threading
import time

import pytest
import uvicorn

from fera.memory.registry import AgentRegistry
from fera.memory.server import AgentContextMiddleware, create_server


@pytest.fixture
def sse_server(tmp_path):
    """Start a real SSE server on a random port in a background thread.

    The registry sync and uvicorn server must run in the same thread because
    MemoryIndex opens a SQLite connection that is bound to the creating thread.
    """
    workspace = tmp_path / "main" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "MEMORY.md").write_text("# Facts\n\nAlex likes Python.\n")
    (tmp_path / "main" / "data").mkdir()

    registry = AgentRegistry(tmp_path)
    server = create_server(registry)
    inner_app = server.sse_app()
    app = AgentContextMiddleware(inner_app, registry, default_agent="main")

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    uvi_server = uvicorn.Server(config)

    def run_server():
        # Sync the index in this thread so the SQLite connection is created
        # here, where uvicorn will later use it for tool handler calls.
        registry.sync_agent("main")
        uvi_server.run()

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()

    # Wait for server to start
    for _ in range(50):
        if uvi_server.started:
            break
        time.sleep(0.1)
    else:
        pytest.fail("SSE server did not start")

    # Get the actual port
    sockets = uvi_server.servers[0].sockets
    port = sockets[0].getsockname()[1]

    yield f"http://127.0.0.1:{port}"

    uvi_server.should_exit = True
    thread.join(timeout=5)


@pytest.mark.asyncio
async def test_sse_e2e_tool_call(sse_server):
    """Connect via MCP SSE client and call memory_search."""
    from mcp.client.session import ClientSession
    from mcp.client.sse import sse_client

    url = f"{sse_server}/sse?agent=main"
    async with sse_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "memory_search", {"query": "Python"}
            )
            text = result.content[0].text
            parsed = json.loads(text)
            assert len(parsed["results"]) > 0
            assert any("Python" in r["snippet"] for r in parsed["results"])
