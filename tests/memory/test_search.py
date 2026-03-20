import pytest

from fera.memory.index import MemoryIndex
from fera.memory.search import SearchResult, hybrid_search, rrf_fuse


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


# --- rrf_fuse unit tests ---


def test_rrf_fuse_basic():
    """Doc in multiple lists scores higher; higher ranks contribute more."""
    lists = [
        ["a", "b", "c"],
        ["a", "c", "d"],
    ]
    scores = rrf_fuse(lists, k=60)
    # a: rank 1 in both lists -> 2/(60+1) = 2/61
    # b: rank 2 in list 1 only -> 1/(60+2) = 1/62
    # c: rank 3 in list 1, rank 2 in list 2 -> 1/63 + 1/62
    # d: rank 3 in list 2 only -> 1/(60+3) = 1/63
    assert scores["a"] > scores["c"]  # a in both at rank 1, c at worse ranks
    assert scores["c"] > scores["b"]  # c in two lists beats b in one
    assert scores["b"] > scores["d"]  # b at rank 2 beats d at rank 3


def test_rrf_fuse_single_list():
    """Scores decrease with rank in a single list."""
    lists = [["x", "y", "z"]]
    scores = rrf_fuse(lists, k=60)
    assert scores["x"] > scores["y"] > scores["z"]


def test_rrf_fuse_empty():
    """Empty input returns empty dict."""
    assert rrf_fuse([]) == {}
    assert rrf_fuse([[]]) == {}


# --- hybrid_search tests ---


def test_search_returns_results(populated_index):
    results = hybrid_search(populated_index, "Python dependency management")
    assert len(results) > 0
    assert all(isinstance(r, SearchResult) for r in results)


def test_search_results_have_required_fields(populated_index):
    results = hybrid_search(populated_index, "architecture")
    assert len(results) > 0
    r = results[0]
    assert isinstance(r.path, str)
    assert isinstance(r.start_line, int)
    assert isinstance(r.end_line, int)
    assert isinstance(r.score, float)
    assert isinstance(r.snippet, str)


def test_search_results_are_sorted_by_score(populated_index):
    results = hybrid_search(populated_index, "Python")
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_search_respects_max_results(populated_index):
    results = hybrid_search(populated_index, "the", max_results=2)
    assert len(results) <= 2


def test_relevant_results_rank_higher(populated_index):
    results = hybrid_search(populated_index, "what editor does Alex use")
    assert len(results) > 0
    snippets = " ".join(r.snippet for r in results)
    assert "Neovim" in snippets


def test_fts_only_search(populated_index):
    results = hybrid_search(populated_index, "scaffolding", mode="fts")
    assert len(results) > 0
    assert "scaffolding" in results[0].snippet.lower()


def test_vector_only_search(populated_index):
    results = hybrid_search(populated_index, "food eaten", mode="vector")
    assert len(results) > 0
    snippets = " ".join(r.snippet for r in results)
    assert "pizza" in snippets


def test_hybrid_search_uses_rrf(populated_index):
    """Results from hybrid search are sorted by descending RRF score."""
    results = hybrid_search(populated_index, "Python project setup")
    assert len(results) >= 2
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)
    # All RRF scores should be positive
    assert all(s > 0 for s in scores)
