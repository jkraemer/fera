"""Knowledge ingest — copy staged chunks into agent memory files."""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


class KnowledgeIngest:
    """Reads the staging area and writes memory files."""

    def __init__(self, *, knowledge_dir: Path, memory_dir: Path) -> None:
        self.knowledge_dir = Path(knowledge_dir)
        self.memory_dir = Path(memory_dir)

    def _load_metadata(self) -> dict:
        meta_path = self.knowledge_dir / "metadata.json"
        if not meta_path.exists():
            return {"version": 1, "last_updated": None, "documents": []}
        try:
            return json.loads(meta_path.read_text())
        except (json.JSONDecodeError, OSError):
            log.warning("Corrupt metadata.json, treating as empty")
            return {"version": 1, "last_updated": None, "documents": []}

    def _read_memory_hash(self, doc_id: str) -> str | None:
        """Read the hash from an existing memory file's YAML front-matter."""
        doc_dir = self.memory_dir / doc_id
        if not doc_dir.is_dir():
            return None
        # Read hash from any chunk file (all share the same hash)
        for md_file in doc_dir.glob("chunk*.md"):
            for line in md_file.read_text().splitlines():
                if line.startswith("hash:"):
                    return line.split(":", 1)[1].strip()
            break
        return None

    def _save_metadata(self, metadata: dict) -> None:
        """Atomic write of metadata.json."""
        meta_path = self.knowledge_dir / "metadata.json"
        fd, tmp = tempfile.mkstemp(
            dir=self.knowledge_dir, suffix=".tmp", prefix="metadata-"
        )
        try:
            with open(fd, "w") as f:
                json.dump(metadata, f, indent=2)
            Path(tmp).replace(meta_path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def detect_unprocessed(self) -> list[dict]:
        """Return metadata entries for docs that need processing."""
        metadata = self._load_metadata()
        result = []
        for doc in metadata["documents"]:
            existing_hash = self._read_memory_hash(doc["id"])
            if existing_hash != doc["hash"]:
                result.append(doc)
        return result

    def ingest_document(self, doc: dict) -> None:
        """Write memory files for a single document, then clean up staging."""
        doc_id = doc["id"]
        doc_dir = self.memory_dir / doc_id
        now = datetime.now(timezone.utc).isoformat()

        # Clear old memory files if updating
        if doc_dir.exists():
            shutil.rmtree(doc_dir)
        doc_dir.mkdir(parents=True, exist_ok=True)

        total_chunks = doc["chunks"]

        for i, content_file in enumerate(doc["content_files"], 1):
            chunk_text = (self.knowledge_dir / content_file).read_text()
            front_matter = (
                f"---\n"
                f"source: {doc['source_path']}\n"
                f"type: {doc['type']}\n"
                f"hash: {doc['hash']}\n"
                f"chunk: {i}\n"
                f"total_chunks: {total_chunks}\n"
                f"processed: {now}\n"
                f"---\n\n"
            )
            (doc_dir / f"chunk{i}.md").write_text(front_matter + chunk_text)

        # Clean up staging chunk files
        for content_file in doc["content_files"]:
            (self.knowledge_dir / content_file).unlink(missing_ok=True)

        # Remove doc from metadata
        metadata = self._load_metadata()
        metadata["documents"] = [
            d for d in metadata["documents"] if d["id"] != doc_id
        ]
        self._save_metadata(metadata)

        log.info("Ingested %s (%d chunk(s))", doc_id, total_chunks)

    def process_deletions(self) -> int:
        """Process deletions.jsonl — remove memory dirs. Returns count deleted."""
        deletions_path = self.knowledge_dir / "deletions.jsonl"
        if not deletions_path.exists():
            return 0

        text = deletions_path.read_text().strip()
        if not text:
            return 0

        count = 0
        for line in text.splitlines():
            entry = json.loads(line)
            doc_dir = self.memory_dir / entry["id"]
            if doc_dir.is_dir():
                shutil.rmtree(doc_dir)
                log.info("Deleted memory for %s", entry["id"])
                count += 1

        deletions_path.write_text("")
        return count

    def run(self) -> dict:
        """Run the full ingest pipeline. Returns a report dict."""
        unprocessed = self.detect_unprocessed()
        ingested = 0
        for doc in unprocessed:
            try:
                self.ingest_document(doc)
                ingested += 1
            except Exception:
                log.error("Failed to ingest %s", doc["id"], exc_info=True)

        deleted = self.process_deletions()

        if ingested or deleted:
            log.info(
                "Ingest complete: %d ingested, %d deleted", ingested, deleted
            )
        else:
            log.info("Nothing to ingest")

        return {"ingested": ingested, "deleted": deleted}


def build_parser() -> "argparse.ArgumentParser":
    import argparse

    parser = argparse.ArgumentParser(
        description="Ingest knowledge staging area into agent memory files"
    )
    parser.add_argument("knowledge_dir", help="Path to staging area (knowledge/ dir)")
    parser.add_argument("memory_dir", help="Path to memory output (memory/knowledge/ dir)")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: INFO)",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    ingest = KnowledgeIngest(
        knowledge_dir=Path(args.knowledge_dir),
        memory_dir=Path(args.memory_dir),
    )
    ingest.run()


if __name__ == "__main__":
    main()
