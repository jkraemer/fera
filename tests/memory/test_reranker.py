import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from fera.memory.reranker import HaikuReranker, Reranker
from fera.memory.types import SearchResult


def _make_candidates():
    """Three candidates in a fixed order for testing."""
    return [
        SearchResult(path="a.md", start_line=1, end_line=5, score=0.5, snippet="alpha"),
        SearchResult(path="b.md", start_line=1, end_line=5, score=0.4, snippet="bravo"),
        SearchResult(path="c.md", start_line=1, end_line=5, score=0.3, snippet="charlie"),
    ]


def test_parse_rerank_response():
    """Parses JSON scores, reorders by score descending, normalizes to 0-1."""
    reranker = HaikuReranker.__new__(HaikuReranker)
    candidates = _make_candidates()
    text = '[{"index": 0, "score": 3}, {"index": 1, "score": 9}, {"index": 2, "score": 6}]'

    result = reranker._parse_response(text, candidates)

    assert len(result) == 3
    # Sorted by score descending: bravo (9/10), charlie (6/10), alpha (3/10)
    assert result[0].snippet == "bravo"
    assert result[0].score == 0.9
    assert result[1].snippet == "charlie"
    assert result[1].score == 0.6
    assert result[2].snippet == "alpha"
    assert result[2].score == 0.3


def test_parse_rerank_fallback_on_bad_json():
    """Malformed JSON returns candidates unchanged."""
    reranker = HaikuReranker.__new__(HaikuReranker)
    candidates = _make_candidates()

    result = reranker._parse_response("not valid json {{{", candidates)

    assert result is candidates


def test_parse_rerank_fallback_on_wrong_structure():
    """Valid JSON but wrong shape returns candidates unchanged."""
    reranker = HaikuReranker.__new__(HaikuReranker)
    candidates = _make_candidates()

    # A dict instead of a list
    result = reranker._parse_response('{"index": 0, "score": 5}', candidates)
    assert result is candidates

    # List of strings instead of objects
    result = reranker._parse_response('["a", "b", "c"]', candidates)
    assert result is candidates


def test_parse_rerank_handles_out_of_range_index():
    """Out-of-range indices are skipped, valid ones still returned."""
    reranker = HaikuReranker.__new__(HaikuReranker)
    candidates = _make_candidates()
    text = '[{"index": 0, "score": 7}, {"index": 99, "score": 10}, {"index": 2, "score": 5}]'

    result = reranker._parse_response(text, candidates)

    # Only index 0 and 2 are valid
    assert len(result) == 2
    assert result[0].snippet == "alpha"
    assert result[0].score == 0.7
    assert result[1].snippet == "charlie"
    assert result[1].score == 0.5


def test_parse_rerank_skips_duplicate_indices():
    """Duplicate indices from LLM are ignored (first wins)."""
    reranker = HaikuReranker.__new__(HaikuReranker)
    candidates = _make_candidates()
    text = '[{"index": 0, "score": 7}, {"index": 0, "score": 3}, {"index": 1, "score": 5}]'

    result = reranker._parse_response(text, candidates)

    assert len(result) == 2
    assert result[0].snippet == "alpha"
    assert result[0].score == 0.7  # first occurrence wins
    assert result[1].snippet == "bravo"
    assert result[1].score == 0.5


def test_haiku_reranker_satisfies_protocol():
    """HaikuReranker is a subclass of the Reranker protocol."""
    assert issubclass(HaikuReranker, Reranker)


@pytest.mark.asyncio
async def test_rerank_logs_warning_on_api_failure(caplog):
    """API exceptions are logged as warnings, not silently swallowed."""
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(side_effect=RuntimeError("connection failed"))
    reranker = HaikuReranker(client=mock_client)
    candidates = _make_candidates()

    with caplog.at_level(logging.WARNING, logger="fera.memory.reranker"):
        result = await reranker.rerank("test query", candidates)

    assert result is candidates
    assert "connection failed" in caplog.text
