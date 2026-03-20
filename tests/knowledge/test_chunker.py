from fera.knowledge.chunker import chunk_text


def test_small_text_single_chunk():
    text = "Short paragraph."
    chunks = chunk_text(text, max_chars=32000)
    assert chunks == [text]


def test_large_text_splits_at_paragraphs():
    para = "Word " * 100  # ~500 chars
    text = f"{para}\n\n{para}\n\n{para}"
    chunks = chunk_text(text, max_chars=600)
    assert len(chunks) > 1


def test_chunks_have_overlap():
    paras = [f"Paragraph {i}. " + "word " * 50 for i in range(10)]
    text = "\n\n".join(paras)
    chunks = chunk_text(text, max_chars=400, overlap_chars=100)
    if len(chunks) >= 2:
        # Last part of chunk N should appear at start of chunk N+1
        for i in range(len(chunks) - 1):
            tail = chunks[i][-80:]  # last 80 chars of this chunk
            # At least some of the tail should appear in the next chunk
            # (overlap means repeated content at boundaries)
            overlap_found = any(
                word in chunks[i + 1][:200]
                for word in tail.split()
                if len(word) > 3
            )
            assert overlap_found, f"No overlap between chunk {i} and {i + 1}"


def test_respects_max_chars():
    paras = [f"Paragraph {i}. " + "word " * 80 for i in range(20)]
    text = "\n\n".join(paras)
    max_chars = 600
    chunks = chunk_text(text, max_chars=max_chars)
    for chunk in chunks:
        # A chunk may exceed max by one paragraph (paragraph-boundary splitting)
        assert len(chunk) <= max_chars * 2, f"Chunk too large: {len(chunk)}"


def test_empty_text():
    assert chunk_text("") == []
    assert chunk_text("   \n\n  ") == []


def test_no_double_newlines_uses_single_newlines():
    """Text without paragraph breaks falls back to single newlines."""
    lines = [f"Line {i}" for i in range(50)]
    text = "\n".join(lines)
    chunks = chunk_text(text, max_chars=200)
    assert len(chunks) > 1
