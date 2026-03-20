# Fera Design: Cron & Task Routing

This document describes how Fera routes and executes scheduled tasks, differentiating between context-aware session jobs and stateless ephemeral workers.

---

## 1. Routing Modes

Fera supports two execution modes for cron jobs, determined by the presence or absence of the `session` field in the job definition (`cron.json`).

### A. Session Jobs (`session` present)
*   **Behavior:** The task runs inside a named persistent session with full conversation history.
*   **Logic:** `execute_job()` delegates to `AgentRunner.run_turn()`, which creates or resumes the named session. Events are published to the EventBus.
*   **Session naming:** Bare session names are qualified with the agent name (e.g., `session: "daily"` with `agent: "main"` becomes `main/daily`).
*   **Transcripts:** Conversation history is persisted to `workspaces/<agent>/workspace/transcripts/<session>.jsonl`.
*   **Prompting:** Defaults to `full` system prompt mode.
*   **Use Case:** Reminders, status updates, or anything requiring the agent to remember prior conversations.

### B. Ephemeral Jobs (no `session` field)
*   **Behavior:** Fera runs a stateless one-shot query with no persistent session.
*   **Logic:** `execute_job()` delegates to `AgentRunner.run_oneshot()`, which calls the SDK's `query()` function directly. No session is created, no events are published to the EventBus.
*   **Output:** Text chunks from `agent.text` events are collected and returned in the result dict.
*   **Prompting:** Defaults to `minimal` system prompt mode.
*   **Use Case:** Data scraping, log analysis, or other tasks that don't need conversational context.

---

## 2. The Execution Workflow

1.  **Trigger:** The OS `crontab` executes `fera-run-job <job_name>`.
2.  **Local validation:** The CLI loads `cron.json` and verifies the job exists before contacting the gateway.
3.  **Connection:** The CLI connects to the gateway WebSocket, authenticates with the configured token, and sends a `cron.run` request.
4.  **Resolution:** The gateway's `_handle_cron_run()` loads the job spec from `cron.json`.
5.  **Routing:** `execute_job()` checks for the `session` field:
    *   **Session:** Calls `_execute_session_job()` → `AgentRunner.run_turn()`. Events are published to the EventBus.
    *   **Ephemeral:** Calls `_execute_ephemeral_job()` → `AgentRunner.run_oneshot()`. Text output is collected in memory.
6.  **Response:** The gateway returns the result to the CLI over the WebSocket.

---

## 3. Job Configuration

Jobs are defined in `$FERA_HOME/cron.json`:

```json
{
  "jobs": {
    "morning-digest": {
      "agent": "main",
      "payload": "Check Redmine and summarize my open tickets.",
      "prompt_mode": "minimal",
      "model": "opus",
      "allowed_tools": ["WebSearch", "WebFetch"]
    },
    "medication-reminder": {
      "agent": "main",
      "session": "daily",
      "payload": "Remind the user to take their vitamins.",
      "prompt_mode": "full"
    }
  }
}
```

### Fields

| Field | Required | Default | Description |
|---|---|---|---|
| `payload` | yes | — | The prompt text sent to the agent |
| `agent` | no | `"main"` | Which agent configuration to use |
| `session` | no | *(absent)* | Named session for persistent context. Absence triggers ephemeral mode |
| `prompt_mode` | no | `"full"` (session) / `"minimal"` (ephemeral) | System prompt verbosity |
| `model` | no | agent default | Model override for this job |
| `allowed_tools` | no | agent default | Whitelist of tools available to the job |
