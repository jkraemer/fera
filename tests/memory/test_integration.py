import json

import pytest

from fera.memory.registry import AgentRegistry, current_agent
from fera.memory.server import create_server


def _parse_tool_result(result):
    """Extract parsed JSON from a call_tool result.

    call_tool returns (content_list, metadata).  content_list[0].text is
    the JSON string returned by the tool function.
    """
    content_list = result[0]
    return json.loads(content_list[0].text)


@pytest.fixture(autouse=True)
def _set_agent():
    token = current_agent.set("main")
    yield
    current_agent.reset(token)


@pytest.fixture
def full_system(tmp_path):
    """Set up a complete memory system with realistic content."""
    workspace = tmp_path / "main" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "memory").mkdir()
    (tmp_path / "main" / "data").mkdir()

    (workspace / "MEMORY.md").write_text(
        "# Memory\n\n"
        "## People\n\n"
        "- Alex: Software engineer, based in Berlin, prefers Python and Neovim\n"
        "- Alice: Alex's colleague, works on frontend\n\n"
        "## Preferences\n\n"
        "- Editor: Neovim\n"
        "- Language: Python\n"
        "- OS: Debian stable\n"
    )

    (workspace / "memory" / "2026-02-17.md").write_text(
        "# 2026-02-17\n\n"
        "- Started Fera project scaffolding\n"
        "- Chose Claude Agent SDK (Python) as the foundation\n"
        "- Set up Podman dev container with Debian stable base\n"
        "- Discussed persistent memory design\n"
    )

    (workspace / "memory" / "architecture.md").write_text(
        "# Architecture\n\n"
        "## Memory System\n\n"
        "Plain markdown files indexed in SQLite with FTS5 and sqlite-vec.\n"
        "Hybrid search: 70% vector weight, 30% keyword weight.\n"
        "Embeddings via local fastembed ONNX model.\n\n"
        "## Agent Core\n\n"
        "Built on Claude Agent SDK. Uses MCP servers for tool access.\n"
    )

    registry = AgentRegistry(tmp_path)
    registry.sync_agent("main")
    return create_server(registry)


@pytest.mark.anyio
async def test_search_then_get(full_system):
    """Simulate the agent's typical workflow: search, then get details."""
    # Agent searches for information about a person
    result = await full_system.call_tool(
        "memory_search", {"query": "who is Alice"}
    )
    parsed = _parse_tool_result(result)
    assert len(parsed["results"]) > 0

    # Agent fetches the specific file for more context
    top_result = parsed["results"][0]
    get_result = await full_system.call_tool(
        "memory_get",
        {
            "path": top_result["path"],
            "from_line": top_result["start_line"],
            "num_lines": top_result["end_line"] - top_result["start_line"] + 1,
        },
    )
    text = _parse_tool_result(get_result)["text"]
    assert "Alice" in text


@pytest.mark.anyio
async def test_semantic_search_finds_related_content(full_system):
    """Semantic search should find content even without exact keyword match."""
    result = await full_system.call_tool(
        "memory_search", {"query": "what text editor is used"}
    )
    parsed = _parse_tool_result(result)
    snippets = " ".join(r["snippet"] for r in parsed["results"])
    assert "Neovim" in snippets
