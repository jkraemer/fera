# Fera Design: The Dream Cycle (Nightly Maintenance)

A nightly autonomous process that archives session transcripts, crystallizes knowledge into durable memory files, and consolidates the day's learnings.

---

## 1. Architecture

The dream cycle runs as an internal async loop inside the gateway process (`DreamCycleScheduler` in `src/fera/gateway/dream_cycle.py`). It triggers at a configurable wall-clock time (default: 03:00) and processes each configured agent's active sessions.

It is **not** a separate systemd service or crontab entry — it runs embedded in the gateway for as long as the gateway is up.

---

## 2. The Three Phases

### Phase 1: Archive Transcripts

For each active session (those with an SDK session ID):

1. Set a target date mapping so JIT memory files are filed under the correct day.
2. Spawn an "Archivist" agent turn via `MemoryWriter` to extract durable facts from the session transcript.
3. Write facts to `memory/timeline/YYYY-MM-DD/NNN.md` (sequential numbering).
4. Call `clear_session()` to reset the SDK state — the next turn starts with a fresh context.

If the session lane is busy (another turn in progress), the scheduler retries once after 5 minutes, then skips if still locked. If the archivist turn itself fails, the session is **not** cleared (preserving state for debugging).

### Phase 2: Synthesis

Call `MemoryWriter.write_synthesis()` to read all `*.md` files in the day's timeline directory and consolidate them into a single `summary.md`.

### Phase 3: MEMORY.md Update

Call `MemoryWriter.update_memory_md()` to selectively add permanent facts from the synthesis to the agent's `MEMORY.md`. Only facts meeting strict criteria are added (would cause mistakes if omitted, stable, not redundant).

**Phase dependency:** Phase 3 is skipped if Phase 2 failed.

---

## 3. Data Layout

```
$FERA_HOME/agents/{agent}/workspace/
  memory/
    timeline/
      2026-03-19/
        001.md          ← JIT write (from pre-compaction hook during the day)
        002.md          ← Another session's JIT write
        summary.md      ← Phase 2 synthesis
      2026-03-20/
        ...
  MEMORY.md             ← Phase 3 curated long-term facts
```

Transcript files live in `$FERA_HOME/data/transcripts/` and are rotated (renamed with UTC timestamp suffixes) before archiving.

---

## 4. Error Handling

- **Per-agent isolation:** Each agent is processed independently. One agent's failure doesn't block others.
- **Phase dependency:** If synthesis fails, MEMORY.md update is skipped.
- **Auto-restart:** If the scheduler loop crashes unexpectedly, it logs the error and restarts itself.
- **Timezone-aware:** Uses configured timezone for date boundary calculations (important when the cycle runs at 03:00 and needs to file work under "yesterday").

---

## 5. The Archivist

The dream cycle delegates all LLM work to `MemoryWriter` (`src/fera/gateway/memory_writer.py`), which spawns minimal ephemeral agent turns with:

- A specialized "Fera Memory Archivist" system prompt.
- Only `Read`, `Glob`, and `Write` tools (no `Bash`).
- The same archivist is used for both the nightly cycle and the daytime JIT memory flush (via the `PreCompact` hook).

See `docs/fera-memory-writing-compaction.md` for full details on the archivist and JIT memory writing.

---

## 6. Configuration

In `$FERA_HOME/config.json`:

```json
{
  "dream_cycle": {
    "enabled": false,
    "time": "03:00",
    "agents": [],
    "model": null
  }
}
```

| Field | Default | Description |
|---|---|---|
| `enabled` | `false` | Whether the nightly cycle runs |
| `time` | `"03:00"` | Wall-clock trigger time (24h format) |
| `agents` | `[]` | List of agent names to process (e.g., `["main"]`) |
| `model` | `null` | Optional model override for archivist turns |

---

## 7. Not Implemented

The following items from earlier design thinking are **not** in the codebase:

- **Backup tarballs / snapshot rotation:** No automated compression, archiving, or retention of old data.
- **Vector index updates:** No `qmd update` / `qmd embed` integration.
- **Morning re-priming:** Sessions are cleared but not explicitly re-primed with a `current_status.md` summary. The system prompt's context file injection provides continuity instead.
- **Separate "Librarian" persona:** The dream cycle uses the same Archivist prompt as the JIT hook, not a distinct Librarian agent.
