# Fera Design: Memory Writing & Compaction

How Fera persists durable knowledge before conversation history is compacted, and how the nightly dream cycle consolidates it.

---

## 1. The Problem

When a session's token count approaches the context limit, the Claude Agent SDK runs an automatic compaction — summarizing history and clearing raw logs. Any facts not saved beforehand are lost. Fera uses a `PreCompact` hook to flush important information to disk just in time.

---

## 2. PreCompact Hook (JIT Memory Flush)

### Wiring

The hook is registered in `AgentRunner._make_client()` (in `src/fera/gateway/runner.py`) via `ClaudeAgentOptions`:

```python
compact_hook = {
    "PreCompact": [HookMatcher(hooks=[self._memory_writer.make_pre_compact_hook(
        on_compact=on_compact,
        transcript_path=tp,
    )])],
}
```

### Behavior

When the SDK detects the context is near its limit:

1. The hook fires and optionally publishes a notification event to the user.
2. The transcript file is rotated (renamed with a UTC timestamp suffix) to prevent read/write conflicts.
3. `MemoryWriter.write_jit_memory()` spawns an ephemeral "Archivist" agent turn with a specialized prompt.
4. The archivist reads the transcript, extracts durable facts, and writes them to `memory/timeline/YYYY-MM-DD/NNN.md` (zero-padded sequential numbering, e.g., `001.md`, `002.md`).
5. The hook returns `{}` so the SDK proceeds with its own compaction.
6. Errors are swallowed and logged — the hook never blocks compaction.

### Target Date Mapping

A subtlety: when the dream cycle triggers compaction at 3 AM on day D+1, the JIT files should be filed under day D. `MemoryWriter` maintains a `_target_dates` dict mapping SDK session IDs to dates. The dream cycle sets this to yesterday before triggering; the hook pops the entry (single-use). Organic mid-session compaction has no entry and defaults to today.

---

## 3. The Archivist

`MemoryWriter` (in `src/fera/gateway/memory_writer.py`) spawns minimal ephemeral Claude SDK instances with:

- **System prompt:** "Fera Memory Archivist" persona.
- **Tools:** Only `Read`, `Glob`, `Write` (no `Bash`).
- **Three workflows:**
  - `write_jit_memory()` — Pre-compaction: read transcript, extract facts, write `NNN.md`.
  - `write_synthesis()` — Nightly: read all `*.md` in today's timeline dir, consolidate into `summary.md`.
  - `update_memory_md()` — Nightly: read synthesis + existing `MEMORY.md`, selectively add permanent facts.

### File Structure

```
workspace/
  memory/
    timeline/
      2026-03-19/
        001.md          ← JIT write (pre-compaction)
        002.md          ← Another JIT write (same day, different session)
        summary.md      ← Dream cycle synthesis
      2026-03-20/
        001.md
        ...
  MEMORY.md             ← Long-term curated facts
```

### Selection Criteria for MEMORY.md

The archivist is instructed to only add facts that:
1. Would cause mistakes if omitted.
2. Are stable (won't change frequently).
3. Aren't already captured in workspace files.

---

## 4. Dream Cycle (Nightly Consolidation)

`DreamCycleScheduler` (in `src/fera/gateway/dream_cycle.py`) runs as an internal async loop inside the gateway process. It triggers at a configurable wall-clock time (default: 03:00) and processes each configured agent's sessions.

### Three Phases

**Phase 1 — Archive:** For each active session with an SDK session ID:
- Set the target date to yesterday.
- Run an archivist turn to extract durable facts.
- Call `/clear` on the session (clear SDK state so the next turn starts fresh).
- If the session lane is busy, retry once after 5 minutes, then skip.
- If the archivist turn fails, skip `/clear` to preserve session state.

**Phase 2 — Synthesis:** Call `write_synthesis()` to consolidate all of today's timeline files into `summary.md`.

**Phase 3 — MEMORY.md update:** Call `update_memory_md()` to selectively add permanent facts. Skipped if Phase 2 failed.

### Error Handling

- Agents are processed independently; one agent's failure doesn't block others.
- Phase dependency: Phase 3 is skipped if Phase 2 fails (don't update MEMORY.md without a good summary).
- The scheduler auto-restarts if its loop crashes.

### Configuration

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

---

## 5. Compact Notification

When the JIT hook fires, a notification is published to the user:

> "Auto-compaction running — context window is being summarised."

This lets the user know the session is being compacted and may take a moment.
