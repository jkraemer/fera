# OpenClaw Codebase Reference Guide

This document serves as a structured reference for the OpenClaw codebase, providing an architectural overview, core concept definitions, and implementation maps for the Fera AI assistant.

---

## 1. Project Overview & Tech Stack

OpenClaw is a multi-channel AI gateway and extensible messaging framework. It acts as a bridge between various messaging platforms (Telegram, WhatsApp, Signal, Discord, etc.) and Large Language Models (LLMs), providing a unified API for building agents that can interact across channels.

### Runtime Requirements
- **Node.js**: >= 22.12.0
- **Package Manager**: pnpm (v10.x)
- **OS**: Linux, macOS, Windows (WSL recommended)

### Key Dependencies
- **AI SDK**: `@mariozechner/pi-agent-core`, `@mariozechner/pi-ai`, `@mariozechner/pi-coding-agent` — provide the core reasoning loop and tool execution.
- **Messaging SDKs**: `@whiskeysockets/baileys` (WhatsApp), `grammy` (Telegram), `signal-utils` (Signal), `@slack/bolt` (Slack).
- **Configuration & Validation**: `zod` and `@sinclair/typebox` for schema definition and runtime validation.
- **Database**: `sqlite` with `sqlite-vec` for vector search and long-term memory.
- **CLI**: `commander` for command-line parsing.
- **Runtime**: `express` for HTTP endpoints and `ws` for the WebSocket gateway.

### Monorepo Structure
- `src/`: Core logic, gateway server, agent loop, channel abstractions, and routing.
- `extensions/`: Self-contained plugins that add new channels (e.g., Signal, Discord) or core features (e.g., memory backends).
- `skills/`: Reusable agent capabilities (tools/prompts) that can be installed into an agent's workspace.
- `packages/`: Internal shared libraries (e.g., `plugin-sdk`).
- `apps/`: Native companion apps (Android, iOS, macOS) and shared cross-platform code.
- `ui/`: Web-based control interface and dashboard.
- `docs/`: System documentation and user guides.

---

## 2. Architecture Map

### The Big Picture
OpenClaw consists of a **Gateway** (the server) and one or more **Agents** (the reasoning loops). The Gateway handles persistence, connectivity to messaging **Channels**, and **Routing** of messages to the appropriate **Session**.

### The Gateway
The Gateway is the primary long-running process. It manages the lifecycle of channel connections and exposes an RPC interface (via WebSocket/TCP) for the CLI and TUI.
- **Startup Trace**: `src/entry.ts` → `src/cli/run-main.ts` → `src/cli/program.ts` → `gateway` command (in `src/cli/gateway-cli/register.ts`) → `src/cli/gateway-cli/run.ts` → `src/gateway/server.ts`.
- **Primary Function**: Listens on a WebSocket port, authenticates clients, and maintains active channel adapters.

### The Agent (AI Reasoning Loop)
The agent logic is encapsulated in the "pi-embedded-runner". It handles the iterative process of:
1.  Assembling the system prompt and conversation history.
2.  Calling the LLM.
3.  Executing requested tools.
4.  Feeding tool results back into the LLM until a final response is generated.
- **Trace**: `src/agents/pi-embedded-runner/run.ts` (`runEmbeddedPiAgent`) → `src/agents/pi-embedded-runner/run/attempt.ts` (`runEmbeddedAttempt`) → `src/agents/pi-embedded-subscribe.ts` (handles response streaming and tool result delivery).
- **Core Library**: Uses `createAgentSession` from `@mariozechner/pi-coding-agent`.

### Channels
Channels are the abstraction layer for messaging platforms.
- **Interface**: Defined in `src/channels/plugins/types.adapters.ts` (e.g., `ChannelMessagingAdapter`, `ChannelGatewayAdapter`).
- **Flow**: `Inbound Message` → `Channel Adapter` → `Gateway` → `Routing` → `Agent` → `Outbound Reply` → `Channel Adapter`.

### Routing
Routing determines which agent/session should handle an incoming message based on the source channel, sender ID, and thread ID.
- **Key Files**: `src/routing/resolve-route.ts`, `src/routing/session-key.ts`.

---

## 3. Core Concepts Glossary

### Gateway
- **What it is**: The central server process that manages channel connections and provides an RPC API.
- **Key Files**: `src/gateway/server.ts`, `src/gateway/server.impl.ts`.
- **Key Types**: `GatewayServer`, `GatewayClient`.
- **Relates to**: Channels (managed by Gateway), CLI (connects to Gateway).

### Agent / Session
- **What it is**: An active conversation state and the AI loop that processes it. A Session is the persistence layer for an Agent's history.
- **Key Files**: `src/agents/pi-embedded-runner/run.ts`, `src/sessions/session-store.ts`.
- **Key Types**: `AgentSession`, `EmbeddedPiRunResult`.
- **Relates to**: Routing (maps messages to sessions).

### Channel
- **What it is**: An abstraction for a messaging platform (e.g., Telegram).
- **Key Files**: `src/channels/registry.ts`, `src/channels/plugins/types.ts`.
- **Key Types**: `ChannelPlugin`, `ChannelMessagingAdapter`.
- **Relates to**: Gateway (hosts channels).

### Channel Plugin / Extension
- **What it is**: A self-contained package (often in `extensions/`) that implements the Channel interfaces.
- **Key Files**: `extensions/`, `src/plugins/`.
- **Relates to**: Plugin SDK (used to implement plugins).

### Plugin SDK
- **What it is**: The public API and type definitions for building OpenClaw extensions.
- **Key Files**: `src/plugin-sdk/index.ts`, `packages/plugin-sdk/`.
- **Key Types**: `OpenClawPluginApi`, `ChannelPlugin`.

### Plugin Manifest
- **What it is**: A JSON file describing an extension's metadata, requirements, and config schema.
- **Key Files**: `openclaw.plugin.json`.

### Skills
- **What it is**: Reusable tools, prompts, or data that extend an agent's capabilities.
- **Key Files**: `src/plugins/skills.ts`, `src/agents/skills/`.
- **Relates to**: Agent (uses skills in its toolset).

### Memory
- **What it is**: Long-term storage for conversation history and indexed documents, utilizing vector embeddings.
- **Key Files**: `src/memory/sqlite.ts`, `src/memory/sqlite-vec.ts`.
- **Relates to**: Agent (uses memory-search tool).

### Cron / Heartbeat
- **What it is**: A system for scheduling recurring tasks (Cron) and periodic agent "thoughts" (Heartbeat).
- **Key Files**: `src/cron/service.ts`, `src/cron/isolated-agent.ts`.
- **Relates to**: Agent (runs turns on schedule).

### Hooks
- **What it is**: Lifecycle events that allow plugins to intercept or modify system behavior (e.g., `before_tool_call`).
- **Key Files**: `src/hooks/`, `src/plugins/hook-runner-global.ts`.

### Config
- **What it is**: The centralized configuration system based on Zod schemas and JSON5 files.
- **Key Files**: `src/config/config.ts`, `src/config/zod-schema.ts`.
- **Key Types**: `OpenClawConfig`.

### Auto-Reply
- **What it is**: The pipeline that automatically generates and formats agent responses to inbound messages.
- **Key Files**: `src/auto-reply/pipeline.ts`, `src/auto-reply/reply/reply-directives.ts`.

### Routing / Session Keys
- **What it is**: The logic that uniquely identifies a conversation thread across different channels.
- **Key Files**: `src/routing/session-key.ts`, `src/routing/resolve-route.ts`.

### Tools
- **What it is**: Capabilities provided to the agent (bash execution, file operations, web browsing).
- **Key Files**: `src/agents/tools/`, `src/agents/pi-embedded-runner/tool-split.ts`.

### System Prompt
- **What it is**: The base instructions and persona definitions sent to the LLM.
- **Key Files**: `src/agents/system-prompt.ts`.

### Subagents
- **What it is**: The ability for an agent to spawn "child" agent sessions to handle subtasks.
- **Key Files**: `src/agents/subagent-spawn.ts`, `src/agents/subagent-registry.ts`.

### Providers / Auth Profiles
- **What it is**: Abstractions for LLM providers (OpenAI, Anthropic) and their credential management.
- **Key Files**: `src/providers/`, `src/agents/auth-profiles.ts`.

### Sandbox
- **What it is**: Isolated environments (Docker/Podman) for executing agent-generated code safely.
- **Key Files**: `src/agents/pi-embedded-runner/sandbox-info.ts`, `src/agents/sandbox/`.

### TUI
- **What it is**: The Terminal User Interface for interacting with the gateway.
- **Key Files**: `src/tui/`.

### Pairing
- **What it is**: Secure mechanism for linking new devices or authorizing messaging targets.
- **Key Files**: `src/pairing/`.

### Commands
- **What it is**: The implementation of various `openclaw <cmd>` subcommands.
- **Key Files**: `src/commands/`.

---

## 4. Extension Model Deep Dive

OpenClaw's extension model is designed for loose coupling. Most significant features (like new messaging channels) are implemented as extensions.

### Extension Structure
A typical extension (e.g., `extensions/signal`) contains:
- `openclaw.plugin.json`: Metadata and config schema.
- `index.ts`: The entry point that registers the extension with the system.
- `src/`: Implementation logic (adapters, monitors, etc.).

### Adapter Interface Hierarchy
A Channel Extension implements the `ChannelPlugin` interface, which includes several specialized adapters:
1.  **`ChannelConfigAdapter`**: (Required) Handles account listing, resolution, and validation.
2.  **`ChannelMessagingAdapter`**: (Required for messaging) Handles target normalization and outbound delivery.
3.  **`ChannelGatewayAdapter`**: (Required for Gateway) Handles the lifecycle of the connection (start/stop) and message monitoring.
4.  **`ChannelStatusAdapter`**: (Recommended) Provides health checks and diagnostics.
5.  **`ChannelPairingAdapter`**: (Optional) Handles secure device/target pairing flows.

### Walkthrough: Signal Extension
The Signal extension (`extensions/signal`) uses `signal-cli` or a REST API to bridge Signal messages:
1.  **Registration**: `index.ts` calls `api.registerChannel({ plugin: signalPlugin })`.
2.  **Gateway Integration**: `signalPlugin.gateway.startAccount` invokes a monitor that listens for new messages.
3.  **Message Flow**: When a message arrives, the monitor calls the Gateway's inbound message handler, which triggers the routing and agent pipeline.
4.  **Outbound**: When the agent replies, `signalPlugin.outbound.sendText` is called to deliver the message via the Signal bridge.

---

## 5. Message Lifecycle (End-to-End Trace)

1.  **Arrival**: A message is received by a channel monitor (e.g., `src/channels/whatsapp/monitor.ts`).
2.  **Ingestion**: The monitor calls `gateway.handleInboundMessage()` (in `src/gateway/server.impl.ts`).
3.  **Routing**: The gateway uses `src/routing/resolve-route.ts` to find the correct `sessionKey`.
4.  **Pipeline**: The `src/auto-reply/pipeline.ts` is invoked.
5.  **Agent Execution**: `runEmbeddedPiAgent` (in `src/agents/pi-embedded-runner/run.ts`) starts the reasoning loop.
6.  **LLM Call**: The runner calls the configured LLM provider (e.g., Anthropic) via `src/agents/pi-embedded-runner/run/attempt.ts`.
7.  **Tool Execution**: If the LLM requests a tool (e.g., `bash`), the runner executes it and feeds the output back.
8.  **Response Delivery**: Once finished, the response is streamed/chunked and sent back to the channel via the channel's `outbound.sendText` adapter.

---

## 6. Configuration System

- **Storage**: Config is stored in `~/.openclaw/config.json5`.
- **Schema**: Defined using Zod in `src/config/zod-schema.ts` and split into logical modules (e.g., `zod-schema.agents.js`).
- **Loading**: `src/config/config.ts` (`loadConfig`) loads, parses, and validates the config against the schema.
- **Multi-Agent**: Agent-specific configurations are stored in the `agents.list` array within the main config.
- **Environment Variables**: Variables prefixed with `OPENCLAW_` can override config values (handled in `src/infra/env.ts`).

---

## 7. Memory System

- **Architecture**: Uses SQLite as the primary store. Vector embeddings are stored and searched using the `sqlite-vec` extension.
- **Indexing**: Conversation turns and external documents are chunked and embedded via an embedding provider (OpenAI, Gemini, etc.).
- **Integration**: The `memory-search` tool (in `src/agents/memory-search.ts`) allows agents to query their long-term memory during a conversation.
- **Extension Points**: Core memory logic is in `src/memory/`, but backends can be swapped via extensions like `extensions/memory-lancedb`.

---

## 8. Cron / Heartbeat System

- **Scheduling**: The Cron service (`src/cron/service.ts`) uses the `croner` library to manage scheduled tasks.
- **Isolated Agent**: To avoid state pollution, cron jobs often run in an "isolated agent" mode (`src/cron/isolated-agent.ts`), which creates a transient agent session just for the task.
- **Heartbeat**: The heartbeat mechanism triggers periodic agent turns, allowing agents to proactively send messages or perform maintenance.

---

## 9. Key Patterns & Conventions

- **Dependency Injection**: The system uses a `createDefaultDeps` pattern (found in many entry points) to inject services into handlers, facilitating testing.
- **Zod + TypeBox**: Zod is used for primary config validation, while TypeBox is often used for high-performance schema validation in messaging and RPC layers.
- **Co-located Tests**: Unit tests (`*.test.ts`) and E2E tests (`*.e2e.test.ts`) are typically located next to the source files they test.
- **Lanes**: The "lanes" concept (`src/agents/lanes.ts`) is used to serialize agent turns, ensuring that only one turn runs at a time for a given session or global scope.

---

## 10. File Index

### Subdirectory Purposes
- `src/acp/` — Agent Control Protocol implementation.
- `src/agents/` — AI agent reasoning loop, tools, and session management.
- `src/auto-reply/` — Logic for generating and formatting automatic responses (thinking, reply directives).
- `src/channels/` — Channel abstraction layer and platform registries.
- `src/cli/` — Commander-based CLI command registrations.
- `src/config/` — Configuration schema, loading, and validation.
- `src/gateway/` — WebSocket server and RPC message handlers.
- `src/memory/` — SQLite and vector search implementation.
- `src/providers/` — LLM provider integrations (OpenAI, Anthropic, etc.).
- `src/routing/` — Message routing and session key resolution (continuity).
- `src/sessions/` — Persistence layer for conversation histories.

### Key Files (Architectural Importance)
- `src/entry.ts`: Main CLI entry point.
- `src/gateway/server.ts`: The heart of the long-running gateway process.
- `src/agents/pi-embedded-runner/run.ts`: The primary AI reasoning loop.
- `src/agents/pi-embedded-runner/run/attempt.ts`: Orchestrates a single LLM turn attempt.
- `src/agents/pi-embedded-subscribe.ts`: Subscribes to agent events and handles streaming/tool output.
- `src/channels/plugins/types.ts`: The "contract" for all messaging integrations.
- `src/config/zod-schema.ts`: The source of truth for all system settings.
- `src/routing/resolve-route.ts`: Determines message-to-session mapping.
- `src/auto-reply/pipeline.ts`: Orchestrates the response generation flow.
- `src/sessions/session-store.ts`: Manages conversation history persistence.
- `src/plugins/runtime.ts`: The plugin loader and registry.
- `src/agents/system-prompt.ts`: Defines how the agent's brain is initialized.
