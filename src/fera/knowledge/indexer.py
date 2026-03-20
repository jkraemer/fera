"""Knowledge base indexer -- processes documents into a staging area."""

from __future__ import annotations

import hashlib
import json
import logging
import tempfile
import time
from pathlib import Path

from fera.knowledge.chunker import chunk_text
from fera.knowledge.extractors import extract_text, supported_suffixes

log = logging.getLogger(__name__)

_SYNC_CONFLICT_MARKER = ".sync-conflict-"


class KnowledgeIndexer:
    """Watches a directory and indexes documents for agent consumption."""

    def __init__(self, *, watch_dir: Path, output_dir: Path) -> None:
        self.watch_dir = Path(watch_dir)
        self.output_dir = Path(output_dir)
        self.content_dir = self.output_dir / "content"
        self.metadata_path = self.output_dir / "metadata.json"
        self.state_path = self.output_dir / "state.json"
        self.deletions_path = self.output_dir / "deletions.jsonl"

        self.content_dir.mkdir(parents=True, exist_ok=True)
        self.state = self._load_state()

    # -- State persistence --

    def _load_state(self) -> dict:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text())
            except (json.JSONDecodeError, OSError):
                log.warning("Corrupt state file, starting fresh: %s", self.state_path)
        return {"last_scan": None, "indexed_files": {}, "errors": []}

    def _save_state(self) -> None:
        fd, tmp = tempfile.mkstemp(
            dir=self.output_dir, suffix=".tmp", prefix="state-"
        )
        try:
            with open(fd, "w") as f:
                json.dump(self.state, f, indent=2)
            Path(tmp).replace(self.state_path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    # -- Metadata (atomic writes) --

    def _load_metadata(self) -> dict:
        if self.metadata_path.exists():
            return json.loads(self.metadata_path.read_text())
        return {"version": 1, "last_updated": None, "documents": []}

    def _save_metadata(self, metadata: dict) -> None:
        metadata["last_updated"] = time.time()
        # Atomic write: temp file in same directory, then rename
        fd, tmp = tempfile.mkstemp(
            dir=self.output_dir, suffix=".tmp", prefix="metadata-"
        )
        try:
            with open(fd, "w") as f:
                json.dump(metadata, f, indent=2)
            Path(tmp).replace(self.metadata_path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    # -- Hashing --

    @staticmethod
    def _file_hash(path: Path) -> str:
        sha256 = hashlib.sha256()
        with path.open("rb") as f:
            for block in iter(lambda: f.read(8192), b""):
                sha256.update(block)
        return f"sha256:{sha256.hexdigest()}"

    # -- Change detection --

    def needs_processing(self, path: Path) -> bool:
        """Check whether a file needs (re-)processing."""
        if not path.is_file():
            return False
        suffix = path.suffix.lower()
        if suffix not in supported_suffixes():
            return False
        if _SYNC_CONFLICT_MARKER in path.name:
            return False

        key = str(path)
        indexed = self.state["indexed_files"].get(key)
        if indexed is None:
            return True  # never seen

        # Fast path: mtime unchanged means content unchanged
        current_mtime = path.stat().st_mtime
        if current_mtime == indexed.get("mtime"):
            return False

        # mtime changed -- verify with hash
        current_hash = self._file_hash(path)
        return current_hash != indexed.get("hash")

    # -- Document ID --

    def _generate_id(self, path: Path) -> str:
        rel = path.relative_to(self.watch_dir)
        # Preserve directory structure, only flatten dots in the filename
        parts = list(rel.parts)
        parts[-1] = parts[-1].replace(".", "_")
        return "/".join(parts)

    # -- Processing --

    def process_file(self, path: Path) -> None:
        """Extract text, chunk, and write to staging area."""
        try:
            log.info("Processing: %s", path)
            text = extract_text(path)
            if not text or not text.strip():
                log.warning("No text extracted from %s", path)
                return

            st = path.stat()
            chunks = chunk_text(text)
            doc_id = self._generate_id(path)
            file_hash = self._file_hash(path)

            # Remove old content files for this doc
            for old in self.content_dir.glob(f"{doc_id}_chunk*.txt"):
                old.unlink()

            # Write new chunks
            content_files = []
            for i, chunk in enumerate(chunks, 1):
                chunk_path = self.content_dir / f"{doc_id}_chunk{i}.txt"
                chunk_path.parent.mkdir(parents=True, exist_ok=True)
                chunk_path.write_text(chunk)
                rel = chunk_path.relative_to(self.output_dir)
                content_files.append(str(rel))

            # Update metadata
            metadata = self._load_metadata()
            metadata["documents"] = [
                d for d in metadata["documents"] if d["id"] != doc_id
            ]
            metadata["documents"].append({
                "id": doc_id,
                "source_path": str(path),
                "type": path.suffix.lstrip(".").lower(),
                "size_bytes": st.st_size,
                "modified": st.st_mtime,
                "processed": time.time(),
                "chunks": len(content_files),
                "content_files": content_files,
                "hash": file_hash,
            })
            self._save_metadata(metadata)

            # Update state
            self.state["indexed_files"][str(path)] = {
                "mtime": st.st_mtime,
                "hash": file_hash,
            }
            self._save_state()
            log.info("Indexed %s (%d chunk(s))", path.name, len(chunks))

        except Exception:
            log.error("Error processing %s", path, exc_info=True)
            self.state["errors"] = self.state["errors"][-99:] + [{
                "file": str(path),
                "time": time.time(),
            }]
            self._save_state()

    def scan(self) -> None:
        """Scan watch directory and process changed files."""
        log.info("Scanning %s", self.watch_dir)
        for path in sorted(self.watch_dir.rglob("*")):
            if self.needs_processing(path):
                self.process_file(path)
        self.state["last_scan"] = time.time()
        self._save_state()

    def cleanup_deleted(self) -> None:
        """Remove staging entries for files that no longer exist on disk.

        When the watch directory is completely empty but we have indexed
        documents, skip cleanup — the volume is likely not mounted.
        """
        if not any(self.watch_dir.rglob("*")):
            if self.state["indexed_files"]:
                log.warning(
                    "Watch directory %s is empty but index has %d file(s) — "
                    "skipping cleanup (volume not mounted?)",
                    self.watch_dir, len(self.state["indexed_files"]),
                )
                return

        metadata = self._load_metadata()
        kept = []
        deleted = []
        for doc in metadata["documents"]:
            if Path(doc["source_path"]).exists():
                kept.append(doc)
            else:
                deleted.append(doc)
                for cf in doc["content_files"]:
                    (self.output_dir / cf).unlink(missing_ok=True)
                log.info("Cleaned up deleted: %s", doc["source_path"])

        if deleted:
            metadata["documents"] = kept
            self._save_metadata(metadata)

            # Append to deletions log
            with self.deletions_path.open("a") as f:
                for doc in deleted:
                    entry = {
                        "id": doc["id"],
                        "source_path": doc["source_path"],
                        "deleted_at": time.time(),
                    }
                    f.write(json.dumps(entry) + "\n")

            # Clean up state entries
            for doc in deleted:
                self.state["indexed_files"].pop(doc["source_path"], None)
            self._save_state()
