from __future__ import annotations

import json
import re
import uuid
from typing import Any


def make_request(method: str, params: dict[str, Any] | None = None) -> dict:
    """Create a request frame."""
    return {
        "type": "req",
        "id": str(uuid.uuid4()),
        "method": method,
        "params": params if params is not None else {},
    }


def make_response(
    request_id: str,
    *,
    payload: Any = None,
    error: str | None = None,
) -> dict:
    """Create a response frame."""
    if error is not None:
        return {"type": "res", "id": request_id, "ok": False, "error": error}
    return {"type": "res", "id": request_id, "ok": True, "payload": payload}


def make_event(
    event: str,
    *,
    session: str,
    data: dict[str, Any] | None = None,
) -> dict:
    """Create an event frame."""
    return {
        "type": "event",
        "event": event,
        "session": session,
        "data": data if data is not None else {},
    }


def parse_frame(raw: str) -> dict | None:
    """Parse a raw JSON string into a frame dict. Returns None if invalid."""
    try:
        frame = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(frame, dict) or "type" not in frame:
        return None
    return frame


_SILENT_EXACT = frozenset({"HEARTBEAT_OK", "(HEARTBEAT_OK)", "(silent)"})
_HEARTBEAT_OK_SUFFIX = re.compile(r"\s*HEARTBEAT_OK\s*\Z")


def strip_silent_suffix(text: str) -> str:
    """Strip HEARTBEAT_OK suffix and exact silent markers, return cleaned text."""
    stripped = text.strip()
    if not stripped or stripped in _SILENT_EXACT:
        return ""
    return _HEARTBEAT_OK_SUFFIX.sub("", text).rstrip()


def is_silent_response(text: str) -> bool:
    """Return True if text has no content after stripping silent markers."""
    return strip_silent_suffix(text) == ""
