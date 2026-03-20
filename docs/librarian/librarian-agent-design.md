# Librarian Agent Design

## Overview

A dedicated agent ("Lore") responsible for indexing and retrieving information from a personal document repository (synced via Syncthing). Operates as a separate agent from the main agent (Fera), with its own workspace, memory collection, and sessions.

The full pipeline is **implemented and running in production**: the knowledge indexer daemon watches for new documents, the ingest script feeds them into the librarian's memory, and the main agent queries Lore via a skill that communicates over the gateway WebSocket.

---

## Architecture

```
~/knowledge/                              (Syncthing source)
    │
    │  watches (inotify via watchdog)
    ▼
fera-knowledge-indexer (daemon)
    │  extracts text, chunks, writes metadata
    ▼
$FERA_HOME/agents/librarian/knowledge/    (staging area)
    ├── metadata.json
    ├── state.json
    ├── deletions.jsonl
    └── content/*.txt
    │
    │  periodic ingest
    ▼
$FERA_HOME/agents/librarian/workspace/memory/knowledge/
    └── {doc_id}/chunk{i}.md              (YAML front-matter)
    │
    │  memory_search MCP tool
    ▼
┌─────────────────┐   ask-lore skill   ┌─────────────────┐
│  Main Agent     │ ────────────────►   │  Librarian Agent│
│  (Fera)         │   via gateway WS    │  (Lore, Haiku)  │
└─────────────────┘ ◄──────────────────  └─────────────────┘
```

---

## Agent Setup

The librarian runs as a standard Fera agent, configured alongside the main agent:

- **Agent name:** `librarian`
- **Model:** Haiku (cheaper and faster for search/retrieval tasks)
- **Workspace:** `$FERA_HOME/agents/librarian/workspace/`
- **Knowledge staging:** `$FERA_HOME/agents/librarian/knowledge/`
- **Session:** `librarian/dm-fera` (persistent — remembers conversation context)

Created like any other agent via `fera-create-agent librarian`, then customized with its own SOUL.md, TOOLS.md, etc.

---

## Agent-to-Agent Communication

The main agent communicates with Lore via the **ask-lore skill** — a Python script that opens a WebSocket connection to the gateway, sends a `chat.send` request targeting the librarian's session, and collects the streamed response.

This approach reuses the existing gateway protocol with zero new infrastructure. The skill was implemented by the main agent itself when asked to find a way to talk to the librarian.

### Skill Definition (`ask-lore/SKILL.md`)

```markdown
---
name: ask-lore
description: Query Lore, the librarian agent, to search the owner's personal
  knowledge base. Use when looking up stored documents, personal records,
  insurance, manuals, notes, vehicles, or any "do I have a document about X?"
  type question.
---
```

### Query Script (`ask-lore/query_lore.py`)

```python
#!/usr/bin/env python3
"""Query the Lore (librarian) agent via the Fera gateway websocket."""

import asyncio
import json
import sys
import uuid

sys.path.insert(0, '/opt/fera/src')


async def query_lore(question: str, session: str = "librarian/dm-fera", timeout: float = 120.0) -> str:
    import websockets
    from fera.config import ensure_auth_token, load_config

    config = load_config()
    gw = config["gateway"]
    token = ensure_auth_token(config, save=False)
    url = f"ws://127.0.0.1:{gw['port']}"

    async with websockets.connect(url) as ws:
        # Authenticate
        req = {"type": "req", "id": str(uuid.uuid4()), "method": "connect", "params": {"token": token}}
        await ws.send(json.dumps(req))
        resp = json.loads(await ws.recv())
        if not resp.get("ok"):
            return f"Auth failed: {resp.get('error')}"

        # Send message
        msg = {
            "type": "req",
            "id": str(uuid.uuid4()),
            "method": "chat.send",
            "params": {"text": question, "session": session},
        }
        await ws.send(json.dumps(msg))

        # Collect streamed response
        parts = []
        try:
            async with asyncio.timeout(timeout):
                async for raw in ws:
                    frame = json.loads(raw)
                    if frame.get("type") == "event":
                        evt = frame.get("event", "")
                        if evt == "agent.text":
                            parts.append(frame.get("data", {}).get("text", ""))
                        elif evt == "agent.done":
                            break
                        elif evt == "agent.error":
                            return f"Agent error: {frame.get('data', {}).get('error')}"
        except asyncio.TimeoutError:
            if parts:
                parts.append("\n[response timed out]")
            else:
                return "Timed out waiting for Lore's response."

    return "".join(parts)
```

### Usage

From the main agent's bash tool:

```bash
python3 /home/fera/agents/main/workspace/.claude/skills/ask-lore/query_lore.py "What insurance documents do we have?"
```

Or inline from Python:

```python
from query_lore import query_lore
result = asyncio.run(query_lore("find tax documents from 2025"))
```

---

## Knowledge Indexing Pipeline

See `docs/librarian/knowledge-indexer-daemon-design.md` for full details on the indexer daemon and ingest script.

**Summary:**

1. **fera-knowledge-indexer** (daemon) watches `~/knowledge/` via inotify, extracts text from documents (markdown, PDF, images via OCR), chunks large files, and writes to a staging area.
2. **fera-knowledge-ingest** (CLI) reads the staging area and writes memory files with YAML front-matter to the librarian's workspace.
3. The **memory server** indexes these files for semantic search via `memory_search`.
4. The **librarian agent** uses `memory_search` to answer queries from the main agent.

---

## Key Design Decisions

### Why a Separate Agent (Not Shared Memory)

- **Clean separation:** Personal conversational memory (main) vs. reference documents (librarian).
- **Specialized behavior:** Lore is optimized for search and retrieval, not conversation. Uses Haiku for cost efficiency.
- **Independent scaling:** Different model, different resource profile.
- **Reusable:** Any agent can query Lore via the same skill pattern.

### Why Gateway WebSocket (Not MCP)

The ask-lore skill uses the existing gateway protocol rather than a dedicated MCP server. This was the simplest path:
- Zero new infrastructure — reuses `chat.send` and event streaming.
- Persistent sessions — Lore remembers context within `librarian/dm-fera`.
- The main agent implemented this itself when asked to find a communication channel.

A dedicated MCP server could be added later if lower-latency or structured tool interfaces are needed.

---

## Open Questions

1. **Ingestion scheduling:** Currently manual/cron. Should it run on the librarian's heartbeat?
2. **Syncthing conflicts:** `.sync-conflict-*` files are skipped by the indexer. Is that the right call?
3. **Direct adapter routing:** Should `@lore` mentions in Telegram/Mattermost route directly to the librarian agent?
