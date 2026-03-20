# Fera Design: Heartbeat Mechanism (Proactive Agent Turns)

The heartbeat is a periodic background process that allows the agent to proactively review its workspace and alert the user without being prompted.

---

## 1. Scheduling

The heartbeat runs as an internal async loop inside the gateway process (`HeartbeatScheduler` in `src/fera/gateway/heartbeat.py`). It is **not** a systemd timer or crontab entry — it runs for as long as the gateway is up.

The loop sleeps for `interval_minutes`, then calls `tick()`. On unexpected errors, it logs and auto-restarts.

---

## 2. Safety Gates (Pre-Flight Checks)

Before calling the LLM, `tick()` evaluates three conditions:

### A. Active Hours Check
`is_active_hours()` parses the `"HH:MM-HH:MM"` range from config. Handles midnight wrapping (e.g., `"22:00-06:00"`). Timezone-aware via `ZoneInfo`.

### B. Concurrency Guard
Calls `self._lanes.is_locked(session)` to check if another turn is in progress. Skips heartbeat if the session lane is busy.

### C. Actionable Content Check
`has_heartbeat_content(workspace)` returns `True` if either:
- `HEARTBEAT.md` has non-empty, non-comment content, **or**
- Files exist in the `workspace/inbox/heartbeat/` directory.

If neither condition is met, the tick is skipped entirely (no API call).

---

## 3. The Heartbeat Turn

When all gates pass, the scheduler builds an instruction and runs a turn via `AgentRunner.run_turn()`:

```
Review your workspace HEARTBEAT.md and process any pending tasks listed there.
If nothing requires the user's attention, reply with just 'HEARTBEAT_OK'.
If something needs attention, report it.
```

If inbox files exist in `workspace/inbox/heartbeat/`, they are listed in the instruction and the agent is told to process them.

The turn runs in the session configured by `heartbeat.session` (default: `"default"`, qualified to `"{agent}/default"`). The prompt mode is `"full"` (not minimal).

---

## 4. Silent Response Handling

The agent can signal "nothing to do" by including `HEARTBEAT_OK` in its response. The gateway handles this via two cooperating pieces:

### A. Stripping (`protocol.py`)
`strip_silent_suffix()` removes `HEARTBEAT_OK`, `(HEARTBEAT_OK)`, or `(silent)` from the end of agent text. This runs inside `translate_message()` during event processing, so empty text blocks are never emitted as `agent.text` events.

### B. Detection (`heartbeat.py`)
`is_heartbeat_ok(events)` checks whether any `agent.text` events with non-empty text remain after stripping. If none remain, the heartbeat is considered silent.

### C. Effect
- **Silent heartbeat:** Events are **not** published to the EventBus. The session history retains the turn, but no adapter or UI sees it.
- **Actionable heartbeat:** Non-tool events (`agent.text`, `agent.done`) are published to the EventBus with a `target_adapter` stamp (derived from `last_inbound_adapter` for the session). Tool use/result events are suppressed.

---

## 5. Inbox Archival

After a successful turn, processed inbox files are moved to a `.processed/` subdirectory with a timestamped prefix (`YYYYMMdd_HHMMSS_ffffff_filename`). Files are only archived if the turn completed without error.

---

## 6. Configuration

In `$FERA_HOME/config.json`:

```json
{
  "heartbeat": {
    "enabled": false,
    "interval_minutes": 30,
    "active_hours": "08:00-22:00",
    "session": "default",
    "agent_tools": null
  }
}
```

| Field | Default | Description |
|---|---|---|
| `enabled` | `false` | Whether the heartbeat loop runs |
| `interval_minutes` | `30` | Sleep between ticks |
| `active_hours` | `"08:00-22:00"` | Time window for heartbeat execution (supports midnight wrapping) |
| `session` | `"default"` | Session name (auto-qualified with agent name) |
| `agent_tools` | `null` | Optional tool override for heartbeat turns (planned for sub-agent support) |

---

## 7. Not Implemented

The following items from earlier design thinking are **not** in the codebase:

- **Session file truncation ("Snapshot & Truncate"):** Silent heartbeats are handled by suppressing event publication, not by truncating JSONL files.
- **Alert deduplication via hashing:** No hash-based suppression of duplicate alerts.
- **Sub-agent delegation:** Planned (see `docs/plans/2026-03-12-heartbeat-subagent-plan.md`) but not yet implemented.
