import pytest
from starlette.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from fera.memory.registry import AgentRegistry, current_agent
from fera.memory.server import AgentContextMiddleware, create_server

BASE_URL = "http://127.0.0.1:8390"


@pytest.fixture
def registry(tmp_path):
    """Create a registry with a single 'main' agent."""
    workspace = tmp_path / "main" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "MEMORY.md").write_text("# Facts\n\nAlex likes Python.\n")
    (tmp_path / "main" / "data").mkdir()

    reg = AgentRegistry(tmp_path)
    reg.sync_agent("main")
    return reg


def _make_echo_app():
    """A tiny ASGI app that echoes the current_agent contextvar."""

    async def echo_agent(request):
        try:
            agent = current_agent.get()
        except LookupError:
            agent = "<unset>"
        return PlainTextResponse(agent)

    return Starlette(routes=[Route("/sse", echo_agent)])


def test_middleware_sets_agent_from_query(registry):
    """Middleware sets current_agent contextvar from ?agent= on /sse."""
    inner = _make_echo_app()
    app = AgentContextMiddleware(inner, registry, default_agent="main")
    client = TestClient(app, base_url=BASE_URL)

    response = client.get("/sse?agent=main")
    assert response.status_code == 200
    assert response.text == "main"


def test_middleware_uses_default_agent(registry):
    """Middleware falls back to default_agent when ?agent= is absent."""
    inner = _make_echo_app()
    app = AgentContextMiddleware(inner, registry, default_agent="main")
    client = TestClient(app, base_url=BASE_URL)

    response = client.get("/sse")
    assert response.status_code == 200
    assert response.text == "main"


def test_middleware_rejects_unknown_agent(registry):
    """Middleware returns 404 for unknown agents."""
    inner = _make_echo_app()
    app = AgentContextMiddleware(inner, registry, default_agent="main")
    client = TestClient(app, base_url=BASE_URL)

    response = client.get("/sse?agent=nonexistent")
    assert response.status_code == 404
    assert "nonexistent" in response.text


def test_middleware_passes_non_sse_through(registry):
    """Non-/sse paths pass through without setting current_agent."""

    async def other_endpoint(request):
        try:
            agent = current_agent.get()
        except LookupError:
            agent = "<unset>"
        return PlainTextResponse(agent)

    inner = Starlette(routes=[Route("/other", other_endpoint)])
    app = AgentContextMiddleware(inner, registry, default_agent="main")
    client = TestClient(app, base_url=BASE_URL)

    response = client.get("/other")
    assert response.status_code == 200
    assert response.text == "<unset>"


def test_middleware_resets_contextvar(registry):
    """current_agent contextvar is reset after middleware returns."""
    inner = _make_echo_app()
    app = AgentContextMiddleware(inner, registry, default_agent="main")
    client = TestClient(app, base_url=BASE_URL)

    # Make a request that sets the contextvar
    response = client.get("/sse?agent=main")
    assert response.status_code == 200

    # After the request, the contextvar should be unset in our context
    # (the middleware resets it via token)
    with pytest.raises(LookupError):
        current_agent.get()


def test_create_server_returns_fastmcp_with_sse(registry):
    """create_server returns a FastMCP instance whose sse_app() works."""
    server = create_server(registry)
    app = server.sse_app()
    # sse_app() returns a Starlette instance with /sse and /messages routes
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/sse" in paths
    assert "/messages" in paths
