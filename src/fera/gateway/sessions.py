from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from fera.config import DEFAULT_AGENT, FERA_HOME, workspace_dir


class SessionManager:
    """Manages session identity -> metadata mapping with JSON persistence.

    Session identity is a composite string "{agent}/{name}" (e.g. "forge/coding-1").
    """

    def __init__(self, path: Path | str, fera_home: Path = FERA_HOME):
        self._path = Path(path)
        self._fera_home = fera_home
        self._sessions: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            self._sessions = json.loads(self._path.read_text())
            # Backfill fields that may be missing from older sessions.json formats
            for key, session in self._sessions.items():
                if "id" not in session:
                    session["id"] = key
                if "workspace_dir" not in session:
                    agent = session.get("agent", DEFAULT_AGENT)
                    session["workspace_dir"] = str(workspace_dir(agent, self._fera_home))
                if "canary_token" not in session:
                    session["canary_token"] = uuid.uuid4().hex
            # Remove stale bare-key entries (no "/") when their composite form already exists
            stale = [k for k in list(self._sessions) if "/" not in k and self._to_key(k) in self._sessions]
            if stale:
                for k in stale:
                    del self._sessions[k]
                self._save()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._sessions, indent=2))

    def _to_key(self, session_id: str) -> str:
        """Normalise a session_id to a composite key.

        Bare names (no '/') are treated as DEFAULT_AGENT/{name}.
        """
        if "/" not in session_id:
            return f"{DEFAULT_AGENT}/{session_id}"
        return session_id

    def create(self, name: str, agent: str = DEFAULT_AGENT) -> dict[str, Any]:
        key = f"{agent}/{name}"
        if key in self._sessions:
            raise ValueError(f"Session '{name}' already exists for agent '{agent}'")
        self._sessions[key] = {
            "id": key,
            "name": name,
            "agent": agent,
            "workspace_dir": str(workspace_dir(agent, self._fera_home)),
            "canary_token": uuid.uuid4().hex,
        }
        self._save()
        return self._sessions[key].copy()

    def get(self, session_id: str) -> dict[str, Any] | None:
        key = self._to_key(session_id)
        info = self._sessions.get(key)
        return info.copy() if info else None

    def get_or_create(self, session_id: str) -> dict[str, Any]:
        key = self._to_key(session_id)
        if key not in self._sessions:
            agent, _, name = key.partition("/")
            return self.create(name, agent=agent)
        return self._sessions[key].copy()

    def list(self) -> list[dict[str, Any]]:
        return [info.copy() for info in self._sessions.values()]

    def sessions_for_agent(self, agent_name: str) -> list[dict[str, Any]]:
        """Return all sessions belonging to the given agent."""
        return [
            info.copy()
            for info in self._sessions.values()
            if info.get("agent") == agent_name
        ]

    def set_sdk_session_id(self, session_id: str, sdk_session_id: str) -> None:
        key = self._to_key(session_id)
        if key not in self._sessions:
            raise KeyError(f"Session '{session_id}' not found")
        self._sessions[key]["sdk_session_id"] = sdk_session_id
        self._save()

    def clear_sdk_session_id(self, session_id: str) -> None:
        key = self._to_key(session_id)
        if key not in self._sessions:
            raise KeyError(f"Session '{session_id}' not found")
        self._sessions[key].pop("sdk_session_id", None)
        self._sessions[key]["canary_token"] = uuid.uuid4().hex
        self._save()

    def set_last_inbound_adapter(self, session_id: str, adapter_name: str) -> None:
        """Record the adapter that last delivered an inbound user message."""
        key = self._to_key(session_id)
        if key not in self._sessions:
            raise KeyError(f"Session '{session_id}' not found")
        self._sessions[key]["last_inbound_adapter"] = adapter_name
        self._save()

    def delete(self, session_id: str) -> None:
        """Remove a session. No-op if the session does not exist."""
        key = self._to_key(session_id)
        if key in self._sessions:
            del self._sessions[key]
            self._save()
