# Fera

A personal AI agent built on the [Claude Agent SDK](https://github.com/anthropics/claude-code), heavily inspired by [OpenClaw](https://github.com/openclaw/openclaw).

Fera is an always-on AI assistant with its own persistent memory and personality. It connects to your life through multiple I/O channels and wakes up periodically to check if there's work to do.

## Architecture

```
                    +-----------+
                    |  Heartbeat|  (wakes every 30 min)
                    +-----+-----+
                          |
                          v
+--------+  +--------+  +-----------+  +----------+
| Signal |  | Email  |  |   Fera    |  | Webhooks |
+--------+  +--------+  |   Core    |  +----------+
| Terminal|  | Web UI |  | (Claude)  |  | External |
+--------+  +--------+  +-----------+  | Services |
     I/O Channels            |         +----------+
                             |
                    +--------+--------+
                    |                 |
              +-----+-----+   +------+------+
              |  Memory    |   |    MCP      |
              | (persistent|   | Connectors  |
              |  journal)  |   | (Redmine,   |
              +------------+   |  Home Asst, |
                               |  ...)       |
                               +-------------+
```

## Core Concepts

### Agent Core
The brain. Built on the Claude Agent SDK, this is the central reasoning engine. It has a defined personality, maintains conversational context, and decides what to do based on incoming messages, scheduled wake-ups, and external events.

### I/O Channels
Pluggable adapters for communication:
- **Web UI** — browser-based chat interface
- **Telegram** — bot integration with streaming responses, voice transcription, and file handling
- **Email** — read-only access + drafts via `himalaya-wrapper`
- **Mattermost** — planned
- **Signal** — planned (via signal-cli)

Each adapter normalizes messages into a common format and routes them through the gateway's event bus.

### Persistent Memory
The agent remembers things across sessions. This goes beyond simple conversation history — it includes facts, preferences, decisions, and learned behaviors stored in a structured journal.

### Heartbeat
A scheduler that wakes the agent every 30 minutes (configurable). On wake, the agent checks for pending tasks, unread messages, calendar events, or anything else that needs attention. If there's nothing to do, it goes back to sleep.

### Webhooks
An HTTP endpoint that external systems can call to notify Fera of events. This enables integrations with CI/CD, monitoring, calendar systems, or anything that can send an HTTP request.

### MCP Integrations
Fera uses the [Model Context Protocol](https://modelcontextprotocol.io/) to interact with external services:
- **Project management** (Redmine, GitHub Issues, etc.)
- **Home automation** (Home Assistant)
- **Custom tools** as needed

## Tech Stack

- **Language**: Python 3.11
- **Agent framework**: Claude Agent SDK (Python)
- **Gateway**: WebSocket server (websockets)
- **Memory**: Markdown files indexed in SQLite (FTS5 + sqlite-vec)
- **Embeddings**: Local ONNX via fastembed (all-MiniLM-L6-v2)
- **Search**: Hybrid RRF fusion + optional Haiku reranking/query expansion
- **Build**: hatchling, managed with uv

## Getting Started

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Claude Code CLI installed.
- Ensure an `ANTHROPIC_API_KEY` is in your environment **or** you have a Claude Code OAuth token (obtained via `claude setup-token`).
- (Optional) `ANTHROPIC_API_KEY` for deep search mode (query expansion + reranking)

### Install

```bash
uv sync
```

### Run

Two terminals:

**Terminal 1 — Gateway:**
```bash
# Without deep search:
uv run fera-gateway

# With deep search (query expansion + reranking):
ANTHROPIC_API_KEY=sk-... uv run fera-gateway
```

**Terminal 2 — Web UI:**
```bash
uv run fera-webui
```

The gateway listens on `localhost:8389`. The web UI connects and gives you a
chat interface at `http://localhost:8080`.

### Configuration

Fera reads configuration from `$FERA_HOME` (defaults to `$HOME`). All config
files support `${VAR}` environment variable substitution.

#### File Layout

```
$FERA_HOME/
  config.json              # Global config (optional, merged with defaults)
  cron.json                # Scheduled job definitions (optional)
  agents/
    main/                  # Default agent
      config.json          # Per-agent config (optional)
      workspace/           # Agent workspace files
        persona/           # SOUL.md, IDENTITY.md, USER.md, GOALS.md, SOUVENIR.md
        memory/            # Markdown memory files
        AGENTS.md
        TOOLS.md
        HEARTBEAT.md       # Heartbeat task list
        BOOTSTRAP.md
        MEMORY.md
      data/                # Per-agent runtime data
    coding/                # Example second agent
      config.json
      workspace/
      data/
  data/
    sessions.json          # Session name -> SDK session ID mapping
    transcripts/           # Per-session JSONL transcript files
```

#### Global Config (`$FERA_HOME/config.json`)

Merged with defaults. Only specify keys you want to override.

```json
{
  "gateway": {
    "host": "127.0.0.1",
    "port": 8389,
    "auth_token": "auto-generated-on-first-run",
    "pool": {
      "max_clients": 5,
      "idle_timeout_minutes": 30
    }
  },
  "memory": {
    "host": "127.0.0.1",
    "port": 8390
  },
  "webui": {
    "host": "0.0.0.0",
    "port": 8080,
    "static_dir": "/opt/fera/webui/dist"
  },
  "heartbeat": {
    "enabled": false,
    "interval_minutes": 30,
    "active_hours": "08:00-22:00",
    "session": "default",
    "agent_tools": null
  },
  "mcp_servers": {
    "redmine": {
      "type": "sse",
      "url": "${REDMINE_MCP_URL}"
    }
  }
}
```

| Section | Key | Default | Description |
|---------|-----|---------|-------------|
| `gateway` | `host` | `"127.0.0.1"` | Bind address |
| | `port` | `8389` | WebSocket port |
| | `auth_token` | *(auto-generated)* | Token for client authentication |
| | `pool.max_clients` | `5` | Max pooled SDK clients |
| | `pool.idle_timeout_minutes` | `30` | Idle client eviction timeout |
| `memory` | `host` | `"127.0.0.1"` | Memory server bind address |
| | `port` | `8390` | Memory server port |
| `webui` | `host` | `"0.0.0.0"` | Web UI bind address |
| | `port` | `8080` | Web UI port |
| | `static_dir` | `"/opt/fera/webui/dist"` | Path to built web UI assets |
| `heartbeat` | `enabled` | `false` | Enable periodic heartbeat turns |
| | `interval_minutes` | `30` | Minutes between heartbeat checks |
| | `active_hours` | `"08:00-22:00"` | Time window for heartbeat (local time) |
| | `session` | `"default"` | Session to run heartbeat turns in |
| | `agent_tools` | `null` | Tools for the heartbeat sub-agent. `null` inherits all parent tools. Set to a list (e.g. `["Read", "Grep", "Bash"]`) to restrict. |
| `mcp_servers` | | `{}` | Global MCP servers available to all agents |

#### Per-Agent Config (`$FERA_HOME/agents/<name>/config.json`)

```json
{
  "mcp_servers": {
    "homeassistant": {
      "type": "sse",
      "url": "${HA_MCP_URL}"
    }
  },
  "allowed_tools": ["Read", "Glob", "mcp__memory__*", "mcp__homeassistant__*"],
  "plugins": [
    {"type": "local", "path": "plugins/superpowers"}
  ],
  "adapters": {
    "telegram": {
      "bot_token": "${TELEGRAM_BOT_TOKEN}",
      "allowed_users": [123456789],
      "default_session": "default",
      "trusted": false
    }
  }
}
```

| Key | Description |
|-----|-------------|
| `mcp_servers` | Per-agent MCP servers (merged with global servers) |
| `allowed_tools` | Explicit tool allowlist. When set, replaces the auto-generated list entirely. Omit to use the default (built-in memory tools + wildcards for all configured MCP servers). Supports exact names and wildcards. Built-in Claude tools: `Read`, `Write`, `Edit`, `Bash`, `Glob`, `Grep`, `WebFetch`, `WebSearch`, `Task`, `TodoRead`, `TodoWrite`. Memory MCP tools: `mcp__memory__memory_search`, `mcp__memory__memory_get`. Use `mcp__<server>__*` for MCP server wildcards. |
| `plugins` | Claude Code plugins to load. Relative paths resolve against workspace. |
| `adapters.telegram.bot_token` | Telegram bot API token |
| `adapters.telegram.allowed_users` | List of allowed Telegram user IDs |
| `adapters.telegram.default_session` | Session name for Telegram conversations |
| `adapters.telegram.trusted` | If `true`, skip untrusted content wrapping for text/voice |

#### Cron Jobs (`$FERA_HOME/cron.json`)

Scheduled job definitions triggered by OS crontab via `fera-run-job`.

```json
{
  "jobs": {
    "morning-digest": {
      "agent": "main",
      "payload": "Check Redmine and summarize my open tickets.",
      "prompt_mode": "minimal"
    },
    "medication-reminder": {
      "agent": "main",
      "session": "default",
      "payload": "Remind the user to take their vitamins."
    }
  }
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `agent` | `"main"` | Agent to run the job as |
| `session` | `null` | Named session (persistent, full context). Omit for ephemeral mode. |
| `payload` | *(required)* | User message text sent to the agent |
| `prompt_mode` | `"full"`/`"minimal"` | System prompt mode. Defaults to `"full"` for session jobs, `"minimal"` for ephemeral. |

Crontab example:
```crontab
0 8 * * *   fera-run-job morning-digest
0 9 * * *   fera-run-job medication-reminder
```

### CLI Commands

| Command | Description |
|---------|-------------|
| `fera-gateway` | Start the WebSocket gateway |
| `fera-memory-server` | Start the memory server |
| `fera-webui` | Start the web UI server |
| `fera-create-agent <name>` | Initialize a new agent from templates |
| `fera-run-job <name>` | Execute a cron job via the gateway |

### Development

```bash
uv run pytest          # run all tests
uv run pytest -v       # verbose
make dev               # dev container (Podman)
make test              # run tests in container
```

### Ports

| Port | Service | Bind Address | Protocol |
|------|---------|--------------|----------|
| 8080 | fera-webui | 0.0.0.0 | HTTP |
| 8389 | fera-gateway | 127.0.0.1 | WebSocket |
| 8390 | fera-memory-server | 127.0.0.1 | HTTP (SSE/MCP) |

The gateway and memory server listen on localhost by default. The web UI binds to `0.0.0.0`. For remote access to the gateway, set `gateway.host` to `"0.0.0.0"` in config. See [INSTALL.md](INSTALL.md) for firewall recommendations.

## Status

Early development. Gateway, web UI, and memory system are functional.

## Reference

- [OpenClaw](https://github.com/openclaw/openclaw) — the primary inspiration for this project
- [Claude Agent SDK](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/sdk)
- [Model Context Protocol](https://modelcontextprotocol.io/)
