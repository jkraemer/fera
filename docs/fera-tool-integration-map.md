# Fera Tool Integration Map

How Fera integrates with external services and exposes capabilities to the agent.

---

## 1. Scheduling & Cron Jobs

Fera uses a **config-driven job system**, not agent-managed crontab editing.

- **Job definitions:** Stored in `$FERA_HOME/cron.json` as a static JSON file. Each job specifies a `payload` (prompt text), `agent`, optional `session`, `model`, and `allowed_tools`.
- **Triggering:** The OS crontab calls `fera-run-job <name>`, which connects to the gateway WebSocket and sends a `cron.run` request. The gateway dispatches to either a session-based or ephemeral execution path.
- **Two modes:** Session jobs (persistent context via `run_turn()`) and ephemeral jobs (stateless one-shot via `run_oneshot()`). See `docs/fera-cron-routing-design.md` for details.

---

## 2. Messaging Adapters

### Telegram
- **Library:** `python-telegram-bot`
- **Connection:** Long polling via `updater.start_polling()`
- **Auth:** `allowed_users` dict maps canonical names to Telegram user IDs. Non-authorized senders are rejected with their user ID displayed.
- **Session routing:** DMs → `dm-{canonical}`, groups → `tg-{slug}`
- **Media:** Downloads files to workspace inbox. Voice messages are transcribed via Whisper.
- **Streaming:** Draft message pattern with throttled edits (1s throttle).

### Mattermost
- **Library:** `mattermostdriver`
- **Connection:** WebSocket (`wss://{url}/api/v4/websocket`) with token auth. Exponential backoff on disconnect (5s → 30min cap).
- **Auth:** Maps Mattermost usernames to canonical names. Non-authorized users rejected in DMs.
- **Session routing:** Groups by channel and `root_id` (thread).
- **Streaming:** Ellipsis draft posts with throttled updates (1s throttle).
- **File handling:** Downloads file attachments to workspace inbox.

Both adapters subscribe to sessions via the gateway's EventBus and receive agent events for the sessions they initiated.

---

## 3. Proactive Messaging

There is no `send_owner_message()` function. Proactive messaging uses the **EventBus**:

- Adapters subscribe to sessions using `context.subscribe(session, callback)`.
- Events are published to all subscribers via `make_event()`.
- Heartbeat turns publish `agent.text` events with a `target_adapter` stamp (derived from the session's `last_inbound_adapter`).
- Security alerts (`security.alert`) and agent alerts (`agent.alert`) bypass adapter/source filters and are sent to all subscribed adapters.

---

## 4. MCP Integrations

### Built-in: Memory Server
Always present. SSE-based MCP server at `http://127.0.0.1:8390/sse?agent={agent_name}`.

Tools: `mcp__memory__memory_search`, `mcp__memory__memory_get`.

### External: Config-Driven
Additional MCP servers (Home Assistant, Redmine, etc.) are configured in `$FERA_HOME/config.json` or per-agent `config.json`. They are loaded at runtime by `GatewayMcpManager` and merged into the agent's MCP server list by `build_mcp_servers()`.

Tool allowlisting automatically generates wildcard entries (`mcp__{server}__*`) for each configured server.

---

## 5. SDK Built-in Tools

The Claude Agent SDK provides file and terminal tools:

- **`Bash`**: Execute commands in the host shell.
- **`Read`**: Read files (restricted to workspace via `cwd` in `ClaudeAgentOptions`).
- **`Write`**: Create files.
- **`Edit`**: Line-by-line modifications (`str_replace` style).
- **`WebSearch` / `WebFetch`**: Browse and fetch web content.

Tool access is controlled at three levels:
1. **Global:** `ALLOWED_TOOLS` in `src/fera/agent.py` (defaults to memory MCP tools).
2. **Per-agent:** `allowed_tools` / `disabled_tools` in agent config.
3. **Per-job:** `allowed_tools` in cron job spec.

A `PreToolUse` denial hook (`build_tool_deny_hook()`) enforces disabled tools at runtime.

---

## 6. Email Integration (`himalaya`)

Read-only email access through a security-hardened wrapper around the `himalaya` CLI.

### Rust Wrapper (`tools/himalaya-wrapper/`)
- **Setuid binary** running as `himalaya-svc` user.
- **Credential isolation:** Config at `/home/himalaya/.config/himalaya/config.toml` (mode 600), readable only by `himalaya-svc`.
- **Command allowlist:** `account list`, `attachment download`, `envelope list`, `flag add/remove`, `folder list`, `message export`, `message read`, `message save`, `template save`.
- **Blocked commands:** `message send`, `message write`, `message reply`, `message forward`, `template send`.
- **Environment scrubbing:** Clears all env vars except `HOME` and `PATH`.

### Python Wrapper (`src/fera/skills/himalaya/himalaya_safe.py`)
- Post-processes himalaya-wrapper output.
- Wraps email content in `<untrusted source="email">` tags.
- Sanitizes JSON envelope fields (strips control characters).
- For large emails (>50KB), writes to temp file with untrusted warning.

### Draft-Only Workflow
To "send" an email, Fera saves a draft to the IMAP `Drafts` folder via `message save` or `template save`. The human reviews and sends from their own email client.

A skill in `src/fera/skills/himalaya/SKILL.md` teaches the agent how to use the wrapper and compose messages using MML (Himalaya's markup language).

---

## 7. Heartbeat

Periodic proactive agent turns via an internal async scheduler (not crontab). Configured in `config.json` under `heartbeat`. See `docs/fera-heartbeat-design.md` for details.

---

## 8. Summary

| Integration | Technology | Safety Mechanism |
|---|---|---|
| **Scheduling** | Config-driven JSON jobs + OS crontab | Jobs pre-defined, not agent-editable |
| **Telegram** | python-telegram-bot (long polling) | User ID allowlist |
| **Mattermost** | mattermostdriver (WebSocket) | Username-to-canonical mapping |
| **Proactive alerts** | EventBus pub/sub | Adapter subscription model |
| **MCP** | Built-in memory + config-driven extras | Per-server tool wildcards |
| **File I/O** | SDK built-ins | `cwd` path restriction |
| **Terminal** | SDK `Bash` tool | Agent's OS user permissions |
| **Email** | himalaya-wrapper (Rust setuid) | Command allowlist + env scrubbing |
