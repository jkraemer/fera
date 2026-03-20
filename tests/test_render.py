from fera.render import render_html, render_telegram_html, split_telegram_html


def test_render_html_heading():
    assert "<h2>" in render_html("## Hello")


def test_render_html_bold():
    assert "<strong>bold</strong>" in render_html("**bold**")


def test_render_html_italic():
    assert "<em>italic</em>" in render_html("*italic*")


def test_render_html_code_block():
    html = render_html("```python\nprint('hi')\n```")
    assert "<pre>" in html
    assert "<code" in html


def test_render_html_inline_code():
    assert "<code>" in render_html("use `foo()` here")


def test_render_html_unordered_list():
    html = render_html("- one\n- two")
    assert "<ul>" in html
    assert "<li>" in html


def test_render_html_ordered_list():
    html = render_html("1. one\n2. two")
    assert "<ol>" in html


def test_render_html_link():
    html = render_html("[example](https://example.com)")
    assert '<a href="https://example.com"' in html


def test_render_html_blockquote():
    html = render_html("> quoted text")
    assert "<blockquote>" in html


def test_render_html_table():
    md = "| A | B |\n|---|---|\n| 1 | 2 |"
    html = render_html(md)
    assert "<table>" in html


def test_render_html_hr():
    assert "<hr" in render_html("---")


def test_render_html_strips_script_tags():
    html = render_html("<script>alert('xss')</script>")
    assert "<script>" not in html
    assert "alert" not in html


def test_render_html_strips_img_tags():
    html = render_html('<img src="x" onerror="alert(1)">')
    assert "<img" not in html
    assert "onerror" not in html


def test_render_html_strips_style_tags():
    html = render_html("<style>body{display:none}</style>")
    assert "<style>" not in html


def test_render_html_strips_event_attributes():
    html = render_html('<a href="#" onclick="alert(1)">click</a>')
    assert "onclick" not in html


def test_render_html_allows_href_on_links():
    html = render_html("[test](https://example.com)")
    assert 'href="https://example.com"' in html


def test_render_html_strips_javascript_href():
    html = render_html('<a href="javascript:alert(1)">click</a>')
    assert "javascript:" not in html


def test_render_html_plain_text_passthrough():
    """Plain text without markdown just becomes a paragraph."""
    html = render_html("hello world")
    assert "hello world" in html


def test_render_html_strikethrough():
    html = render_html("~~deleted~~")
    assert "<del>" in html or "<s>" in html


def test_render_html_empty_string():
    assert render_html("") == ""


# --- Telegram HTML rendering ---


def test_telegram_bold():
    assert "<b>bold</b>" in render_telegram_html("**bold**")


def test_telegram_italic():
    assert "<i>italic</i>" in render_telegram_html("*italic*")


def test_telegram_strikethrough():
    assert "<s>strike</s>" in render_telegram_html("~~strike~~")


def test_telegram_inline_code():
    assert "<code>foo</code>" in render_telegram_html("`foo`")


def test_telegram_code_block():
    html = render_telegram_html("```python\nprint('hi')\n```")
    assert "<pre>" in html
    assert "<code" in html


def test_telegram_code_block_without_language():
    html = render_telegram_html("```\nhello\n```")
    assert "<pre>" in html
    assert "<code>" in html


def test_telegram_link():
    html = render_telegram_html("[test](https://example.com)")
    assert '<a href="https://example.com">' in html
    assert "test</a>" in html


def test_telegram_blockquote():
    html = render_telegram_html("> quoted")
    assert "<blockquote>" in html


def test_telegram_heading_becomes_bold():
    """Telegram has no heading tags -- headings degrade to bold."""
    html = render_telegram_html("## Section Title")
    assert "<b>Section Title</b>" in html
    assert "<h2>" not in html


def test_telegram_unordered_list():
    """Lists degrade to text lines with bullet characters."""
    html = render_telegram_html("- one\n- two")
    assert "\u2022 one" in html
    assert "\u2022 two" in html


def test_telegram_ordered_list():
    html = render_telegram_html("1. one\n2. two")
    assert "1. one" in html
    assert "2. two" in html


def test_telegram_table_becomes_preformatted():
    """Tables degrade to preformatted text."""
    md = "| A | B |\n|---|---|\n| 1 | 2 |"
    html = render_telegram_html(md)
    assert "<pre>" in html
    assert "A" in html


def test_telegram_hr_becomes_line():
    html = render_telegram_html("---")
    assert "---" in html or "\u2014" in html


def test_telegram_escapes_html_entities():
    """Angle brackets in text are escaped for Telegram HTML."""
    html = render_telegram_html("use <script> carefully")
    assert "&lt;script&gt;" in html


def test_telegram_plain_text():
    html = render_telegram_html("hello world")
    assert "hello world" in html


def test_telegram_empty_string():
    assert render_telegram_html("") == ""


# --- Telegram message splitting ---

TELEGRAM_LIMIT = 4096


def test_split_short_message_returns_single_chunk():
    chunks = split_telegram_html("short message")
    assert len(chunks) == 1
    assert chunks[0] == "short message"


def test_split_long_message_respects_limit():
    paragraphs = [f"Paragraph {i}. " + "x" * 200 for i in range(30)]
    text = "\n\n".join(paragraphs)
    chunks = split_telegram_html(text)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= TELEGRAM_LIMIT


def test_split_preserves_all_content():
    paragraphs = [f"Paragraph {i}. " + "x" * 200 for i in range(30)]
    text = "\n\n".join(paragraphs)
    chunks = split_telegram_html(text)
    joined = "\n\n".join(chunks)
    for p in paragraphs:
        assert p in joined


def test_split_single_huge_paragraph():
    """A single paragraph exceeding the limit is included as-is."""
    huge = "x" * 5000
    chunks = split_telegram_html(huge)
    assert len(chunks) == 1
    assert chunks[0] == huge


def test_split_empty_string():
    assert split_telegram_html("") == [""]
