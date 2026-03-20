from __future__ import annotations

from typing import TypedDict

# Defaults match OpenClaw: ~400 tokens ~ 1600 chars, ~80 tokens ~ 320 chars
DEFAULT_MAX_CHARS = 1600
DEFAULT_OVERLAP_CHARS = 320


class Chunk(TypedDict):
    text: str
    start_line: int  # 1-indexed
    end_line: int  # 1-indexed, inclusive


def chunk_markdown(
    text: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[Chunk]:
    """Split markdown text into overlapping chunks on line boundaries."""
    lines = text.splitlines(keepends=True)
    if not lines:
        return []

    chunks: list[Chunk] = []
    start_idx = 0  # 0-indexed line index

    while start_idx < len(lines):
        # Accumulate lines up to max_chars
        char_count = 0
        end_idx = start_idx
        while end_idx < len(lines) and char_count + len(lines[end_idx]) <= max_chars:
            char_count += len(lines[end_idx])
            end_idx += 1

        # If we couldn't fit even one line, take it anyway
        if end_idx == start_idx:
            end_idx = start_idx + 1

        chunk_text = "".join(lines[start_idx:end_idx])
        chunks.append(
            Chunk(
                text=chunk_text,
                start_line=start_idx + 1,
                end_line=end_idx,
            )
        )

        if end_idx >= len(lines):
            break

        # Step back by overlap_chars to find the next start,
        # but never further than start_idx + 1 to guarantee progress.
        overlap_count = 0
        next_start = end_idx
        while next_start > start_idx + 1 and overlap_count < overlap_chars:
            next_start -= 1
            overlap_count += len(lines[next_start])

        start_idx = next_start

    return chunks
