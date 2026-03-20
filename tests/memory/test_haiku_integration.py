"""Integration tests that call real Haiku via the Anthropic API.

Skipped entirely when ANTHROPIC_API_KEY is not set.
"""

from __future__ import annotations

import os

import anthropic
import pytest

from fera.memory.expander import HaikuQueryExpander
from fera.memory.index import MemoryIndex
from fera.memory.reranker import HaikuReranker
from fera.memory.search import deep_search
from fera.memory.types import SearchResult

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set",
    ),
]


@pytest.fixture
async def haiku_client():
    client = anthropic.AsyncAnthropic()
    yield client
    await client.close()


async def test_haiku_expansion(haiku_client):
    """HaikuQueryExpander returns at least one alternative phrasing."""
    expander = HaikuQueryExpander(haiku_client)
    results = await expander.expand("what programming language does Alex use")
    assert len(results) >= 1
    assert all(isinstance(r, str) for r in results)


async def test_haiku_rerank(haiku_client):
    """HaikuReranker ranks the directly relevant snippet first."""
    candidates = [
        SearchResult(
            path="a.md",
            start_line=1,
            end_line=5,
            score=0.5,
            snippet="Alex loves Python and uses it for everything",
        ),
        SearchResult(
            path="b.md",
            start_line=1,
            end_line=3,
            score=0.4,
            snippet="The weather in Berlin was sunny today",
        ),
        SearchResult(
            path="c.md",
            start_line=1,
            end_line=4,
            score=0.3,
            snippet="Python is a programming language created by Guido",
        ),
    ]
    reranker = HaikuReranker(haiku_client)
    reranked = await reranker.rerank("what language does Alex use", candidates)

    assert len(reranked) == 3
    # The snippet mentioning Alex + Python should rank first
    assert reranked[0].snippet == "Alex loves Python and uses it for everything"


async def test_deep_search_end_to_end(haiku_client, tmp_path):
    """Full deep_search pipeline with real Haiku finds relevant content."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    mem = workspace / "memory"
    mem.mkdir()

    (mem / "facts.md").write_text(
        "# Facts\n\nAlex uses Python and Neovim.\nHe lives in Berlin.\n"
    )
    (mem / "log.md").write_text(
        "# Log\n\nHad pizza for dinner.\nWent for a walk in the park.\n"
    )

    idx = MemoryIndex(memory_dir=str(workspace), db_path=str(tmp_path / "memory.db"))
    idx.sync()

    expander = HaikuQueryExpander(haiku_client)
    reranker = HaikuReranker(haiku_client)

    results = await deep_search(
        idx,
        "what editor does Alex prefer",
        expander=expander,
        reranker=reranker,
    )

    assert len(results) >= 1
    snippets = " ".join(r.snippet for r in results)
    assert "Neovim" in snippets
