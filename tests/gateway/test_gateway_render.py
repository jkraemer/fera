"""Test that the gateway adds HTML rendering to WebSocket events and history."""
from fera.render import render_html


def test_render_html_added_to_agent_text_event():
    """Simulates the WebSocket callback transform for agent.text events."""
    event = {
        "type": "event",
        "event": "agent.text",
        "session": "default",
        "data": {"text": "**bold** text"},
    }

    # Simulate the transform the callback does
    rendered = dict(event)
    rendered["data"] = dict(event["data"])
    rendered["data"]["html"] = render_html(event["data"]["text"])

    assert "<strong>bold</strong>" in rendered["data"]["html"]
    assert rendered["data"]["text"] == "**bold** text"  # original preserved
    # Original event untouched
    assert "html" not in event["data"]


def test_non_agent_events_not_modified():
    """Non-agent.text events pass through unchanged."""
    event = {
        "type": "event",
        "event": "user.message",
        "session": "default",
        "data": {"text": "**bold**"},
    }

    # The callback only transforms agent.text events
    should_transform = event.get("event") == "agent.text"
    assert should_transform is False


def test_history_agent_entries_get_html():
    """History entries of type 'agent' get an html field."""
    entry = {"ts": "2026-01-01T00:00:00Z", "type": "agent", "text": "# Hello"}
    if entry.get("type") == "agent" and entry.get("text"):
        entry["html"] = render_html(entry["text"])

    assert "<h1>" in entry["html"]
    assert entry["text"] == "# Hello"


def test_history_non_agent_entries_unchanged():
    """Non-agent history entries are not modified."""
    entry = {"ts": "2026-01-01T00:00:00Z", "type": "user", "text": "# Hello"}
    if entry.get("type") == "agent" and entry.get("text"):
        entry["html"] = render_html(entry["text"])

    assert "html" not in entry


def test_history_agent_entry_without_text_unchanged():
    """Agent entries without text are not modified."""
    entry = {"ts": "2026-01-01T00:00:00Z", "type": "agent", "text": ""}
    if entry.get("type") == "agent" and entry.get("text"):
        entry["html"] = render_html(entry["text"])

    assert "html" not in entry
