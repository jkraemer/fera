from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

import sqlite_vec

from fera.memory.chunker import chunk_markdown
from fera.memory.embeddings import Embedder


class MemoryIndex:
    """SQLite-backed index of markdown memory files with FTS and vector search."""

    def __init__(
        self,
        memory_dir: str,
        db_path: str,
        embedder: Embedder | None = None,
    ):
        self._memory_dir = Path(memory_dir)
        self._db_path = Path(db_path)
        self._embedder = embedder if embedder is not None else Embedder()

        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)

        self._ensure_schema()

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    @property
    def embedder(self) -> Embedder:
        return self._embedder

    def _ensure_schema(self) -> None:
        self._conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                hash TEXT NOT NULL,
                mtime INTEGER NOT NULL,
                size INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                hash TEXT NOT NULL,
                text TEXT NOT NULL,
                embedding BLOB,
                updated_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);

            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                text, id UNINDEXED, path UNINDEXED,
                start_line UNINDEXED, end_line UNINDEXED
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
                id TEXT PRIMARY KEY,
                embedding float[{self._embedder.dimension}]
            );
            """
        )

    def sync(self) -> None:
        """Synchronize the index with markdown files on disk."""
        # Discover memory files: MEMORY.md at root + memory/**/*.md
        disk_files: dict[str, Path] = {}
        root_memory = self._memory_dir / "MEMORY.md"
        if root_memory.is_file():
            disk_files["MEMORY.md"] = root_memory
        memory_subdir = self._memory_dir / "memory"
        if memory_subdir.is_dir():
            for md_file in memory_subdir.rglob("*.md"):
                rel = str(md_file.relative_to(self._memory_dir))
                disk_files[rel] = md_file

        # Get indexed files
        indexed = {
            row[0]: row[1]
            for row in self._conn.execute(
                "SELECT path, hash FROM files"
            ).fetchall()
        }

        # Process new and changed files
        for rel_path, abs_path in disk_files.items():
            content = abs_path.read_text(encoding="utf-8")
            file_hash = hashlib.sha256(content.encode()).hexdigest()

            if rel_path in indexed and indexed[rel_path] == file_hash:
                continue  # Unchanged

            stat = abs_path.stat()
            self._index_file(rel_path, content, file_hash, stat)

        # Remove deleted files
        for rel_path in indexed:
            if rel_path not in disk_files:
                self._remove_file(rel_path)

    def _index_file(
        self, path: str, content: str, file_hash: str, stat: object
    ) -> None:
        """Index or re-index a single file."""
        # Remove old data first
        self._remove_file(path)

        # Chunk the content
        chunks = chunk_markdown(content)
        if not chunks:
            # Still record the file even if no chunks produced
            self._conn.execute(
                "INSERT INTO files (path, hash, mtime, size) VALUES (?, ?, ?, ?)",
                (path, file_hash, int(stat.st_mtime), stat.st_size),
            )
            self._conn.commit()
            return

        # Embed all chunk texts
        texts = [c["text"] for c in chunks]
        embeddings = self._embedder.embed_batch(texts)

        now = int(time.time())

        for i, chunk in enumerate(chunks):
            chunk_id = f"{path}:{chunk['start_line']}-{chunk['end_line']}"
            chunk_hash = hashlib.sha256(chunk["text"].encode()).hexdigest()
            embedding = embeddings[i]
            embedding_blob = embedding.tobytes()

            self._conn.execute(
                """INSERT INTO chunks
                   (id, path, start_line, end_line, hash, text, embedding, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    chunk_id,
                    path,
                    chunk["start_line"],
                    chunk["end_line"],
                    chunk_hash,
                    chunk["text"],
                    embedding_blob,
                    now,
                ),
            )

            self._conn.execute(
                """INSERT INTO chunks_fts (text, id, path, start_line, end_line)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    chunk["text"],
                    chunk_id,
                    path,
                    chunk["start_line"],
                    chunk["end_line"],
                ),
            )

            self._conn.execute(
                "INSERT INTO chunks_vec (id, embedding) VALUES (?, ?)",
                (chunk_id, embedding_blob),
            )

        # Record the file
        self._conn.execute(
            "INSERT INTO files (path, hash, mtime, size) VALUES (?, ?, ?, ?)",
            (path, file_hash, int(stat.st_mtime), stat.st_size),
        )

        self._conn.commit()

    def _remove_file(self, path: str) -> None:
        """Remove all data for a file from the index."""
        # Get chunk IDs for FTS and vec cleanup
        chunk_ids = [
            row[0]
            for row in self._conn.execute(
                "SELECT id FROM chunks WHERE path = ?", (path,)
            ).fetchall()
        ]

        for chunk_id in chunk_ids:
            self._conn.execute(
                "DELETE FROM chunks_fts WHERE id = ?", (chunk_id,)
            )
            self._conn.execute(
                "DELETE FROM chunks_vec WHERE id = ?", (chunk_id,)
            )

        self._conn.execute("DELETE FROM chunks WHERE path = ?", (path,))
        self._conn.execute("DELETE FROM files WHERE path = ?", (path,))
        self._conn.commit()

    def all_chunks(self) -> list[dict]:
        """Return all chunks as a list of dicts (primarily for testing)."""
        rows = self._conn.execute(
            "SELECT id, path, start_line, end_line, text FROM chunks"
        ).fetchall()
        return [
            {
                "id": row[0],
                "path": row[1],
                "start_line": row[2],
                "end_line": row[3],
                "text": row[4],
            }
            for row in rows
        ]
