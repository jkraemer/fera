import sqlite3

import pytest

from fera.memory.index import MemoryIndex


@pytest.fixture
def memory_dir(tmp_path):
    mem = tmp_path / "workspace"
    mem.mkdir()
    (mem / "memory").mkdir()
    return mem


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "memory.db"


@pytest.fixture
def index(memory_dir, db_path):
    return MemoryIndex(memory_dir=str(memory_dir), db_path=str(db_path))


def test_creates_schema(index, db_path):
    assert db_path.exists()
    conn = sqlite3.connect(str(db_path))
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    assert "files" in tables
    assert "chunks" in tables


def test_sync_indexes_new_file(index, memory_dir):
    (memory_dir / "memory" / "test.md").write_text("# Hello\n\nSome content here.\n")
    index.sync()
    chunks = index.all_chunks()
    assert len(chunks) > 0
    assert chunks[0]["path"] == "memory/test.md"


def test_sync_skips_unchanged_file(index, memory_dir):
    (memory_dir / "memory" / "test.md").write_text("# Hello\n\nContent.\n")
    index.sync()
    chunks_before = index.all_chunks()
    index.sync()
    chunks_after = index.all_chunks()
    assert len(chunks_before) == len(chunks_after)


def test_sync_updates_changed_file(index, memory_dir):
    f = memory_dir / "memory" / "test.md"
    f.write_text("# Version 1\n")
    index.sync()
    f.write_text("# Version 2\n\nNew content added.\n")
    index.sync()
    chunks = index.all_chunks()
    texts = " ".join(c["text"] for c in chunks)
    assert "Version 2" in texts
    assert "Version 1" not in texts


def test_sync_removes_deleted_file(index, memory_dir):
    f = memory_dir / "memory" / "test.md"
    f.write_text("# Temp\n")
    index.sync()
    assert len(index.all_chunks()) > 0
    f.unlink()
    index.sync()
    assert len(index.all_chunks()) == 0


def test_ignores_non_markdown_files(index, memory_dir):
    (memory_dir / "memory" / "notes.txt").write_text("not markdown")
    (memory_dir / "memory" / "real.md").write_text("# Real\n")
    index.sync()
    paths = {c["path"] for c in index.all_chunks()}
    assert "memory/real.md" in paths
    assert "memory/notes.txt" not in paths


def test_sync_indexes_memory_md_at_root(index, memory_dir):
    """MEMORY.md at workspace root should be indexed."""
    (memory_dir / "MEMORY.md").write_text("# Memory\n\nImportant facts.\n")
    index.sync()
    paths = {c["path"] for c in index.all_chunks()}
    assert "MEMORY.md" in paths


def test_sync_indexes_files_under_memory_subdir(index, memory_dir):
    """Files under memory/ subdirectory should be indexed."""
    (memory_dir / "memory" / "notes.md").write_text("# Notes\n\nSome notes.\n")
    index.sync()
    paths = {c["path"] for c in index.all_chunks()}
    assert "memory/notes.md" in paths


def test_sync_ignores_md_files_outside_memory_paths(index, memory_dir):
    """Markdown files not matching MEMORY.md or memory/**/*.md should be skipped."""
    (memory_dir / "MEMORY.md").write_text("# Memory\n")
    (memory_dir / "README.md").write_text("# Readme\n")
    (memory_dir / "TOOLS.md").write_text("# Tools\n")
    sub = memory_dir / "docs"
    sub.mkdir()
    (sub / "guide.md").write_text("# Guide\n")
    index.sync()
    paths = {c["path"] for c in index.all_chunks()}
    assert "MEMORY.md" in paths
    assert "README.md" not in paths
    assert "TOOLS.md" not in paths
    assert "docs/guide.md" not in paths
