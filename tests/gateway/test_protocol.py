import json
import uuid

import pytest

from fera.gateway.protocol import (
    make_request,
    make_response,
    make_event,
    parse_frame,
    strip_silent_suffix,
    is_silent_response,
)


def test_make_request():
    frame = make_request("chat.send", {"text": "hello", "session": "default"})
    assert frame["type"] == "req"
    assert frame["method"] == "chat.send"
    assert frame["params"]["text"] == "hello"
    # id should be a valid UUID
    uuid.UUID(frame["id"])


def test_make_response_ok():
    frame = make_response("abc-123", payload={"sessions": []})
    assert frame["type"] == "res"
    assert frame["id"] == "abc-123"
    assert frame["ok"] is True
    assert frame["payload"] == {"sessions": []}


def test_make_response_error():
    frame = make_response("abc-123", error="something went wrong")
    assert frame["type"] == "res"
    assert frame["ok"] is False
    assert frame["error"] == "something went wrong"


def test_make_event():
    frame = make_event("agent.text", session="default", data={"text": "hi"})
    assert frame["type"] == "event"
    assert frame["event"] == "agent.text"
    assert frame["session"] == "default"
    assert frame["data"]["text"] == "hi"


def test_parse_frame_request():
    raw = json.dumps({"type": "req", "id": "x", "method": "chat.send", "params": {}})
    frame = parse_frame(raw)
    assert frame["type"] == "req"
    assert frame["method"] == "chat.send"


def test_parse_frame_event():
    raw = json.dumps({"type": "event", "event": "agent.text", "session": "s", "data": {}})
    frame = parse_frame(raw)
    assert frame["type"] == "event"


def test_parse_frame_invalid_json():
    frame = parse_frame("not json")
    assert frame is None


def test_parse_frame_missing_type():
    raw = json.dumps({"id": "x", "method": "chat.send"})
    frame = parse_frame(raw)
    assert frame is None


def test_roundtrip_serialization():
    original = make_request("session.list", {})
    raw = json.dumps(original)
    parsed = parse_frame(raw)
    assert parsed == original


# --- strip_silent_suffix tests ---


class TestStripSilentSuffix:
    """Tests for strip_silent_suffix()."""

    @pytest.mark.parametrize("marker", ["HEARTBEAT_OK", "(HEARTBEAT_OK)", "(silent)"])
    def test_exact_silent_markers_return_empty(self, marker):
        assert strip_silent_suffix(marker) == ""

    @pytest.mark.parametrize("marker", ["HEARTBEAT_OK", "(HEARTBEAT_OK)", "(silent)"])
    def test_exact_silent_markers_with_whitespace(self, marker):
        assert strip_silent_suffix(f"  {marker}  ") == ""

    def test_empty_string(self):
        assert strip_silent_suffix("") == ""

    def test_whitespace_only(self):
        assert strip_silent_suffix("   \n\t  ") == ""

    def test_suffix_stripped_from_content(self):
        assert strip_silent_suffix("Some content HEARTBEAT_OK") == "Some content"

    def test_suffix_with_trailing_whitespace(self):
        assert strip_silent_suffix("Content HEARTBEAT_OK  \n") == "Content"

    def test_suffix_with_extra_spaces_before_marker(self):
        assert strip_silent_suffix("Content   HEARTBEAT_OK") == "Content"

    def test_real_briefing_with_suffix(self):
        briefing = "Good morning, Alex. Here is your daily briefing."
        text = f"{briefing} HEARTBEAT_OK"
        assert strip_silent_suffix(text) == briefing

    def test_no_suffix_returns_text_unchanged(self):
        text = "Just regular content here"
        assert strip_silent_suffix(text) == text

    def test_no_suffix_rstrips_result(self):
        assert strip_silent_suffix("Content with trailing space   ") == "Content with trailing space"

    def test_heartbeat_ok_in_middle_not_stripped(self):
        text = "Before HEARTBEAT_OK and after"
        assert strip_silent_suffix(text) == text

    def test_multiline_with_suffix_at_end(self):
        text = "Line one\nLine two HEARTBEAT_OK"
        assert strip_silent_suffix(text) == "Line one\nLine two"


# --- is_silent_response tests ---


class TestIsSilentResponse:
    """Tests for is_silent_response()."""

    @pytest.mark.parametrize("marker", ["HEARTBEAT_OK", "(HEARTBEAT_OK)", "(silent)"])
    def test_silent_markers_are_silent(self, marker):
        assert is_silent_response(marker) is True

    def test_empty_string_is_silent(self):
        assert is_silent_response("") is True

    def test_whitespace_only_is_silent(self):
        assert is_silent_response("   \n  ") is True

    def test_real_content_is_not_silent(self):
        assert is_silent_response("Hello, how can I help?") is False

    def test_content_with_heartbeat_suffix_is_not_silent(self):
        assert is_silent_response("Some useful reply HEARTBEAT_OK") is False

    def test_padded_silent_marker_is_silent(self):
        assert is_silent_response("  HEARTBEAT_OK  ") is True
