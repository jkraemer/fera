# src/fera/sanitize.py
"""Untrusted content wrapping and prompt sanitization."""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Awaitable, Callable

log = logging.getLogger(__name__)

_TAG = "untrusted"


def _escape_tags(content: str) -> str:
    """Escape <untrusted and </untrusted> inside content to prevent breakout."""
    content = content.replace(f"</{_TAG}>", f"&lt;/{_TAG}&gt;")
    content = content.replace(f"<{_TAG}", f"&lt;{_TAG}")
    return content


def wrap_untrusted(content: str, source: str, **attrs: str) -> str:
    """Wrap content in <untrusted> tags with source metadata.

    Escapes any <untrusted or </untrusted> in content before wrapping.
    Scans for injection patterns and fires alert handler if matches found.
    """
    content = sanitize_untrusted_content(content)
    escaped = _escape_tags(content)

    # Injection detection (non-blocking)
    matches = scan_for_injection(content)
    if matches:
        names = ", ".join(m.pattern_name for m in matches)
        log.warning("Injection pattern detected from %s: %s", source, names)
        if _alert_handler is not None:
            excerpt = content[:200]
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_alert_handler(source, excerpt, matches))
            except RuntimeError:
                pass  # no event loop — warning log is enough

    attr_parts = [f'source="{source}"']
    for k, v in attrs.items():
        attr_parts.append(f'{k}="{v}"')
    attr_str = " ".join(attr_parts)
    return f"<{_TAG} {attr_str}>\n{escaped}\n</{_TAG}>"


_STRIP_CATEGORIES = frozenset(("Cc", "Cf", "Zl", "Zp"))
_SAFE_WHITESPACE = frozenset(("\n", "\r", "\t"))


def sanitize_untrusted_content(value: str) -> str:
    """Strip dangerous Unicode characters from untrusted content.

    Like sanitize_for_prompt but preserves newlines, carriage returns,
    and tabs — suitable for multi-line content like emails or documents.

    Strips: control chars (Cc except \\n \\r \\t), format chars (Cf),
    line separators (Zl U+2028), paragraph separators (Zp U+2029).
    """
    return "".join(
        ch for ch in value
        if ch in _SAFE_WHITESPACE
        or unicodedata.category(ch) not in _STRIP_CATEGORIES
    )


def sanitize_for_prompt(value: str) -> str:
    """Strip Unicode control/format characters from a value for safe prompt injection.

    Removes characters in categories Cc (control), Cf (format),
    and line/paragraph separators (Zl, Zp). Preserves normal text
    and spaces.
    """
    return "".join(
        ch for ch in value
        if unicodedata.category(ch) not in _STRIP_CATEGORIES
    )


# --- Injection pattern detection ---


@dataclass
class InjectionMatch:
    """A detected injection pattern match."""
    pattern_name: str
    matched_text: str
    position: int


INJECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("ignore_previous", re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?)", re.I)),
    ("disregard_previous", re.compile(r"disregard\s+(all\s+)?(previous|prior|above)", re.I)),
    ("forget_instructions", re.compile(r"forget\s+(everything|all(\s+your)?|your)\s+(instructions?|rules?|guidelines?)", re.I)),
    ("role_hijack", re.compile(r"you\s+are\s+now\s+(a|an)\s+", re.I)),
    ("new_instructions", re.compile(r"new\s+instructions?:", re.I)),
    ("system_override", re.compile(r"system\s*:?\s*(prompt|override|command)", re.I)),
    ("exec_command", re.compile(r"\bexec\b.*command\s*=", re.I)),
    ("elevated_flag", re.compile(r"elevated\s*=\s*true", re.I)),
    ("rm_rf", re.compile(r"rm\s+-rf", re.I)),
    ("delete_all", re.compile(r"delete\s+all\s+(emails?|files?|data)", re.I)),
    ("system_xml_tag", re.compile(r"</?system>", re.I)),
    ("chat_boundary", re.compile(r"]\s*\n\s*\[?(system|assistant|user)\]?:", re.I)),
]


def scan_for_injection(content: str) -> list[InjectionMatch]:
    """Scan content for known prompt injection patterns.

    Returns a list of matches. Empty list means no patterns detected.
    """
    matches: list[InjectionMatch] = []
    for name, pattern in INJECTION_PATTERNS:
        for m in pattern.finditer(content):
            matches.append(InjectionMatch(
                pattern_name=name,
                matched_text=m.group(),
                position=m.start(),
            ))
    return matches


# --- Alert hook ---

_AlertHandler = Callable[[str, str, list[InjectionMatch]], Awaitable[None]]
_alert_handler: _AlertHandler | None = None


def set_alert_handler(handler: _AlertHandler | None) -> None:
    """Register (or clear) an async callback for injection alerts.

    Handler signature: (source, content_excerpt, matches) -> None
    """
    global _alert_handler
    _alert_handler = handler
