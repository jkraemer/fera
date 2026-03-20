import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from fera.knowledge.ingest import KnowledgeIngest


def _make_staging(tmp_path, documents):
    """Helper: write metadata.json and content chunk files."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    content_dir = knowledge_dir / "content"
    content_dir.mkdir()
    for doc in documents:
        for cf in doc["content_files"]:
            p = knowledge_dir / cf
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"Chunk text for {cf}")
    meta = {"version": 1, "last_updated": None, "documents": documents}
    (knowledge_dir / "metadata.json").write_text(json.dumps(meta))
    return knowledge_dir


class TestDetectUnprocessed:
    def test_all_new_docs_detected(self, tmp_path):
        docs = [
            {
                "id": "note_txt",
                "source_path": "/src/note.txt",
                "type": "txt",
                "size_bytes": 100,
                "chunks": 1,
                "content_files": ["content/note_txt_chunk1.txt"],
                "hash": "sha256:aaa",
            },
        ]
        knowledge_dir = _make_staging(tmp_path, docs)
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        ingest = KnowledgeIngest(
            knowledge_dir=knowledge_dir, memory_dir=memory_dir
        )
        unprocessed = ingest.detect_unprocessed()
        assert len(unprocessed) == 1
        assert unprocessed[0]["id"] == "note_txt"

    def test_matching_hash_skipped(self, tmp_path):
        docs = [
            {
                "id": "note_txt",
                "source_path": "/src/note.txt",
                "type": "txt",
                "size_bytes": 100,
                "chunks": 1,
                "content_files": ["content/note_txt_chunk1.txt"],
                "hash": "sha256:aaa",
            },
        ]
        knowledge_dir = _make_staging(tmp_path, docs)
        memory_dir = tmp_path / "memory"
        # Pre-existing memory file with matching hash
        doc_dir = memory_dir / "note_txt"
        doc_dir.mkdir(parents=True)
        (doc_dir / "chunk1.md").write_text(
            "---\nhash: sha256:aaa\n---\nOld content"
        )

        ingest = KnowledgeIngest(
            knowledge_dir=knowledge_dir, memory_dir=memory_dir
        )
        assert ingest.detect_unprocessed() == []

    def test_changed_hash_detected(self, tmp_path):
        docs = [
            {
                "id": "note_txt",
                "source_path": "/src/note.txt",
                "type": "txt",
                "size_bytes": 100,
                "chunks": 1,
                "content_files": ["content/note_txt_chunk1.txt"],
                "hash": "sha256:bbb",
            },
        ]
        knowledge_dir = _make_staging(tmp_path, docs)
        memory_dir = tmp_path / "memory"
        doc_dir = memory_dir / "note_txt"
        doc_dir.mkdir(parents=True)
        (doc_dir / "chunk1.md").write_text(
            "---\nhash: sha256:aaa\n---\nOld content"
        )

        ingest = KnowledgeIngest(
            knowledge_dir=knowledge_dir, memory_dir=memory_dir
        )
        assert len(ingest.detect_unprocessed()) == 1

    def test_empty_metadata_returns_empty(self, tmp_path):
        knowledge_dir = tmp_path / "knowledge"
        knowledge_dir.mkdir()
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        # No metadata.json at all

        ingest = KnowledgeIngest(
            knowledge_dir=knowledge_dir, memory_dir=memory_dir
        )
        assert ingest.detect_unprocessed() == []

    def test_nested_doc_id(self, tmp_path):
        """Doc IDs with directory structure (e.g. finance/tax_pdf)."""
        docs = [
            {
                "id": "finance/tax_pdf",
                "source_path": "/src/finance/tax.pdf",
                "type": "pdf",
                "size_bytes": 1000,
                "chunks": 1,
                "content_files": ["content/finance/tax_pdf_chunk1.txt"],
                "hash": "sha256:ccc",
            },
        ]
        knowledge_dir = _make_staging(tmp_path, docs)
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        ingest = KnowledgeIngest(
            knowledge_dir=knowledge_dir, memory_dir=memory_dir
        )
        assert len(ingest.detect_unprocessed()) == 1


class TestIngestDocument:
    def test_writes_chunk_as_memory_file(self, tmp_path):
        docs = [
            {
                "id": "note_txt",
                "source_path": "/src/note.txt",
                "type": "txt",
                "size_bytes": 100,
                "chunks": 1,
                "content_files": ["content/note_txt_chunk1.txt"],
                "hash": "sha256:aaa",
            },
        ]
        knowledge_dir = _make_staging(tmp_path, docs)
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        ingest = KnowledgeIngest(
            knowledge_dir=knowledge_dir, memory_dir=memory_dir
        )
        ingest.ingest_document(docs[0])

        chunk_path = memory_dir / "note_txt" / "chunk1.md"
        assert chunk_path.exists()
        text = chunk_path.read_text()
        assert "hash: sha256:aaa" in text
        assert "source: /src/note.txt" in text
        assert "type: txt" in text
        assert "chunk: 1" in text
        assert "total_chunks: 1" in text
        assert "Chunk text for" in text

    def test_writes_multiple_chunks(self, tmp_path):
        docs = [
            {
                "id": "big_pdf",
                "source_path": "/src/big.pdf",
                "type": "pdf",
                "size_bytes": 50000,
                "chunks": 3,
                "content_files": [
                    "content/big_pdf_chunk1.txt",
                    "content/big_pdf_chunk2.txt",
                    "content/big_pdf_chunk3.txt",
                ],
                "hash": "sha256:bbb",
            },
        ]
        knowledge_dir = _make_staging(tmp_path, docs)
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        ingest = KnowledgeIngest(
            knowledge_dir=knowledge_dir, memory_dir=memory_dir
        )
        ingest.ingest_document(docs[0])

        doc_dir = memory_dir / "big_pdf"
        assert (doc_dir / "chunk1.md").exists()
        assert (doc_dir / "chunk2.md").exists()
        assert (doc_dir / "chunk3.md").exists()
        # Check total_chunks in each
        for md in doc_dir.glob("chunk*.md"):
            assert "total_chunks: 3" in md.read_text()

    def test_nested_doc_id_creates_subdirs(self, tmp_path):
        docs = [
            {
                "id": "finance/tax_pdf",
                "source_path": "/src/finance/tax.pdf",
                "type": "pdf",
                "size_bytes": 1000,
                "chunks": 1,
                "content_files": ["content/finance/tax_pdf_chunk1.txt"],
                "hash": "sha256:ccc",
            },
        ]
        knowledge_dir = _make_staging(tmp_path, docs)
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        ingest = KnowledgeIngest(
            knowledge_dir=knowledge_dir, memory_dir=memory_dir
        )
        ingest.ingest_document(docs[0])

        assert (memory_dir / "finance" / "tax_pdf" / "chunk1.md").exists()

    def test_deletes_staging_chunks_after_ingest(self, tmp_path):
        docs = [
            {
                "id": "note_txt",
                "source_path": "/src/note.txt",
                "type": "txt",
                "size_bytes": 100,
                "chunks": 1,
                "content_files": ["content/note_txt_chunk1.txt"],
                "hash": "sha256:aaa",
            },
        ]
        knowledge_dir = _make_staging(tmp_path, docs)
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        ingest = KnowledgeIngest(
            knowledge_dir=knowledge_dir, memory_dir=memory_dir
        )
        ingest.ingest_document(docs[0])

        assert not (knowledge_dir / "content" / "note_txt_chunk1.txt").exists()

    def test_removes_doc_from_metadata(self, tmp_path):
        docs = [
            {
                "id": "note_txt",
                "source_path": "/src/note.txt",
                "type": "txt",
                "size_bytes": 100,
                "chunks": 1,
                "content_files": ["content/note_txt_chunk1.txt"],
                "hash": "sha256:aaa",
            },
            {
                "id": "other_txt",
                "source_path": "/src/other.txt",
                "type": "txt",
                "size_bytes": 200,
                "chunks": 1,
                "content_files": ["content/other_txt_chunk1.txt"],
                "hash": "sha256:ddd",
            },
        ]
        knowledge_dir = _make_staging(tmp_path, docs)
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        ingest = KnowledgeIngest(
            knowledge_dir=knowledge_dir, memory_dir=memory_dir
        )
        ingest.ingest_document(docs[0])

        meta = json.loads((knowledge_dir / "metadata.json").read_text())
        ids = [d["id"] for d in meta["documents"]]
        assert "note_txt" not in ids
        assert "other_txt" in ids

    def test_update_replaces_old_memory_files(self, tmp_path):
        docs = [
            {
                "id": "note_txt",
                "source_path": "/src/note.txt",
                "type": "txt",
                "size_bytes": 100,
                "chunks": 1,
                "content_files": ["content/note_txt_chunk1.txt"],
                "hash": "sha256:bbb",
            },
        ]
        knowledge_dir = _make_staging(tmp_path, docs)
        memory_dir = tmp_path / "memory"
        # Pre-existing memory files with old hash and extra chunk
        doc_dir = memory_dir / "note_txt"
        doc_dir.mkdir(parents=True)
        (doc_dir / "chunk1.md").write_text("---\nhash: sha256:aaa\n---\nOld")
        (doc_dir / "chunk2.md").write_text("---\nhash: sha256:aaa\n---\nOld2")

        ingest = KnowledgeIngest(
            knowledge_dir=knowledge_dir, memory_dir=memory_dir
        )
        ingest.ingest_document(docs[0])

        # Old chunk2 should be gone (new doc only has 1 chunk)
        assert not (doc_dir / "chunk2.md").exists()
        assert "sha256:bbb" in (doc_dir / "chunk1.md").read_text()


class TestProcessDeletions:
    def test_deletes_memory_directory(self, tmp_path):
        knowledge_dir = tmp_path / "knowledge"
        knowledge_dir.mkdir()
        memory_dir = tmp_path / "memory"
        doc_dir = memory_dir / "note_txt"
        doc_dir.mkdir(parents=True)
        (doc_dir / "chunk1.md").write_text("---\nhash: sha256:aaa\n---\nText")

        deletions = [
            {"id": "note_txt", "source_path": "/src/note.txt", "deleted_at": 1.0}
        ]
        (knowledge_dir / "deletions.jsonl").write_text(
            json.dumps(deletions[0]) + "\n"
        )

        ingest = KnowledgeIngest(
            knowledge_dir=knowledge_dir, memory_dir=memory_dir
        )
        count = ingest.process_deletions()

        assert count == 1
        assert not doc_dir.exists()

    def test_truncates_deletions_file(self, tmp_path):
        knowledge_dir = tmp_path / "knowledge"
        knowledge_dir.mkdir()
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        (knowledge_dir / "deletions.jsonl").write_text(
            json.dumps({"id": "gone_txt", "source_path": "/x", "deleted_at": 1.0}) + "\n"
        )

        ingest = KnowledgeIngest(
            knowledge_dir=knowledge_dir, memory_dir=memory_dir
        )
        ingest.process_deletions()

        assert (knowledge_dir / "deletions.jsonl").read_text() == ""

    def test_skips_missing_memory_dir(self, tmp_path):
        """Deletion for a doc that was never ingested — no crash."""
        knowledge_dir = tmp_path / "knowledge"
        knowledge_dir.mkdir()
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        (knowledge_dir / "deletions.jsonl").write_text(
            json.dumps({"id": "never_existed", "source_path": "/x", "deleted_at": 1.0}) + "\n"
        )

        ingest = KnowledgeIngest(
            knowledge_dir=knowledge_dir, memory_dir=memory_dir
        )
        count = ingest.process_deletions()
        assert count == 0

    def test_no_deletions_file(self, tmp_path):
        knowledge_dir = tmp_path / "knowledge"
        knowledge_dir.mkdir()
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        ingest = KnowledgeIngest(
            knowledge_dir=knowledge_dir, memory_dir=memory_dir
        )
        count = ingest.process_deletions()
        assert count == 0

    def test_nested_doc_id_deletion(self, tmp_path):
        knowledge_dir = tmp_path / "knowledge"
        knowledge_dir.mkdir()
        memory_dir = tmp_path / "memory"
        doc_dir = memory_dir / "finance" / "tax_pdf"
        doc_dir.mkdir(parents=True)
        (doc_dir / "chunk1.md").write_text("---\nhash: sha256:x\n---\nText")

        (knowledge_dir / "deletions.jsonl").write_text(
            json.dumps({"id": "finance/tax_pdf", "source_path": "/x", "deleted_at": 1.0}) + "\n"
        )

        ingest = KnowledgeIngest(
            knowledge_dir=knowledge_dir, memory_dir=memory_dir
        )
        count = ingest.process_deletions()

        assert count == 1
        assert not doc_dir.exists()
        # Parent dir (finance/) should remain if empty — we only delete the doc dir


class TestRun:
    def test_ingests_new_and_processes_deletions(self, tmp_path):
        docs = [
            {
                "id": "note_txt",
                "source_path": "/src/note.txt",
                "type": "txt",
                "size_bytes": 100,
                "chunks": 1,
                "content_files": ["content/note_txt_chunk1.txt"],
                "hash": "sha256:aaa",
            },
        ]
        knowledge_dir = _make_staging(tmp_path, docs)
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        # Also add a deletion
        old_dir = memory_dir / "old_txt"
        old_dir.mkdir()
        (old_dir / "chunk1.md").write_text("---\nhash: sha256:x\n---\nOld")
        (knowledge_dir / "deletions.jsonl").write_text(
            json.dumps({"id": "old_txt", "source_path": "/x", "deleted_at": 1.0}) + "\n"
        )

        ingest = KnowledgeIngest(
            knowledge_dir=knowledge_dir, memory_dir=memory_dir
        )
        report = ingest.run()

        assert (memory_dir / "note_txt" / "chunk1.md").exists()
        assert not old_dir.exists()
        assert report["ingested"] == 1
        assert report["deleted"] == 1

    def test_nothing_to_do(self, tmp_path):
        knowledge_dir = tmp_path / "knowledge"
        knowledge_dir.mkdir()
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        ingest = KnowledgeIngest(
            knowledge_dir=knowledge_dir, memory_dir=memory_dir
        )
        report = ingest.run()

        assert report["ingested"] == 0
        assert report["deleted"] == 0

    def test_skips_already_ingested(self, tmp_path):
        docs = [
            {
                "id": "note_txt",
                "source_path": "/src/note.txt",
                "type": "txt",
                "size_bytes": 100,
                "chunks": 1,
                "content_files": ["content/note_txt_chunk1.txt"],
                "hash": "sha256:aaa",
            },
        ]
        knowledge_dir = _make_staging(tmp_path, docs)
        memory_dir = tmp_path / "memory"
        doc_dir = memory_dir / "note_txt"
        doc_dir.mkdir(parents=True)
        (doc_dir / "chunk1.md").write_text("---\nhash: sha256:aaa\n---\nText")

        ingest = KnowledgeIngest(
            knowledge_dir=knowledge_dir, memory_dir=memory_dir
        )
        report = ingest.run()

        assert report["ingested"] == 0
        # Staging should NOT be cleaned up for skipped docs
        assert (knowledge_dir / "content" / "note_txt_chunk1.txt").exists()


class TestCli:
    def test_cli_runs_ingest(self, tmp_path):
        docs = [
            {
                "id": "note_txt",
                "source_path": "/src/note.txt",
                "type": "txt",
                "size_bytes": 100,
                "chunks": 1,
                "content_files": ["content/note_txt_chunk1.txt"],
                "hash": "sha256:aaa",
            },
        ]
        knowledge_dir = _make_staging(tmp_path, docs)
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        result = subprocess.run(
            [sys.executable, "-m", "fera.knowledge.ingest",
             str(knowledge_dir), str(memory_dir)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert (memory_dir / "note_txt" / "chunk1.md").exists()

    def test_cli_no_args_exits_with_error(self):
        result = subprocess.run(
            [sys.executable, "-m", "fera.knowledge.ingest"],
            capture_output=True, text=True,
        )
        assert result.returncode != 0

    def test_cli_nothing_to_do(self, tmp_path):
        knowledge_dir = tmp_path / "knowledge"
        knowledge_dir.mkdir()
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        result = subprocess.run(
            [sys.executable, "-m", "fera.knowledge.ingest",
             str(knowledge_dir), str(memory_dir)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "Nothing to ingest" in result.stderr
