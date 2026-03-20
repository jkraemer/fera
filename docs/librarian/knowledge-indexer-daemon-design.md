# Knowledge Indexer Daemon

A standalone daemon that watches a document repository, extracts text, and stages it for agent consumption.

---

## Architecture

```
~/knowledge/                              (Syncthing source)
    │
    │  watches (inotify via watchdog, 2s debounce)
    ▼
fera-knowledge-indexer (daemon)
    │  extracts text, chunks, writes metadata
    ▼
$FERA_HOME/agents/librarian/knowledge/    (staging area)
    ├── metadata.json       # Index of all processed documents
    ├── state.json          # Daemon internal state (indexed files, hashes)
    ├── deletions.jsonl     # Log of deleted source files
    └── content/            # Extracted text chunks
        ├── taxes_2025-tax-return_chunk1.txt
        ├── taxes_2025-tax-return_chunk2.txt
        └── notes_project-ideas.txt
```

---

## Components

### File Monitoring (`watcher.py`)

Uses `watchdog` (inotify on Linux) to detect file changes in real-time. Events are debounced (2-second window) to avoid redundant processing during rapid writes (e.g., Syncthing sync bursts).

### Text Extraction (`extractors.py`)

| Format | Method | Fallback Chain |
|---|---|---|
| `.md`, `.txt` | Direct read | — |
| `.pdf` | `pypdf` | → `pdftotext` → `pdftoppm` + `tesseract` |
| `.png`, `.jpg`, `.tiff` | `tesseract` OCR | — |

Syncthing conflict files (`.sync-conflict-*`) are handled gracefully.

### Chunking (`chunker.py`)

Documents exceeding ~32k characters (~8k tokens) are split at paragraph boundaries. Chunks overlap by ~800 characters (~200 tokens) for retrieval context continuity.

### Indexing (`indexer.py`)

`KnowledgeIndexer` manages the processing pipeline:

1. **Change detection:** Compares file mtime + SHA256 hash against `state.json`.
2. **Processing:** Extract text → chunk → write to `content/` directory.
3. **Metadata:** Updates `metadata.json` with document info (source path, type, size, modification time, chunk count, hash).
4. **Deletion tracking:** When source files are removed, records them in `deletions.jsonl` and cleans up content files.
5. **Atomic writes:** Uses temp files + rename for crash safety.

### Ingestion (`ingest.py`)

`fera-knowledge-ingest` is a separate CLI that bridges the staging area to the memory system:

1. Reads `metadata.json` from staging.
2. Compares document hashes to detect new/updated content.
3. Writes memory files with YAML front-matter to `workspace/memory/knowledge/{doc_id}/chunk{i}.md`.
4. Processes `deletions.jsonl` to remove memory files for deleted documents.

This separation means the daemon handles the "dirty work" (file I/O, PDF parsing, OCR) while the ingest script handles the "clean" memory integration.

---

## Deployment

### Systemd Service

`deploy/fera-knowledge-indexer.service`:

```ini
[Unit]
Description=Fera Knowledge Base Indexer
After=network.target

[Service]
Type=simple
User=fera
ExecStart=/opt/fera-venv/bin/fera-knowledge-indexer ...
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### Dependencies

System packages: `poppler-utils` (for `pdftotext`/`pdftoppm`), optionally `tesseract-ocr` (for scanned docs/images).

Python: `pypdf` and `watchdog` (included in Fera's dependencies).

### Entry Points

```toml
[project.scripts]
fera-knowledge-indexer = "fera.knowledge.daemon:main"
fera-knowledge-ingest = "fera.knowledge.ingest:main"
```

---

## Usage

```bash
# Start the daemon (watches ~/knowledge/, outputs to staging area)
fera-knowledge-indexer ~/knowledge/ ~/.fera/agents/librarian/knowledge/

# Run ingestion (staging → memory files)
fera-knowledge-ingest ~/.fera/agents/librarian/knowledge/ ~/.fera/agents/librarian/workspace/
```

See `INSTALL.md` for full setup instructions.
