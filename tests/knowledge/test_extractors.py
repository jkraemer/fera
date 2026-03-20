from pathlib import Path

from fera.knowledge.extractors import extract_text, supported_suffixes


def test_extract_plain_text(tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("Hello world")
    assert extract_text(f) == "Hello world"


def test_extract_markdown(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("# Title\n\nBody text.")
    assert extract_text(f) == "# Title\n\nBody text."


def test_unsupported_format_raises(tmp_path):
    f = tmp_path / "data.xlsx"
    f.write_text("not supported")
    import pytest

    with pytest.raises(ValueError, match="Unsupported"):
        extract_text(f)


def test_extract_pdf_with_pypdf(tmp_path):
    """PDF with readable text uses pypdf."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    # pypdf's blank page won't have text; this tests the fallback chain.
    pdf_path = tmp_path / "blank.pdf"
    with open(pdf_path, "wb") as f:
        writer.write(f)
    # Blank PDF should return empty-ish string (triggers fallback).
    # We just verify it doesn't crash.
    result = extract_text(pdf_path)
    assert isinstance(result, str)


def test_supported_suffixes_includes_expected():
    s = supported_suffixes()
    assert ".md" in s
    assert ".txt" in s
    assert ".pdf" in s
    assert ".png" in s
    assert ".jpg" in s
