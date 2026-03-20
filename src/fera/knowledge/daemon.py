"""Knowledge indexer daemon -- CLI entry point."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from pathlib import Path

from fera.knowledge.watcher import KnowledgeWatcher

log = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fera knowledge base indexer daemon"
    )
    parser.add_argument("watch_dir", help="Directory to watch (Syncthing folder)")
    parser.add_argument("output_dir", help="Staging area for processed content")
    parser.add_argument(
        "--debounce",
        type=float,
        default=2.0,
        help="Debounce interval in seconds (default: 2.0)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: INFO)",
    )
    return parser


async def _run(watch_dir: Path, output_dir: Path, debounce: float) -> None:
    loop = asyncio.get_running_loop()
    watcher = KnowledgeWatcher(
        watch_dir=watch_dir, output_dir=output_dir, debounce_seconds=debounce
    )
    stop_event = asyncio.Event()

    def _shutdown():
        log.info("Shutdown signal received")
        stop_event.set()

    loop.add_signal_handler(signal.SIGTERM, _shutdown)
    loop.add_signal_handler(signal.SIGINT, _shutdown)

    watcher.start(loop)
    log.info("Watching %s -> %s (debounce=%.1fs)", watch_dir, output_dir, debounce)

    try:
        await stop_event.wait()
    finally:
        watcher.stop()
        log.info("Daemon stopped")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    asyncio.run(
        _run(
            watch_dir=Path(args.watch_dir),
            output_dir=Path(args.output_dir),
            debounce=args.debounce,
        )
    )
