import json

import pytest

from fera.memory.registry import AgentRegistry, current_agent
from fera.memory.server import create_server


@pytest.fixture
def registry_and_server(tmp_path):
    workspace = tmp_path / "main" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "memory").mkdir()
    (workspace / "MEMORY.md").write_text("# Facts\n\nAlex likes Python.\n")
    (workspace / "memory" / "2026-02-17.md").write_text("# Today\n\nBuilt the memory system.\n")
    (tmp_path / "main" / "data").mkdir()

    registry = AgentRegistry(tmp_path)
    registry.sync_agent("main")
    server = create_server(registry)
    return registry, server


@pytest.fixture(autouse=True)
def _set_agent():
    token = current_agent.set("main")
    yield
    current_agent.reset(token)


def _parse_tool_result(result):
    """Extract parsed JSON from a call_tool result."""
    content_list = result[0]
    return json.loads(content_list[0].text)


@pytest.mark.anyio
async def test_memory_search_returns_results(registry_and_server):
    _, server = registry_and_server
    result = await server.call_tool("memory_search", {"query": "Python"})
    parsed = _parse_tool_result(result)
    assert len(parsed["results"]) > 0


@pytest.mark.anyio
async def test_memory_get_reads_file(registry_and_server):
    _, server = registry_and_server
    result = await server.call_tool("memory_get", {"path": "MEMORY.md"})
    parsed = _parse_tool_result(result)
    assert "Alex likes Python" in parsed["text"]


@pytest.mark.anyio
async def test_memory_get_line_range(registry_and_server):
    _, server = registry_and_server
    result = await server.call_tool(
        "memory_get", {"path": "MEMORY.md", "from_line": 3, "num_lines": 1}
    )
    parsed = _parse_tool_result(result)
    assert "Alex" in parsed["text"]
    # Content between wrapper tags should have at most 1 newline
    inner = parsed["text"].split("\n", 1)[1].rsplit("\n", 1)[0]
    assert inner.count("\n") <= 1


@pytest.mark.anyio
async def test_memory_get_rejects_non_markdown(registry_and_server):
    _, server = registry_and_server
    result = await server.call_tool("memory_get", {"path": "secret.txt"})
    parsed = _parse_tool_result(result)
    assert "error" in parsed


@pytest.mark.anyio
async def test_memory_get_rejects_path_traversal(registry_and_server):
    _, server = registry_and_server
    result = await server.call_tool("memory_get", {"path": "../../etc/passwd"})
    parsed = _parse_tool_result(result)
    assert "error" in parsed


@pytest.mark.anyio
async def test_memory_search_accepts_mode_param(registry_and_server):
    _, server = registry_and_server
    result = await server.call_tool(
        "memory_search", {"query": "Python", "mode": "quick"}
    )
    parsed = _parse_tool_result(result)
    assert len(parsed["results"]) > 0


class _FakeExpander:
    async def expand(self, query: str) -> list[str]:
        return ["alternative phrasing"]


class _FakeReranker:
    async def rerank(self, query, candidates):
        return list(reversed(candidates))


@pytest.mark.anyio
async def test_memory_search_deep_with_fakes(tmp_path):
    workspace = tmp_path / "main" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "MEMORY.md").write_text("# Facts\n\nAlex likes Python.\n")
    (tmp_path / "main" / "data").mkdir()

    registry = AgentRegistry(tmp_path)
    registry.sync_agent("main")
    server = create_server(
        registry, expander=_FakeExpander(), reranker=_FakeReranker()
    )
    token = current_agent.set("main")
    try:
        result = await server.call_tool(
            "memory_search", {"query": "Python", "mode": "deep"}
        )
        parsed = _parse_tool_result(result)
        assert len(parsed["results"]) > 0
    finally:
        current_agent.reset(token)


@pytest.mark.anyio
async def test_memory_search_deep_without_api_key(registry_and_server):
    _, server = registry_and_server
    result = await server.call_tool(
        "memory_search", {"query": "Python", "mode": "deep"}
    )
    parsed = _parse_tool_result(result)
    assert "error" in parsed


@pytest.mark.anyio
async def test_memory_search_wraps_snippets(registry_and_server):
    _, server = registry_and_server
    result = await server.call_tool("memory_search", {"query": "Python"})
    parsed = _parse_tool_result(result)
    for r in parsed["results"]:
        assert r["snippet"].startswith("<untrusted")
        assert r["snippet"].endswith("</untrusted>")
        assert 'source="memory"' in r["snippet"]


@pytest.mark.anyio
async def test_memory_get_wraps_content(registry_and_server):
    _, server = registry_and_server
    result = await server.call_tool("memory_get", {"path": "MEMORY.md"})
    parsed = _parse_tool_result(result)
    assert parsed["text"].startswith("<untrusted")
    assert parsed["text"].endswith("</untrusted>")
    assert 'source="memory"' in parsed["text"]
    assert "Alex likes Python" in parsed["text"]


@pytest.mark.anyio
async def test_memory_get_wraps_line_range(registry_and_server):
    _, server = registry_and_server
    result = await server.call_tool(
        "memory_get", {"path": "MEMORY.md", "from_line": 3, "num_lines": 1}
    )
    parsed = _parse_tool_result(result)
    assert parsed["text"].startswith("<untrusted")
    assert 'source="memory"' in parsed["text"]
