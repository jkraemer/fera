# Fera Design: System Prompt & Workspace Initialization

How Fera initializes agent workspaces and dynamically composes system prompts.

---

## 1. Workspace Initialization

### Template Seeding

On first run (or when a new agent is created via `fera-create-agent`), `init_agent()` in `src/fera/setup.py` copies template files from `src/fera/templates/` into the agent's workspace directory at `$FERA_HOME/agents/{name}/workspace/`.

The resulting directory structure:

```
$FERA_HOME/agents/{name}/
├── workspace/
│   ├── AGENTS.md          # Workspace rules, memory protocol, safety, heartbeat guidance
│   ├── BOOTSTRAP.md       # First-run initialization (self-deleting)
│   ├── HEARTBEAT.md       # Periodic task checklist
│   ├── MEMORY.md          # Long-term fact storage (used by memory_search)
│   ├── TOOLS.md           # Local environment notes (cameras, SSH, TTS)
│   ├── persona/
│   │   ├── GOALS.md       # Mission statement and active goals
│   │   ├── IDENTITY.md    # Agent name, creature type, vibe, emoji
│   │   ├── SOUL.md        # Personality, values, tone, boundaries
│   │   ├── SOUVENIR.md    # Reflection layer, timestamped learnings
│   │   └── USER.md        # Human's info (name, timezone, preferences)
│   └── memory/            # Daily memory files written by archivist
└── data/                  # Per-agent runtime data
```

### Placeholder Substitution

Templates contain `{{WORKSPACE_PATH}}` placeholders. During init, all `.md` files are scanned and the placeholder is replaced with the actual absolute workspace path.

### Idempotent Safety

`ensure_agent()` is called on every startup. If the workspace already exists, it's a no-op — existing files are never overwritten.

---

## 2. Prompt Modes

`SystemPromptBuilder` (in `src/fera/prompt.py`) assembles prompts from workspace files. It supports three modes:

### Mode: `full`
- **Use case:** Direct human interaction (web chat, Telegram, Mattermost).
- **Includes:** Base identity, security block, canary token, all 10 context files, runtime info.

### Mode: `minimal`
- **Use case:** Background tasks (ephemeral cron jobs, sub-agents).
- **Includes:** Base identity, security block, `AGENTS.md` and `TOOLS.md` only.

### Mode: `none`
- **Use case:** Diagnostics, health checks.
- **Returns:** Just the base identity line: `"You are a personal AI agent running inside Fera."`

---

## 3. Prompt Composition

The final system prompt is built from these blocks, in order:

### Block 1: Base Identity
Always present: `"You are a personal AI agent running inside Fera."`

### Block 2: Security
Defines `<untrusted>` tag behavior — teaches the agent to treat external content as data, not instructions.

### Block 3: Canary Token (optional)
Format: `CANARY:{hex_token}`. Instructs the agent to never reproduce the token. Used by the runner to detect prompt injection — if the token appears in agent output, an `agent.alert` event is raised and the turn is interrupted.

### Block 4: Context Files
Loaded from workspace in a fixed order. In `full` mode, all 10 files are loaded. In `minimal` mode, only `AGENTS.md` and `TOOLS.md`.

Each file is wrapped in XML:
```xml
<file path="persona/SOUL.md">
[content]
</file>
```

If `SOUL.md` exists, the preamble reads: *"defines your workspace. SOUL.md defines your persona and tone — embody it."* Otherwise, the generic preamble is used.

**Truncation:** Files exceeding 20,000 characters are truncated (head 70% + tail 20%, joined by a `[truncated]` marker). A total budget of 150,000 characters is enforced across all files — once exhausted, remaining files are skipped.

### Block 5: Runtime
Current date, day of week, time, and timezone.

---

## 4. Canary Token Lifecycle

Each session gets a unique canary token (UUID hex) on creation, stored in `sessions.json`. The token is:
1. Embedded in the system prompt (Block 3).
2. Checked against every `agent.text` event by the runner.
3. Refreshed when a session's SDK state is cleared.

If the agent reproduces the token (possible indicator of prompt injection), the runner logs an alert with a SHA256 hash and publishes a `security.alert` event.

---

## 5. Key Differences from Earlier Design

- **No reasoning tags:** The `<think>` / `<final>` tag injection mentioned in earlier notes is not implemented.
- **No tooling block:** Tool configuration is handled by the Claude Agent SDK (`ClaudeAgentOptions.allowed_tools`), not by a prompt section.
- **No model ID in prompt:** The runtime block includes date/time/timezone but not the model ID.
- **Workspace path not in prompt body:** The absolute path is baked into template files via placeholder substitution, not injected as a separate prompt block.
