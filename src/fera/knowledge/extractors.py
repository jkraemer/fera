"""Text extraction for various file formats."""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

_PLAIN_SUFFIXES = frozenset({".md", ".txt"})
_PDF_SUFFIXES = frozenset({".pdf"})
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".tiff", ".tif"})
_ALL_SUPPORTED = _PLAIN_SUFFIXES | _PDF_SUFFIXES | _IMAGE_SUFFIXES

# Minimum characters from pypdf before we consider it a failed extraction
# (scanned PDFs often yield near-empty strings).
_MIN_PDF_TEXT_CHARS = 50


def supported_suffixes() -> frozenset[str]:
    return _ALL_SUPPORTED


def extract_text(path: Path) -> str:
    """Extract text content from a file.

    Raises ValueError for unsupported formats.
    """
    suffix = path.suffix.lower()
    if suffix in _PLAIN_SUFFIXES:
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix in _PDF_SUFFIXES:
        return _extract_pdf(path)
    if suffix in _IMAGE_SUFFIXES:
        return _extract_ocr(path)
    raise ValueError(f"Unsupported file type: {suffix}")


def _extract_pdf(path: Path) -> str:
    """Extract text from PDF with fallback chain: pypdf -> pdftotext -> OCR."""
    text = _try_pypdf(path)
    if text and len(text.strip()) >= _MIN_PDF_TEXT_CHARS:
        return text

    log.info("pypdf yielded little text for %s, trying pdftotext", path.name)
    text = _try_pdftotext(path)
    if text and len(text.strip()) >= _MIN_PDF_TEXT_CHARS:
        return text

    log.info("pdftotext yielded little text for %s, trying OCR", path.name)
    return _extract_ocr(path)


def _try_pypdf(path: Path) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(path)
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages)
    except Exception:
        log.warning("pypdf failed for %s", path.name, exc_info=True)
        return ""


def _try_pdftotext(path: Path) -> str:
    try:
        result = subprocess.run(
            ["pdftotext", str(path), "-"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            log.warning("pdftotext returned %d for %s", result.returncode, path.name)
            return ""
        return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        log.warning("pdftotext not available or timed out for %s", path.name)
        return ""


def _extract_ocr(path: Path) -> str:
    """OCR via tesseract. For PDFs, render pages to images first with pdftoppm."""
    if path.suffix.lower() in _PDF_SUFFIXES:
        return _ocr_pdf(path)
    return _ocr_image(path)


def _ocr_image(path: Path) -> str:
    try:
        result = subprocess.run(
            ["tesseract", str(path), "stdout"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            log.warning("tesseract returned %d for %s", result.returncode, path.name)
            return ""
        return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        log.warning("tesseract not available or timed out for %s", path.name)
        return ""


def _ocr_pdf(path: Path) -> str:
    """Render PDF to images with pdftoppm and OCR each page."""
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            subprocess.run(
                ["pdftoppm", "-png", str(path), f"{tmpdir}/page"],
                capture_output=True,
                timeout=120,
                check=True,
            )
        except (
            FileNotFoundError,
            subprocess.TimeoutExpired,
            subprocess.CalledProcessError,
        ):
            log.warning("pdftoppm failed for %s", path.name, exc_info=True)
            return ""

        pages = sorted(Path(tmpdir).glob("page-*.png"))
        texts = []
        for page_img in pages:
            text = _ocr_image(page_img)
            if text.strip():
                texts.append(text)
        return "\n".join(texts)
