import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from fera.memory.expander import HaikuQueryExpander, QueryExpander


def test_parse_expansion_response():
    """Multi-line response parsed into list of queries."""
    expander = HaikuQueryExpander.__new__(HaikuQueryExpander)
    text = "How to configure Python logging\nPython logging setup guide\nBest practices for Python logs"
    result = expander._parse_response(text)
    assert result == [
        "How to configure Python logging",
        "Python logging setup guide",
        "Best practices for Python logs",
    ]


def test_parse_expansion_strips_blanks():
    """Blank lines and leading/trailing whitespace stripped."""
    expander = HaikuQueryExpander.__new__(HaikuQueryExpander)
    text = "\n  first query  \n\n  second query \n\n"
    result = expander._parse_response(text)
    assert result == ["first query", "second query"]


def test_parse_expansion_empty():
    """Empty or whitespace-only response returns empty list."""
    expander = HaikuQueryExpander.__new__(HaikuQueryExpander)
    assert expander._parse_response("") == []
    assert expander._parse_response("   \n  \n  ") == []


def test_haiku_expander_satisfies_protocol():
    """HaikuQueryExpander is a subclass of QueryExpander protocol."""
    assert issubclass(HaikuQueryExpander, QueryExpander)


def test_parse_expansion_strips_numbered_prefixes():
    """Numbered list prefixes like '1. ' or '2) ' are stripped."""
    expander = HaikuQueryExpander.__new__(HaikuQueryExpander)
    text = "1. How to configure Python logging\n2. Python logging setup guide\n3) Best practices for Python logs"
    result = expander._parse_response(text)
    assert result == [
        "How to configure Python logging",
        "Python logging setup guide",
        "Best practices for Python logs",
    ]


def test_parse_expansion_preserves_non_numbered_lines():
    """Lines that don't start with numbered prefixes are left alone."""
    expander = HaikuQueryExpander.__new__(HaikuQueryExpander)
    text = "How to configure Python logging\n2. Python logging setup guide"
    result = expander._parse_response(text)
    assert result == [
        "How to configure Python logging",
        "Python logging setup guide",
    ]


@pytest.mark.asyncio
async def test_expand_logs_warning_on_api_failure(caplog):
    """API exceptions are logged as warnings, not silently swallowed."""
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(side_effect=RuntimeError("connection failed"))
    expander = HaikuQueryExpander(client=mock_client)

    with caplog.at_level(logging.WARNING, logger="fera.memory.expander"):
        result = await expander.expand("test query")

    assert result == []
    assert "connection failed" in caplog.text
