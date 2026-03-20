"""Markdown rendering for chat output channels."""
from __future__ import annotations

from typing import Any, Dict, Optional

import mistune
import nh3
from mistune.core import BaseRenderer, BlockState
from mistune.util import escape as escape_text

# Tags and attributes allowed in web UI HTML output
_HTML_ALLOWED_TAGS = {
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr",
    "strong", "em", "del", "s",
    "ul", "ol", "li",
    "pre", "code",
    "blockquote",
    "a",
    "table", "thead", "tbody", "tr", "th", "td",
}

_HTML_ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    "a": {"href"},
    "code": {"class"},  # language class from fenced code blocks
}

# escape=False: let raw HTML pass through to nh3 for proper sanitization
_md = mistune.create_markdown(escape=False, plugins=["table", "strikethrough"])


def render_html(markdown: str) -> str:
    """Convert markdown to sanitized HTML for the web UI."""
    if not markdown:
        return ""
    raw_html = _md(markdown)
    return nh3.clean(
        raw_html,
        tags=_HTML_ALLOWED_TAGS,
        attributes=_HTML_ALLOWED_ATTRIBUTES,
        url_schemes={"http", "https", "mailto"},
    )


# ---------------------------------------------------------------------------
# Telegram HTML renderer
# ---------------------------------------------------------------------------


class _TelegramRenderer(BaseRenderer):
    """Render markdown as Telegram-compatible HTML.

    Telegram only supports: b, i, u, s, code, pre, a, blockquote.
    Everything else degrades gracefully to plain text or allowed tags.
    """

    NAME = "html"  # plugins check this to register their renderers

    def render_token(self, token: Dict[str, Any], state: BlockState) -> str:
        """Dispatch rendering based on token type.

        List and table tokens are handled at the token level because they
        need access to the full token structure (item indices, cell grid).
        Everything else follows the HTMLRenderer pattern.
        """
        tok_type = token["type"]

        # Token-level handlers that need full structure before children
        # are flattened into a single string
        if tok_type == "list":
            return self._render_list(token, state)
        if tok_type == "table":
            return self._render_table(token, state)

        func = self._get_method(tok_type)

        if "raw" in token:
            text = token["raw"]
        elif "children" in token:
            text = self.render_tokens(token["children"], state)
        else:
            attrs = token.get("attrs")
            if attrs:
                return func(**attrs)
            return func()

        attrs = token.get("attrs")
        if attrs:
            return func(text, **attrs)
        return func(text)

    # -- inline tokens --

    def text(self, text: str) -> str:
        return escape_text(text)

    def emphasis(self, text: str) -> str:
        return "<i>" + text + "</i>"

    def strong(self, text: str) -> str:
        return "<b>" + text + "</b>"

    def codespan(self, text: str) -> str:
        return "<code>" + escape_text(text) + "</code>"

    def link(self, text: str, url: str, title: Optional[str] = None) -> str:
        return '<a href="' + escape_text(url) + '">' + text + "</a>"

    def image(self, text: str, url: str, title: Optional[str] = None) -> str:
        # Telegram has no image tag; degrade to a link
        return '<a href="' + escape_text(url) + '">' + escape_text(text) + "</a>"

    def linebreak(self) -> str:
        return "\n"

    def softbreak(self) -> str:
        return "\n"

    def inline_html(self, html: str) -> str:
        return escape_text(html)

    # -- block tokens --

    def paragraph(self, text: str) -> str:
        return text + "\n\n"

    def heading(self, text: str, level: int, **attrs: Any) -> str:
        return "<b>" + text + "</b>\n"

    def blank_line(self) -> str:
        return ""

    def thematic_break(self) -> str:
        return "---\n"

    def block_text(self, text: str) -> str:
        return text

    def block_code(self, code: str, info: Optional[str] = None) -> str:
        html = "<pre><code"
        if info is not None:
            lang = info.strip().split(None, 1)[0] if info.strip() else ""
            if lang:
                html += ' class="language-' + escape_text(lang) + '"'
        html += ">" + escape_text(code) + "</code></pre>\n"
        return html

    def block_quote(self, text: str) -> str:
        return "<blockquote>" + text + "</blockquote>\n"

    def block_html(self, html: str) -> str:
        return escape_text(html.strip()) + "\n"

    def block_error(self, text: str) -> str:
        return escape_text(text) + "\n"

    # -- list handling (token-level, since lists have complex structure) --

    def _render_list(self, token: Dict[str, Any], state: BlockState) -> str:
        attrs = token["attrs"]
        ordered = attrs["ordered"]
        start = attrs.get("start", 1)
        lines: list[str] = []

        for item in token["children"]:
            item_text = self._render_list_item_text(item, state)
            if ordered:
                lines.append(str(start) + ". " + item_text)
                start += 1
            else:
                lines.append("\u2022 " + item_text)

        return "\n".join(lines) + "\n"

    def _render_list_item_text(
        self, item: Dict[str, Any], state: BlockState,
    ) -> str:
        """Render the children of a list_item into plain inline text."""
        parts: list[str] = []
        for child in item["children"]:
            if child["type"] == "blank_line":
                continue
            parts.append(self.render_token(child, state))
        return "".join(parts).strip()

    def list_item(self, text: str) -> str:
        # Not called directly; list() handles items at the token level
        return text

    # -- table handling (degrade to preformatted text) --

    def _render_table(self, token: Dict[str, Any], state: BlockState) -> str:
        rows: list[list[str]] = []
        for section in token["children"]:
            if section["type"] in ("table_head", "table_body"):
                for row_or_cell in section["children"]:
                    if row_or_cell["type"] == "table_cell":
                        # table_head has cells directly
                        cells = section["children"]
                        row = [
                            self.render_tokens(c["children"], state).strip()
                            for c in cells
                        ]
                        rows.append(row)
                        break
                    elif row_or_cell["type"] == "table_row":
                        cells = row_or_cell["children"]
                        row = [
                            self.render_tokens(c["children"], state).strip()
                            for c in cells
                        ]
                        rows.append(row)

        if not rows:
            return ""

        # Calculate column widths
        col_count = max(len(r) for r in rows)
        col_widths = [0] * col_count
        for row in rows:
            for i, cell in enumerate(row):
                col_widths[i] = max(col_widths[i], len(cell))

        # Format as aligned plain-text table
        lines: list[str] = []
        for row in rows:
            parts = []
            for i in range(col_count):
                cell = row[i] if i < len(row) else ""
                parts.append(cell.ljust(col_widths[i]))
            lines.append(" | ".join(parts).rstrip())
        return "<pre>" + escape_text("\n".join(lines)) + "</pre>\n"

    # -- strikethrough (registered by the plugin since NAME == "html",
    #    but we override the default <del> rendering) --

    def strikethrough(self, text: str) -> str:
        return "<s>" + text + "</s>"


_telegram_renderer = _TelegramRenderer()
_telegram_md = mistune.Markdown(
    renderer=_telegram_renderer,
    plugins=[
        mistune.plugins.import_plugin("table"),
        mistune.plugins.import_plugin("strikethrough"),
    ],
)


TELEGRAM_MAX_LENGTH = 4096


def split_telegram_html(html: str) -> list[str]:
    """Split rendered Telegram HTML into chunks under the message limit.

    Splits at double-newline (paragraph) boundaries. If a single paragraph
    exceeds the limit, it is included as a single chunk (Telegram will
    accept slightly oversized messages in some cases, and mid-paragraph
    splitting would break HTML tags).
    """
    if not html or len(html) <= TELEGRAM_MAX_LENGTH:
        return [html]

    paragraphs = html.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        # +2 for the "\n\n" separator
        added_len = len(para) + (2 if current else 0)
        if current and current_len + added_len > TELEGRAM_MAX_LENGTH:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = len(para)
        else:
            current.append(para)
            current_len += added_len

    if current:
        chunks.append("\n\n".join(current))

    return chunks


def render_telegram_html(markdown: str) -> str:
    """Convert markdown to Telegram-compatible HTML.

    Only uses tags Telegram supports: b, i, u, s, code, pre, a, blockquote.
    Unsupported elements degrade to plain text or allowed equivalents.
    """
    if not markdown:
        return ""
    result = _telegram_md(markdown)
    return result.rstrip()
