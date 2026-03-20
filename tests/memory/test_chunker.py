from fera.memory.chunker import chunk_markdown


SAMPLE_DOC = """\
# Heading

Paragraph one with some text that goes on for a while. This is meant to
represent a typical markdown document with multiple sections.

## Section Two

More content here. Decisions were made about the architecture.
We chose Python for its ecosystem.

## Section Three

Final section with concluding thoughts.
"""


def test_chunks_have_required_fields():
    chunks = chunk_markdown(SAMPLE_DOC, max_chars=200, overlap_chars=40)
    assert len(chunks) > 0
    for chunk in chunks:
        assert "text" in chunk
        assert "start_line" in chunk
        assert "end_line" in chunk
        assert isinstance(chunk["start_line"], int)
        assert isinstance(chunk["end_line"], int)
        assert chunk["start_line"] >= 1
        assert chunk["end_line"] >= chunk["start_line"]


def test_chunks_cover_all_lines():
    chunks = chunk_markdown(SAMPLE_DOC, max_chars=200, overlap_chars=40)
    lines = SAMPLE_DOC.splitlines()
    covered = set()
    for chunk in chunks:
        for i in range(chunk["start_line"], chunk["end_line"] + 1):
            covered.add(i)
    # Every non-empty line should be covered
    for i, line in enumerate(lines, 1):
        if line.strip():
            assert i in covered, f"Line {i} not covered: {line!r}"


def test_small_doc_single_chunk():
    doc = "# Title\n\nOne paragraph.\n"
    chunks = chunk_markdown(doc, max_chars=2000, overlap_chars=100)
    assert len(chunks) == 1


def test_chunks_respect_max_size():
    long_doc = "\n".join(f"Line {i}: some content here." for i in range(100))
    max_chars = 200
    max_line = max(len(line) for line in long_doc.splitlines(keepends=True))
    chunks = chunk_markdown(long_doc, max_chars=max_chars, overlap_chars=40)
    for chunk in chunks:
        # A chunk may exceed max_chars by at most one line (line-boundary splitting)
        assert len(chunk["text"]) <= max_chars + max_line, (
            f"Chunk too large: {len(chunk['text'])} chars"
        )


def test_overlap_between_consecutive_chunks():
    long_doc = "\n".join(f"Line {i}: content." for i in range(100))
    chunks = chunk_markdown(long_doc, max_chars=200, overlap_chars=40)
    if len(chunks) >= 2:
        for i in range(len(chunks) - 1):
            # The end of chunk i should overlap with the start of chunk i+1
            assert chunks[i]["end_line"] >= chunks[i + 1]["start_line"], (
                f"No overlap between chunk {i} and {i + 1}"
            )


def test_single_line_chunk_does_not_loop():
    """A short line followed by a long line must not cause an infinite loop.

    When only one short line fits in a chunk (the next line would exceed
    max_chars), the overlap logic must still advance start_idx.
    """
    # Line 0: 100 chars, Line 1: 1550 chars — only line 0 fits in a chunk
    doc = ("x" * 100) + "\n" + ("y" * 1550) + "\n" + "end\n"
    chunks = chunk_markdown(doc, max_chars=1600, overlap_chars=320)
    texts = [c["text"] for c in chunks]
    # Must terminate and cover all content
    assert len(chunks) >= 2
    joined = "".join(texts)
    assert "end" in joined


def test_line_longer_than_max_chars_does_not_loop():
    """A line exceeding max_chars must be emitted and the chunker must advance."""
    doc = ("z" * 3000) + "\n" + "after\n"
    chunks = chunk_markdown(doc, max_chars=1600, overlap_chars=320)
    texts = [c["text"] for c in chunks]
    assert len(chunks) >= 2
    assert "after" in "".join(texts)
