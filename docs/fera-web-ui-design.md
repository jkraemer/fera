# Fera Design: Web Management UI

A real-time SPA for chatting with the agent, editing workspace files, viewing logs, and monitoring the system. Runs as a separate process from the gateway.

---

## 1. Architecture

The web UI is a **separate server** (`fera-webui.service` systemd unit). It serves built SPA assets over HTTP and proxies WebSocket connections to the gateway on localhost.

```
Browser
  ├── HTTP GET static assets ──► fera-webui (port 8080)
  ├── GET /config.json ─────────► returns {"gateway_ws": "ws://.../"}
  └── WebSocket /ws ────────────► fera-webui ──► fera-gateway (127.0.0.1:8389)
```

The gateway binds to `127.0.0.1` and is not directly reachable from outside the host. The web UI's `/ws` endpoint proxies WebSocket frames bidirectionally to the gateway.

**Server implementation:** Starlette ASGI app (`src/fera/webui/server.py`) using `uvicorn`. Serves static files from a configurable `static_dir` and provides the `/config.json` and `/ws` endpoints.

---

## 2. Views

### A. Chat

Session-based chat using the gateway protocol.

- **Session sidebar:** Lists sessions grouped by agent, with stats (turns, tokens, cost). Supports create, deactivate, and delete actions.
- **Agent selector:** Modal for choosing which agent a new session belongs to.
- **Message display:** Bubble layout — user messages right-aligned (orange/white), agent messages left-aligned (zinc/gray).
- **Tool calls:** Collapsible blocks for `agent.tool_use` (name + input) and `agent.tool_result` (output, with syntax highlighting).
- **Input:** Textarea with Enter-to-send, Shift+Enter for newlines.
- **History:** Loaded on-demand via `session.history` when selecting a session. Live events stream in real-time for the active session.
- **Interrupt:** `chat.interrupt` to cancel an in-progress turn.

### B. Workspace Explorer

File browser and editor for the agent's workspace.

- **Agent selector bar:** Switches between agents (if multiple exist).
- **File tree:** Breadcrumb navigation through workspace directories.
- **Editor:** CodeMirror 6 with markdown syntax highlighting and one-dark theme.
- **Save:** Writes changes via `workspace.set`. Changes are picked up by the memory server's filesystem watcher automatically.

### C. Logs

Scrolling log viewer with live streaming.

- **Sidebar:** Live toggle, date picker, log level filter, category filter (system, session, adapter, client, turn, tool, exception).
- **Main panel:** Searchable, collapsible log entries with inline summaries.
- **Live mode:** Subscribes to `log.entry` events pushed over the WebSocket. Caps in-memory entries at 2000.
- **Historical:** Loads JSONL entries for a selected date via `logs.read`.

### D. MCP Servers

Lists configured MCP servers with their scope, type, URL, and headers.

### E. Status

System health dashboard.

- **Summary cards:** Uptime, active sessions, turns today, cost today, token breakdown.
- **Adapter status:** Green/red indicators for each connected adapter.
- **Sparkline charts:** Turns/hr, tokens/hr, cost/hr over the last 24 hours.
- **Auto-refresh:** Every 60 seconds.

---

## 3. Gateway Protocol

The gateway speaks JSON-RPC over WebSocket with three frame types.

### Request (`req`)
```json
{"type": "req", "id": "uuid", "method": "chat.send", "params": {"text": "...", "session": "main/default"}}
```

### Response (`res`)
```json
{"type": "res", "id": "uuid", "ok": true, "payload": {...}}
```
On error: `"ok": false, "error": "message"`.

### Event (`event`)
```json
{"type": "event", "event": "agent.text", "session": "main/default", "data": {"text": "...", "html": "..."}}
```

### Implemented Methods

| Method | Params | Response payload |
|---|---|---|
| `connect` | `{token}` | `{version, sessions, agents}` |
| `agents.list` | — | `{agents: [...]}` |
| `session.list` | — | `{sessions: [...]}` |
| `session.create` | `{name, agent?}` | session info |
| `session.deactivate` | `{session}` | — |
| `session.delete` | `{session}` | — |
| `session.history` | `{session}` | `{messages: [...]}` |
| `session.stats` | `{session}` | `{turns, tokens, cost}` |
| `chat.send` | `{text, session}` | ack (events stream async) |
| `chat.interrupt` | `{session}` | — |
| `workspace.list` | `{path?, agent?}` | `{files: [...]}` |
| `workspace.get` | `{path, agent?}` | `{content}` |
| `workspace.set` | `{path, content, agent?}` | — |
| `mcp.list` | — | `{servers: [...]}` |
| `logs.list` | — | `{dates: [...]}` |
| `logs.read` | `{date, level?}` | `{entries: [...]}` |
| `status.summary` | — | `{uptime, sessions, turns, cost, adapters}` |
| `status.metrics` | `{metrics, range?, bucket?}` | time-series data |
| `cron.run` | `{job}` | result dict |
| `question.answer` | `{session, question_id, answer}` | — |

### Event Types

| Event | Data | Notes |
|---|---|---|
| `user.message` | `{text, source}` | source: "web", "telegram", etc. |
| `agent.text` | `{text, html}` | html is server-rendered markdown |
| `agent.tool_use` | `{name, input}` | |
| `agent.tool_result` | `{content, is_error}` | |
| `agent.done` | `{model, input_tokens, output_tokens, cost_usd, ...}` | |
| `agent.error` | `{error}` | |
| `agent.compact` | `{pre_tokens}` | context window compaction |
| `agent.alert` | `{...}` | canary token detection |
| `security.alert` | `{source, patterns, excerpt}` | injection detection |
| `log.entry` | log entry object | live log streaming |

---

## 4. Authentication

The gateway uses token-based auth in the `connect` handshake (configured via `gateway.auth_token` in config, auto-generated on first run). The web UI proxies all WebSocket traffic, so adding HTTP-level auth (e.g., basic auth via a reverse proxy) secures both static assets and the gateway in one place.

The frontend stores the token in `localStorage` and prompts for it on first visit.

---

## 5. Technology Stack

- **Frontend:** Vite + vanilla TypeScript
- **Styling:** Tailwind CSS v4 (dark zinc/orange theme)
- **Editor:** CodeMirror 6
- **WebSocket client:** Custom wrapper (`gateway-client.ts`) matching the gateway protocol
- **Server:** Starlette ASGI (uvicorn)
- **Deployment:** `fera-webui.service` systemd unit, depends on `fera-gateway.service`

---

## 6. Configuration

In `$FERA_HOME/config.json`:

```json
{
  "webui": {
    "host": "0.0.0.0",
    "port": 8080,
    "static_dir": "/opt/fera/webui/dist"
  }
}
```

The browser fetches `/config.json` from the web UI server to discover the WebSocket proxy URL. The server builds this from the request's `Host` header so it works regardless of hostname.
