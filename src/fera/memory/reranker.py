from __future__ import annotations

import json
import logging
from dataclasses import replace
from typing import Protocol, runtime_checkable

import anthropic

log = logging.getLogger(__name__)

from fera.memory.types import SearchResult

RERANK_PROMPT = """\
Rate each document's relevance to the query on a scale of 0-10.
Return a JSON array of objects with "index" and "score" fields, nothing else.

Query: {query}

Documents:
{documents}"""


@runtime_checkable
class Reranker(Protocol):
    async def rerank(
        self, query: str, candidates: list[SearchResult]
    ) -> list[SearchResult]: ...


class HaikuReranker:
    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        model: str = "claude-haiku-4-5-20251001",
    ):
        self._client = client
        self._model = model

    def _format_documents(self, candidates: list[SearchResult]) -> str:
        return "\n".join(
            f"[{i}] {c.snippet}" for i, c in enumerate(candidates)
        )

    def _parse_response(
        self, text: str, candidates: list[SearchResult]
    ) -> list[SearchResult]:
        try:
            data = json.loads(text)
            if not isinstance(data, list):
                return candidates
            scored = []
            seen: set[int] = set()
            for item in data:
                idx = item["index"]
                score = item["score"]
                if not (0 <= idx < len(candidates)):
                    continue
                if idx in seen:
                    continue
                seen.add(idx)
                scored.append(replace(candidates[idx], score=score / 10.0))
            if not scored:
                return candidates
            scored.sort(key=lambda r: r.score, reverse=True)
            return scored
        except (json.JSONDecodeError, KeyError, TypeError):
            return candidates

    async def rerank(
        self, query: str, candidates: list[SearchResult]
    ) -> list[SearchResult]:
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                messages=[
                    {
                        "role": "user",
                        "content": RERANK_PROMPT.format(
                            query=query,
                            documents=self._format_documents(candidates),
                        ),
                    }
                ],
            )
            return self._parse_response(response.content[0].text, candidates)
        except Exception:
            log.warning("Reranking failed", exc_info=True)
            return candidates
