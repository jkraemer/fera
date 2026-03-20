from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from fera.memory.registry import AgentRegistry

log = logging.getLogger(__name__)


_MUTATION_EVENTS = frozenset({"created", "modified", "deleted", "moved"})


class _MarkdownHandler(FileSystemEventHandler):
    """Watchdog handler that fires a callback on memory .md file mutations."""

    def __init__(self, agent: str, workspace: Path, callback):
        self._agent = agent
        self._workspace = workspace
        self._callback = callback

    def _is_memory_path(self, path: str) -> bool:
        """Check if path is MEMORY.md or under memory/ in the workspace."""
        try:
            rel = Path(path).relative_to(self._workspace)
        except ValueError:
            return False
        parts = rel.parts
        if not parts or rel.suffix != ".md":
            return False
        return rel.name == "MEMORY.md" and len(parts) == 1 or parts[0] == "memory"

    def on_any_event(self, event: FileSystemEvent):
        if event.is_directory or event.event_type not in _MUTATION_EVENTS:
            return
        paths = [event.src_path]
        dest = getattr(event, "dest_path", None)
        if dest:
            paths.append(dest)
        if any(self._is_memory_path(p) for p in paths):
            self._callback(self._agent)


class MemoryWatcher:
    """Watches agent workspace directories and triggers debounced syncs."""

    def __init__(
        self,
        registry: AgentRegistry,
        debounce_seconds: float = 1.0,
    ):
        self._registry = registry
        self._debounce = debounce_seconds
        self._observer = Observer()
        self._pending: dict[str, asyncio.TimerHandle] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = False

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start watching all discovered agent workspaces."""
        self._loop = loop
        for agent in self._registry.discover():
            workspace = self._registry.workspace_dir(agent)
            handler = _MarkdownHandler(agent, workspace, self._on_change)
            self._observer.schedule(handler, str(workspace), recursive=True)
        self._observer.start()
        self._started = True

    def _on_change(self, agent: str) -> None:
        """Called from watchdog thread -- schedule debounced sync on event loop."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._debounce_sync, agent)

    def _debounce_sync(self, agent: str) -> None:
        """Called on event loop thread -- manage debounce timer."""
        if agent in self._pending:
            self._pending[agent].cancel()
        self._pending[agent] = self._loop.call_later(
            self._debounce, self._sync, agent
        )

    def _sync(self, agent: str) -> None:
        """Called on event loop thread -- perform the sync."""
        self._pending.pop(agent, None)
        try:
            self._registry.sync_agent(agent)
            log.debug("Synced agent %s", agent)
        except Exception:
            log.warning("Sync failed for agent %s", agent, exc_info=True)

    def stop(self) -> None:
        """Stop watching and cancel pending syncs."""
        if self._started:
            self._observer.stop()
            self._observer.join()
            self._started = False
        for handle in self._pending.values():
            handle.cancel()
        self._pending.clear()
