from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SearchResult:
    path: str
    start_line: int
    end_line: int
    score: float
    snippet: str
