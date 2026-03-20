OpenClaw Gateway Control UI — Feature Overview
==============================================

Chat

- Full streaming chat with the agent directly in the browser (no messaging app needed)
- Live tool call cards — you see tool invocations as they happen
- Abort in-progress runs
- Inject assistant notes into the session transcript (without triggering an agent run)
- View chat history (bounded for UI safety; very large messages get truncated)

Sessions

- List all active sessions
- Per-session overrides for thinking level and verbose output
- Send /status to see context window usage, current toggles, and channel
credential freshness. Send /stop to abort a run and any spawned sub-agents. Send
/compact to summarize older context and free window space.

Channels

- Status overview for all connected channels (WhatsApp, Telegram, Discord, Slack, Signal, etc.)
- Per-channel config edits

Skills

- View installed/available skills and their status
- Enable/disable individual skills
- Update API keys for skills that need them

Cron Jobs

- List scheduled jobs

Exec Approvals

Edit gateway or node allowlists and set the ask policy for exec on gateway/node hosts Openclaw
Approve or deny pending shell command execution requests from the agent in real time

Config

Renders a form from the config schema, with a Raw JSON editor as an escape hatch Openclaw
Apply config changes and restart the gateway with validation

Nodes

List connected nodes (iOS/Android/Pi/remote machines) and their capabilities
Exec node binding — set which node a session targets for shell commands

Instances / Presence

Presence list showing active gateway instances
Refresh
