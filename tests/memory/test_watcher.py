import asyncio

import pytest

from fera.memory.registry import AgentRegistry
from fera.memory.watcher import MemoryWatcher


@pytest.fixture
def agent_setup(tmp_path):
    """Create a minimal agent workspace for watcher tests."""
    workspace = tmp_path / "main" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "memory").mkdir()
    (workspace / "MEMORY.md").write_text("# Facts\n\nOriginal content.\n")
    (tmp_path / "main" / "data").mkdir()
    registry = AgentRegistry(tmp_path)
    registry.sync_agent("main")
    return tmp_path, registry


@pytest.mark.asyncio
async def test_watcher_syncs_on_file_change(agent_setup):
    agents_dir, registry = agent_setup
    workspace = agents_dir / "main" / "workspace"
    loop = asyncio.get_event_loop()

    watcher = MemoryWatcher(registry, debounce_seconds=0.1)
    watcher.start(loop)
    try:
        index = registry.get_index("main")
        initial_count = len(index.all_chunks())

        (workspace / "memory" / "new-note.md").write_text("# New\n\nNew content here.\n")

        await asyncio.sleep(0.5)

        new_count = len(index.all_chunks())
        assert new_count > initial_count
    finally:
        watcher.stop()


@pytest.mark.asyncio
async def test_watcher_ignores_non_markdown(agent_setup):
    agents_dir, registry = agent_setup
    workspace = agents_dir / "main" / "workspace"
    loop = asyncio.get_event_loop()

    watcher = MemoryWatcher(registry, debounce_seconds=0.1)
    watcher.start(loop)
    try:
        index = registry.get_index("main")
        initial_count = len(index.all_chunks())

        (workspace / "notes.txt").write_text("not markdown")
        await asyncio.sleep(0.5)

        assert len(index.all_chunks()) == initial_count
    finally:
        watcher.stop()


@pytest.mark.asyncio
async def test_watcher_debounces_rapid_changes(agent_setup):
    agents_dir, registry = agent_setup
    workspace = agents_dir / "main" / "workspace"
    loop = asyncio.get_event_loop()

    sync_count = 0
    original_sync = registry.sync_agent

    def counting_sync(agent):
        nonlocal sync_count
        sync_count += 1
        original_sync(agent)

    registry.sync_agent = counting_sync

    watcher = MemoryWatcher(registry, debounce_seconds=0.2)
    watcher.start(loop)
    try:
        for i in range(5):
            (workspace / "memory" / f"note-{i}.md").write_text(f"# Note {i}\n\nContent.\n")
            await asyncio.sleep(0.02)

        await asyncio.sleep(0.5)

        assert sync_count < 5
        assert sync_count >= 1
    finally:
        watcher.stop()


@pytest.mark.asyncio
async def test_watcher_ignores_read_only_access(agent_setup):
    """Reading .md files should not trigger a sync (avoids feedback loop)."""
    agents_dir, registry = agent_setup
    workspace = agents_dir / "main" / "workspace"
    loop = asyncio.get_event_loop()

    sync_count = 0
    original_sync = registry.sync_agent

    def counting_sync(agent):
        nonlocal sync_count
        sync_count += 1
        original_sync(agent)

    registry.sync_agent = counting_sync

    watcher = MemoryWatcher(registry, debounce_seconds=0.1)
    watcher.start(loop)
    try:
        # Only read the file — should not trigger sync
        (workspace / "MEMORY.md").read_text()
        await asyncio.sleep(0.5)
        assert sync_count == 0
    finally:
        watcher.stop()


@pytest.mark.asyncio
async def test_watcher_ignores_md_outside_memory_paths(agent_setup):
    """Changes to .md files outside MEMORY.md / memory/ should not trigger sync."""
    agents_dir, registry = agent_setup
    workspace = agents_dir / "main" / "workspace"
    loop = asyncio.get_event_loop()

    sync_count = 0
    original_sync = registry.sync_agent

    def counting_sync(agent):
        nonlocal sync_count
        sync_count += 1
        original_sync(agent)

    registry.sync_agent = counting_sync

    watcher = MemoryWatcher(registry, debounce_seconds=0.1)
    watcher.start(loop)
    try:
        (workspace / "README.md").write_text("# Readme\n")
        (workspace / "TOOLS.md").write_text("# Tools\n")
        await asyncio.sleep(0.5)
        assert sync_count == 0
    finally:
        watcher.stop()


@pytest.mark.asyncio
async def test_watcher_triggers_on_memory_subdir(agent_setup):
    """Changes to memory/**/*.md should trigger sync."""
    agents_dir, registry = agent_setup
    workspace = agents_dir / "main" / "workspace"
    (workspace / "memory").mkdir(exist_ok=True)
    loop = asyncio.get_event_loop()

    sync_count = 0
    original_sync = registry.sync_agent

    def counting_sync(agent):
        nonlocal sync_count
        sync_count += 1
        original_sync(agent)

    registry.sync_agent = counting_sync

    watcher = MemoryWatcher(registry, debounce_seconds=0.1)
    watcher.start(loop)
    try:
        (workspace / "memory" / "note.md").write_text("# Note\n\nContent.\n")
        await asyncio.sleep(0.5)
        assert sync_count >= 1
    finally:
        watcher.stop()


def test_watcher_stop_is_idempotent(agent_setup):
    _, registry = agent_setup
    watcher = MemoryWatcher(registry)
    watcher.stop()
