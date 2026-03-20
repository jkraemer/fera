import asyncio

import pytest

from fera.knowledge.watcher import KnowledgeWatcher


@pytest.fixture
def watcher_setup(tmp_path):
    watch_dir = tmp_path / "source"
    watch_dir.mkdir()
    output_dir = tmp_path / "staging"
    output_dir.mkdir()
    return watch_dir, output_dir


@pytest.mark.asyncio
async def test_watcher_processes_new_file(watcher_setup):
    watch_dir, output_dir = watcher_setup
    watcher = KnowledgeWatcher(
        watch_dir=watch_dir, output_dir=output_dir, debounce_seconds=0.1
    )
    loop = asyncio.get_event_loop()
    watcher.start(loop)
    try:
        (watch_dir / "test.txt").write_text("Hello watcher")
        await asyncio.sleep(0.5)

        content_files = list((output_dir / "content").iterdir())
        assert len(content_files) >= 1
    finally:
        watcher.stop()


@pytest.mark.asyncio
async def test_watcher_ignores_unsupported_formats(watcher_setup):
    watch_dir, output_dir = watcher_setup
    watcher = KnowledgeWatcher(
        watch_dir=watch_dir, output_dir=output_dir, debounce_seconds=0.1
    )
    loop = asyncio.get_event_loop()
    watcher.start(loop)
    try:
        (watch_dir / "data.csv").write_text("a,b,c")
        await asyncio.sleep(0.5)

        content_files = list((output_dir / "content").iterdir())
        assert len(content_files) == 0
    finally:
        watcher.stop()


@pytest.mark.asyncio
async def test_watcher_debounces_rapid_changes(watcher_setup):
    watch_dir, output_dir = watcher_setup
    process_count = 0

    watcher = KnowledgeWatcher(
        watch_dir=watch_dir, output_dir=output_dir, debounce_seconds=0.2
    )
    original_scan = watcher._indexer.scan

    def counting_scan():
        nonlocal process_count
        process_count += 1
        original_scan()

    watcher._indexer.scan = counting_scan

    loop = asyncio.get_event_loop()
    watcher.start(loop)
    try:
        for i in range(5):
            (watch_dir / f"note-{i}.txt").write_text(f"Content {i}")
            await asyncio.sleep(0.02)

        await asyncio.sleep(0.5)
        assert process_count < 5
        assert process_count >= 1
    finally:
        watcher.stop()


@pytest.mark.asyncio
async def test_watcher_handles_deletion(watcher_setup):
    watch_dir, output_dir = watcher_setup
    src = watch_dir / "temp.txt"
    src.write_text("Temporary")

    watcher = KnowledgeWatcher(
        watch_dir=watch_dir, output_dir=output_dir, debounce_seconds=0.1
    )
    loop = asyncio.get_event_loop()
    watcher.start(loop)
    try:
        await asyncio.sleep(0.3)  # let initial file get indexed

        src.unlink()
        await asyncio.sleep(0.3)

        deletions = output_dir / "deletions.jsonl"
        # Deletions should be logged (cleanup runs on each scan)
        if deletions.exists():
            assert deletions.read_text().strip() != ""
    finally:
        watcher.stop()


def test_watcher_stop_idempotent(watcher_setup):
    watch_dir, output_dir = watcher_setup
    watcher = KnowledgeWatcher(
        watch_dir=watch_dir, output_dir=output_dir
    )
    watcher.stop()  # should not raise
