import json
from pathlib import Path

from fera.knowledge.indexer import KnowledgeIndexer


def test_process_creates_content_and_metadata(tmp_path):
    watch_dir = tmp_path / "source"
    watch_dir.mkdir()
    output_dir = tmp_path / "staging"
    output_dir.mkdir()

    (watch_dir / "note.txt").write_text("Hello world, this is a test document.")

    indexer = KnowledgeIndexer(watch_dir=watch_dir, output_dir=output_dir)
    indexer.process_file(watch_dir / "note.txt")

    # Content file should exist
    content_files = list((output_dir / "content").iterdir())
    assert len(content_files) == 1
    assert "Hello world" in content_files[0].read_text()

    # Metadata should exist
    meta = json.loads((output_dir / "metadata.json").read_text())
    assert len(meta["documents"]) == 1
    doc = meta["documents"][0]
    assert doc["source_path"] == str(watch_dir / "note.txt")
    assert doc["type"] == "txt"
    assert doc["chunks"] == 1
    assert doc["hash"].startswith("sha256:")


def test_process_updates_state(tmp_path):
    watch_dir = tmp_path / "source"
    watch_dir.mkdir()
    output_dir = tmp_path / "staging"
    output_dir.mkdir()

    src = watch_dir / "note.md"
    src.write_text("# Test\n\nContent here.")

    indexer = KnowledgeIndexer(watch_dir=watch_dir, output_dir=output_dir)
    indexer.process_file(src)

    state = json.loads((output_dir / "state.json").read_text())
    assert str(src) in state["indexed_files"]
    entry = state["indexed_files"][str(src)]
    assert entry["hash"].startswith("sha256:")
    assert "mtime" in entry


def test_unchanged_file_skipped(tmp_path):
    watch_dir = tmp_path / "source"
    watch_dir.mkdir()
    output_dir = tmp_path / "staging"
    output_dir.mkdir()

    src = watch_dir / "doc.txt"
    src.write_text("Content")

    indexer = KnowledgeIndexer(watch_dir=watch_dir, output_dir=output_dir)
    indexer.process_file(src)
    assert not indexer.needs_processing(src)  # already indexed, same hash


def test_modified_file_reprocessed(tmp_path):
    watch_dir = tmp_path / "source"
    watch_dir.mkdir()
    output_dir = tmp_path / "staging"
    output_dir.mkdir()

    src = watch_dir / "doc.txt"
    src.write_text("Version 1")

    indexer = KnowledgeIndexer(watch_dir=watch_dir, output_dir=output_dir)
    indexer.process_file(src)

    src.write_text("Version 2")
    assert indexer.needs_processing(src)  # mtime changed


def test_scan_processes_all_supported_files(tmp_path):
    watch_dir = tmp_path / "source"
    watch_dir.mkdir()
    output_dir = tmp_path / "staging"
    output_dir.mkdir()

    (watch_dir / "a.txt").write_text("File A")
    (watch_dir / "b.md").write_text("# File B")
    (watch_dir / "c.csv").write_text("not,supported")  # should be skipped
    sub = watch_dir / "sub"
    sub.mkdir()
    (sub / "d.txt").write_text("Nested file D")

    indexer = KnowledgeIndexer(watch_dir=watch_dir, output_dir=output_dir)
    indexer.scan()

    meta = json.loads((output_dir / "metadata.json").read_text())
    ids = {d["id"] for d in meta["documents"]}
    assert len(ids) == 3  # a.txt, b.md, sub/d.txt -- not c.csv


def test_deleted_file_cleanup(tmp_path):
    watch_dir = tmp_path / "source"
    watch_dir.mkdir()
    output_dir = tmp_path / "staging"
    output_dir.mkdir()

    src = watch_dir / "ephemeral.txt"
    src.write_text("Temporary content")
    # Keep another file so watch_dir isn't empty (empty = volume-not-mounted guard)
    (watch_dir / "keeper.txt").write_text("I stay")

    indexer = KnowledgeIndexer(watch_dir=watch_dir, output_dir=output_dir)
    indexer.process_file(src)
    indexer.process_file(watch_dir / "keeper.txt")

    # Now delete only the ephemeral file
    src.unlink()
    indexer.cleanup_deleted()

    # Only ephemeral's content file gone; keeper's remains
    remaining = {p.name for p in (output_dir / "content").iterdir()}
    assert "ephemeral_txt_chunk1.txt" not in remaining
    assert "keeper_txt_chunk1.txt" in remaining

    # Metadata has only keeper
    meta = json.loads((output_dir / "metadata.json").read_text())
    assert len(meta["documents"]) == 1
    assert meta["documents"][0]["id"] == "keeper_txt"

    # Deletion logged
    deletions_path = output_dir / "deletions.jsonl"
    assert deletions_path.exists()
    lines = deletions_path.read_text().strip().split("\n")
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["id"] == "ephemeral_txt"
    assert "deleted_at" in entry


def test_metadata_written_atomically(tmp_path):
    """Verify metadata.json is replaced atomically (no partial writes)."""
    watch_dir = tmp_path / "source"
    watch_dir.mkdir()
    output_dir = tmp_path / "staging"
    output_dir.mkdir()

    (watch_dir / "a.txt").write_text("A")

    indexer = KnowledgeIndexer(watch_dir=watch_dir, output_dir=output_dir)
    indexer.process_file(watch_dir / "a.txt")

    # Metadata should be valid JSON at all times
    meta = json.loads((output_dir / "metadata.json").read_text())
    assert meta["version"] == 1


def test_sync_conflict_files_skipped(tmp_path):
    watch_dir = tmp_path / "source"
    watch_dir.mkdir()
    output_dir = tmp_path / "staging"
    output_dir.mkdir()

    (watch_dir / "doc.txt").write_text("Original")
    (watch_dir / "doc.sync-conflict-20260223-123456-ABCDEF.txt").write_text("Conflict")

    indexer = KnowledgeIndexer(watch_dir=watch_dir, output_dir=output_dir)
    indexer.scan()

    meta = json.loads((output_dir / "metadata.json").read_text())
    assert len(meta["documents"]) == 1  # only original, not conflict


def test_cleanup_skipped_when_watch_dir_empty_but_has_indexed_docs(tmp_path):
    """When watch_dir is empty (e.g. encrypted volume not mounted), cleanup must not
    delete all indexed documents."""
    watch_dir = tmp_path / "source"
    watch_dir.mkdir()
    output_dir = tmp_path / "staging"
    output_dir.mkdir()

    # Index a document, then "unmount" by removing the file
    src = watch_dir / "secret.txt"
    src.write_text("Classified information")

    indexer = KnowledgeIndexer(watch_dir=watch_dir, output_dir=output_dir)
    indexer.process_file(src)

    # Simulate volume unmount: directory exists but is empty
    src.unlink()

    # cleanup_deleted should detect empty watch_dir and skip
    indexer.cleanup_deleted()

    # Documents should be preserved
    meta = json.loads((output_dir / "metadata.json").read_text())
    assert len(meta["documents"]) == 1
    assert meta["documents"][0]["id"] == "secret_txt"

    # Content files should still exist
    content_files = list((output_dir / "content").iterdir())
    assert len(content_files) == 1

    # No deletions logged
    assert not (output_dir / "deletions.jsonl").exists()


def test_cleanup_proceeds_when_watch_dir_has_some_files(tmp_path):
    """When watch_dir still has some files, deletions of individual files proceed."""
    watch_dir = tmp_path / "source"
    watch_dir.mkdir()
    output_dir = tmp_path / "staging"
    output_dir.mkdir()

    a = watch_dir / "keep.txt"
    b = watch_dir / "remove.txt"
    a.write_text("Staying")
    b.write_text("Going away")

    indexer = KnowledgeIndexer(watch_dir=watch_dir, output_dir=output_dir)
    indexer.process_file(a)
    indexer.process_file(b)

    # Delete one file — watch_dir is not empty
    b.unlink()
    indexer.cleanup_deleted()

    meta = json.loads((output_dir / "metadata.json").read_text())
    assert len(meta["documents"]) == 1
    assert meta["documents"][0]["id"] == "keep_txt"

    # Deletion logged for remove.txt only
    deletions = (output_dir / "deletions.jsonl").read_text().strip().split("\n")
    assert len(deletions) == 1
    assert "remove_txt" in deletions[0]


def test_generate_id_preserves_directory_structure(tmp_path):
    """Doc IDs preserve subdirectory structure from watch_dir."""
    watch_dir = tmp_path / "source"
    watch_dir.mkdir()
    output_dir = tmp_path / "staging"
    output_dir.mkdir()

    indexer = KnowledgeIndexer(watch_dir=watch_dir, output_dir=output_dir)

    # Top-level file: dots replaced with underscores
    assert indexer._generate_id(watch_dir / "note.txt") == "note_txt"

    # Nested file: directory separators preserved
    assert indexer._generate_id(watch_dir / "finance" / "tax.pdf") == "finance/tax_pdf"

    # Deeply nested
    assert indexer._generate_id(watch_dir / "a" / "b" / "c.md") == "a/b/c_md"


def test_nested_file_creates_content_subdirectory(tmp_path):
    """Processing a file in a subdirectory creates matching content subdirectory."""
    watch_dir = tmp_path / "source"
    sub = watch_dir / "finance"
    sub.mkdir(parents=True)
    output_dir = tmp_path / "staging"
    output_dir.mkdir()

    (sub / "report.txt").write_text("Quarterly earnings report")

    indexer = KnowledgeIndexer(watch_dir=watch_dir, output_dir=output_dir)
    indexer.process_file(sub / "report.txt")

    # Content chunk should be in a subdirectory
    chunk = output_dir / "content" / "finance" / "report_txt_chunk1.txt"
    assert chunk.exists()
    assert "Quarterly earnings" in chunk.read_text()

    # Metadata should reference the subdirectory path
    meta = json.loads((output_dir / "metadata.json").read_text())
    doc = meta["documents"][0]
    assert doc["id"] == "finance/report_txt"
    assert doc["content_files"] == ["content/finance/report_txt_chunk1.txt"]


def test_large_document_chunked(tmp_path):
    watch_dir = tmp_path / "source"
    watch_dir.mkdir()
    output_dir = tmp_path / "staging"
    output_dir.mkdir()

    # Create a document larger than default chunk size
    paras = [f"Paragraph {i}. " + "word " * 500 for i in range(20)]
    (watch_dir / "big.txt").write_text("\n\n".join(paras))

    indexer = KnowledgeIndexer(watch_dir=watch_dir, output_dir=output_dir)
    indexer.process_file(watch_dir / "big.txt")

    meta = json.loads((output_dir / "metadata.json").read_text())
    doc = meta["documents"][0]
    assert doc["chunks"] > 1
    assert len(doc["content_files"]) == doc["chunks"]
