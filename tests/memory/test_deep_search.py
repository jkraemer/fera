from __future__ import annotations

import pytest

from fera.memory.expander import QueryExpander
from fera.memory.index import MemoryIndex
from fera.memory.reranker import Reranker
from fera.memory.search import deep_search
from fera.memory.types import SearchResult


class FakeExpander:
    """Records calls and returns canned expansions."""

    def __init__(self, expansions: list[str] | None = None):
        self.calls: list[str] = []
        self._expansions = expansions if expansions is not None else [
            "alternative phrasing one",
            "alternative phrasing two",
        ]

    async def expand(self, query: str) -> list[str]:
        self.calls.append(query)
        return list(self._expansions)


class FakeReranker:
    """Records calls and reverses candidate order (deterministic reordering)."""

    def __init__(self):
        self.calls: list[tuple[str, list[SearchResult]]] = []

    async def rerank(
        self, query: str, candidates: list[SearchResult]
    ) -> list[SearchResult]:
        self.calls.append((query, list(candidates)))
        return list(reversed(candidates))


@pytest.fixture
def populated_index(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    mem = workspace / "memory"
    mem.mkdir()

    (workspace / "MEMORY.md").write_text(
        "# About Me\n\n"
        "Name: Alex. Prefers Python. Lives in Berlin.\n"
        "Favorite editor: Neovim.\n"
    )
    (mem / "2026-02-17.md").write_text(
        "# 2026-02-17\n\n"
        "Started building Fera today. Set up the project scaffolding.\n"
        "Chose Python with uv for dependency management.\n"
        "Had pizza for dinner.\n"
    )
    (mem / "architecture.md").write_text(
        "# Architecture Decisions\n\n"
        "Using Claude Agent SDK for the agent core.\n"
        "Memory stored as plain markdown files.\n"
        "SQLite with FTS5 and sqlite-vec for search.\n"
    )

    idx = MemoryIndex(memory_dir=str(workspace), db_path=str(tmp_path / "memory.db"))
    idx.sync()
    return idx


# --- Protocol conformance ---


def test_fake_expander_satisfies_protocol():
    assert isinstance(FakeExpander(), QueryExpander)


def test_fake_reranker_satisfies_protocol():
    assert isinstance(FakeReranker(), Reranker)


# --- deep_search tests ---


@pytest.mark.anyio
async def test_deep_search_calls_expander_and_reranker(populated_index):
    """Both expander and reranker are called with correct arguments."""
    expander = FakeExpander()
    reranker = FakeReranker()

    results = await deep_search(
        populated_index,
        "Python dependency management",
        expander=expander,
        reranker=reranker,
    )

    # Expander was called with the original query
    assert expander.calls == ["Python dependency management"]

    # Reranker was called with the original query and some candidates
    assert len(reranker.calls) == 1
    rerank_query, rerank_candidates = reranker.calls[0]
    assert rerank_query == "Python dependency management"
    assert len(rerank_candidates) > 0
    assert all(isinstance(c, SearchResult) for c in rerank_candidates)

    # Results are returned
    assert len(results) > 0
    assert all(isinstance(r, SearchResult) for r in results)


@pytest.mark.anyio
async def test_deep_search_works_with_expander_failure(populated_index):
    """Empty expansions still works -- original query is always used."""
    expander = FakeExpander(expansions=[])
    reranker = FakeReranker()

    results = await deep_search(
        populated_index,
        "Python",
        expander=expander,
        reranker=reranker,
    )

    # Expander was called
    assert expander.calls == ["Python"]

    # Still got results from the original query alone
    assert len(results) > 0

    # Reranker was still called
    assert len(reranker.calls) == 1
