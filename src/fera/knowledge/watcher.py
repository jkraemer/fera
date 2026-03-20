"""File watcher for the knowledge indexer daemon."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from fera.knowledge.extractors import supported_suffixes
from fera.knowledge.indexer import KnowledgeIndexer

log = logging.getLogger(__name__)

_MUTATION_EVENTS = frozenset({"created", "modified", "deleted", "moved"})


class _Handler(FileSystemEventHandler):
    """Watchdog handler that fires callback on supported file mutations."""

    def __init__(self, callback) -> None:
        self._callback = callback

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory or event.event_type not in _MUTATION_EVENTS:
            return
        paths = [event.src_path]
        dest = getattr(event, "dest_path", None)
        if dest:
            paths.append(dest)
        suffixes = supported_suffixes()
        if any(Path(p).suffix.lower() in suffixes for p in paths):
            self._callback()
        elif event.event_type == "deleted":
            # Trigger on deletions of any tracked file, even unsupported suffix
            self._callback()


class KnowledgeWatcher:
    """Watches a directory and triggers indexer scans with debouncing."""

    def __init__(
        self,
        *,
        watch_dir: Path,
        output_dir: Path,
        debounce_seconds: float = 2.0,
    ) -> None:
        self._indexer = KnowledgeIndexer(watch_dir=watch_dir, output_dir=output_dir)
        self._debounce = debounce_seconds
        self._observer = Observer()
        self._handler = _Handler(self._on_change)
        self._watch_dir = watch_dir
        self._pending: asyncio.TimerHandle | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = False

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._observer.schedule(self._handler, str(self._watch_dir), recursive=True)
        self._observer.start()
        self._started = True
        # Run initial scan
        self._indexer.scan()
        self._indexer.cleanup_deleted()

    def _on_change(self) -> None:
        """Called from watchdog thread -- schedule debounced scan on event loop."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._debounce_scan)

    def _debounce_scan(self) -> None:
        """Called on event loop thread -- manage debounce timer."""
        if self._pending is not None:
            self._pending.cancel()
        self._pending = self._loop.call_later(self._debounce, self._do_scan)

    def _do_scan(self) -> None:
        """Called on event loop thread -- perform the scan."""
        self._pending = None
        try:
            self._indexer.scan()
            self._indexer.cleanup_deleted()
        except Exception:
            log.warning("Scan failed", exc_info=True)

    def stop(self) -> None:
        """Stop watching and cancel pending scans."""
        if self._started:
            self._observer.stop()
            self._observer.join()
            self._started = False
        if self._pending is not None:
            self._pending.cancel()
            self._pending = None
