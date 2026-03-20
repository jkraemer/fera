"""Document chunking for extracted text."""

from __future__ import annotations

# Default: ~8k tokens * 4 chars/token = 32k chars
DEFAULT_MAX_CHARS = 32_000
DEFAULT_OVERLAP_CHARS = 800  # ~200 tokens


def chunk_text(
    text: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[str]:
    """Split text into chunks at paragraph boundaries with overlap.

    Splits on double newlines first (paragraphs). If the text has no
    double newlines, falls back to single newline boundaries.
    """
    text = text.strip()
    if not text:
        return []

    if len(text) <= max_chars:
        return [text]

    # Choose split boundary
    if "\n\n" in text:
        segments = text.split("\n\n")
        joiner = "\n\n"
    else:
        segments = text.split("\n")
        joiner = "\n"

    chunks: list[str] = []
    current_segments: list[str] = []
    current_len = 0

    for seg in segments:
        seg_len = len(seg) + len(joiner)
        if current_len + seg_len > max_chars and current_segments:
            chunks.append(joiner.join(current_segments))

            # Walk back from end to build overlap
            overlap_segs: list[str] = []
            overlap_len = 0
            for prev in reversed(current_segments):
                if overlap_len + len(prev) > overlap_chars:
                    break
                overlap_segs.insert(0, prev)
                overlap_len += len(prev) + len(joiner)

            current_segments = overlap_segs
            current_len = sum(len(s) + len(joiner) for s in current_segments)

        current_segments.append(seg)
        current_len += seg_len

    if current_segments:
        final = joiner.join(current_segments)
        if final.strip():
            chunks.append(final)

    return chunks
