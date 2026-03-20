# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is Fera

A personal AI agent built on the Claude Agent SDK (Python). It runs on a dedicated Debian stable machine. See README.md for the full architecture vision (I/O channels, heartbeat, MCP integrations).

## Commands

```bash
uv sync              # install dependencies
uv run pytest        # run all tests
uv run pytest tests/test_agent.py::test_name  # run a single test
```

### Dev container (Podman)

```bash
make dev             # build image + start container with source mounted
make shell           # exec into running container
make test            # run pytest inside container
make down            # stop container
make build           # build production image
```

The dev container uses a named volume for `.venv/` to avoid clobbering the host's virtual environment.

## Architecture

- **src layout**: `src/fera/` — installed as the `fera` package
- **Agent config**: `fera.agent` — constants (SYSTEM_PROMPT, MCP_SERVERS, ALLOWED_TOOLS) and `ensure_memory_dir()`
- **Auth**: Claude Code CLI auth (Max subscription or API key via `claude login`), not env vars
- **Build backend**: hatchling
- **Python**: 3.11 (pinned in `.python-version` to match Debian stable)

## Agent SDK usage

The SDK spawns Claude Code as a subprocess. Key imports:

```python
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
```

Configure via `ClaudeAgentOptions` (not plain dicts). The gateway uses `ClaudeSDKClient` for conversation management.

Cannot run from inside a Claude Code session (nesting protection). Test from an external terminal.

## Reference codebase

[OpenClaw](../openclaw) is the primary inspiration for Fera. `docs/openclaw-reference-guide.md` documents its architecture and patterns. Consult it when implementing new features — many of Fera's components will be modeled after OpenClaw's approach.

## Memory

When using the recollect persistent memory tools, use `project="fera"` for all entries related to this project.

When asked to access redmine in the context of this project, also use 'fera' as the project identifier.

## Git

when running git commands, do not use `git -C ...`. If in doubt about your
currend work dir, use `pwd` to find out, then `cd` if necessary.

When cleaning up a worktree, ALWAYS remove the worktree first, then delete
the branch — in that order:
```bash
git worktree remove <path>   # first
git branch -D <branch>       # second
```
