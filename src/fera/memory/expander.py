from __future__ import annotations

import logging
import re
from typing import Protocol, runtime_checkable

import anthropic

log = logging.getLogger(__name__)

EXPANSION_PROMPT = """\
Given this search query, generate 2-3 alternative phrasings that would help \
find relevant documents. Return only the alternative queries, one per line. \
Do not include the original query.

Query: {query}"""


@runtime_checkable
class QueryExpander(Protocol):
    async def expand(self, query: str) -> list[str]: ...


class HaikuQueryExpander:
    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        model: str = "claude-haiku-4-5-20251001",
    ):
        self._client = client
        self._model = model

    _PREFIX_RE = re.compile(r"^\d+[.)]\s*")

    def _parse_response(self, text: str) -> list[str]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return [self._PREFIX_RE.sub("", line) for line in lines]

    async def expand(self, query: str) -> list[str]:
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=256,
                messages=[
                    {"role": "user", "content": EXPANSION_PROMPT.format(query=query)}
                ],
            )
            return self._parse_response(response.content[0].text)
        except Exception:
            log.warning("Query expansion failed", exc_info=True)
            return []
