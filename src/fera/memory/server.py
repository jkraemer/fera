from __future__ import annotations

import json
import logging
import os

from mcp.server import FastMCP
from starlette.datastructures import QueryParams
from starlette.responses import Response

from fera.config import DEFAULT_AGENT
from fera.memory.expander import QueryExpander
from fera.memory.registry import AgentRegistry, current_agent
from fera.memory.reranker import Reranker
from fera.memory.search import deep_search, hybrid_search
from fera.sanitize import wrap_untrusted

log = logging.getLogger(__name__)


def create_server(
    registry: AgentRegistry,
    *,
    expander: QueryExpander | None = None,
    reranker: Reranker | None = None,
) -> FastMCP:
    """Create the MCP server backed by an AgentRegistry."""
    server = FastMCP("fera-memory")

    @server.tool()
    async def memory_search(
        query: str, max_results: int = 6, mode: str = "quick"
    ) -> str:
        """Search persistent memory. Use before answering about prior work,
        decisions, dates, people, preferences, or todos.

        mode: "quick" (default) uses hybrid FTS+vector search.
              "deep" uses query expansion + reranking (requires API key).
        """
        agent = current_agent.get()
        index = registry.get_index(agent)

        if mode == "deep":
            if expander is None or reranker is None:
                return json.dumps(
                    {"error": "deep search unavailable (no API key configured)"}
                )
            results = await deep_search(
                index,
                query,
                expander=expander,
                reranker=reranker,
                max_results=max_results,
            )
        else:
            results = hybrid_search(index, query, max_results=max_results)

        return json.dumps(
            {
                "results": [
                    {
                        "path": r.path,
                        "start_line": r.start_line,
                        "end_line": r.end_line,
                        "score": round(r.score, 4),
                        "snippet": wrap_untrusted(r.snippet, source="memory", path=r.path),
                    }
                    for r in results
                ]
            }
        )

    @server.tool()
    async def memory_get(
        path: str, from_line: int | None = None, num_lines: int | None = None
    ) -> str:
        """Read a memory file or a specific line range."""
        agent = current_agent.get()
        workspace = registry.workspace_dir(agent)
        resolved = (workspace / path).resolve()

        if not resolved.is_relative_to(workspace.resolve()):
            return json.dumps({"error": "path outside memory directory"})
        if resolved.suffix != ".md":
            return json.dumps({"error": "only .md files are readable"})
        if not resolved.exists():
            return json.dumps({"error": f"file not found: {path}"})

        text = resolved.read_text()
        if from_line is not None:
            lines = text.splitlines(keepends=True)
            start = max(0, from_line - 1)
            end = start + (num_lines or len(lines))
            text = "".join(lines[start:end])

        text = wrap_untrusted(text, source="memory", path=path)
        return json.dumps({"text": text, "path": path})

    return server


class AgentContextMiddleware:
    """ASGI middleware that sets current_agent contextvar from ?agent= query param."""

    def __init__(self, app, registry: AgentRegistry, default_agent: str = DEFAULT_AGENT):
        self._app = app
        self._registry = registry
        self._default_agent = default_agent

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            params = QueryParams(scope.get("query_string", b""))
            path = scope.get("path", "")

            if path.endswith("/sse"):
                agent = params.get("agent", self._default_agent)
                if not self._registry.has_agent(agent):
                    response = Response(
                        f"Unknown agent: {agent}", status_code=404
                    )
                    await response(scope, receive, send)
                    return
                token = current_agent.set(agent)
                try:
                    await self._app(scope, receive, send)
                finally:
                    current_agent.reset(token)
                return

        await self._app(scope, receive, send)


def run_server():
    """Entry point for the standalone SSE memory server."""
    import asyncio

    import uvicorn

    from fera.config import AGENTS_DIR, load_config
    from fera.memory.watcher import MemoryWatcher

    logging.basicConfig(level=logging.INFO)

    config = load_config()
    mem_config = config["memory"]

    registry = AgentRegistry(AGENTS_DIR)
    for agent in registry.discover():
        registry.sync_agent(agent)

    expander = None
    reranker = None
    if os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic

        from fera.memory.expander import HaikuQueryExpander
        from fera.memory.reranker import HaikuReranker

        client = anthropic.AsyncAnthropic()
        expander = HaikuQueryExpander(client)
        reranker = HaikuReranker(client)

    server = create_server(registry, expander=expander, reranker=reranker)
    inner_app = server.sse_app()
    app = AgentContextMiddleware(inner_app, registry, DEFAULT_AGENT)

    watcher = MemoryWatcher(registry)

    async def serve():
        loop = asyncio.get_event_loop()
        watcher.start(loop)
        uvi_config = uvicorn.Config(
            app,
            host=mem_config["host"],
            port=mem_config["port"],
            log_level="info",
        )
        uvi_server = uvicorn.Server(uvi_config)
        try:
            await uvi_server.serve()
        finally:
            watcher.stop()

    asyncio.run(serve())
