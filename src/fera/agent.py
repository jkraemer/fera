from __future__ import annotations

from pathlib import Path

from fera.config import data_dir as _data_dir, workspace_dir as _workspace_dir


def mcp_servers(memory_url: str, agent: str) -> dict:
    return {
        "memory": {
            "type": "sse",
            "url": f"{memory_url}?agent={agent}",
        }
    }


ALLOWED_TOOLS = [
    "mcp__memory__memory_search",
    "mcp__memory__memory_get",
]

DEFAULT_DISABLED_TOOLS = []


def extra_mcp_servers(agent_name: str, global_servers: dict) -> dict:
    """Merge global and per-agent MCP servers, applying env var substitution."""
    from fera.config import load_agent_config, substitute_env_vars

    agent_cfg = load_agent_config(agent_name)
    agent_servers = agent_cfg.get("mcp_servers", {})
    return substitute_env_vars({**global_servers, **agent_servers})


def build_mcp_servers(
    memory_url: str,
    agent_name: str,
    extra: dict | None = None,
) -> dict:
    """Build the full MCP server dict: built-in memory server plus any extras."""
    servers = mcp_servers(memory_url, agent_name)
    if extra:
        servers.update(extra)
    return servers


def build_allowed_tools(
    extra_servers: dict | None = None,
    agent_allowed: list[str] | None = None,
    agent_disabled: list[str] | None = None,
) -> list[str]:
    """Build the allowed tools list.

    If agent_allowed is provided, use it (explicit override).
    Otherwise, compute the default: built-in tools plus wildcards for extra servers.
    If agent_disabled is provided, remove those tools from the final list.
    """
    if agent_allowed is not None:
        tools = list(agent_allowed)
    else:
        tools = list(ALLOWED_TOOLS)
        if extra_servers:
            for name in extra_servers:
                tools.append(f"mcp__{name}__*")
    if agent_disabled:
        disabled = set(agent_disabled)
        tools = [t for t in tools if t not in disabled]
    return tools


def merge_hooks(*hook_dicts: dict | None) -> dict:
    """Merge multiple hook dicts, concatenating matcher lists per event type."""
    merged: dict = {}
    for d in hook_dicts:
        if not d:
            continue
        for event, matchers in d.items():
            merged.setdefault(event, []).extend(matchers)
    return merged


def build_continue_hook() -> dict:
    """Build a PreToolUse hook that keeps the stream open for can_use_tool.

    The Python SDK requires at least one PreToolUse hook that returns
    ``{"continue_": True}`` to keep the stream open while the
    ``can_use_tool`` callback awaits user input.
    """
    from claude_agent_sdk import HookMatcher

    async def _continue(input_data, _tool_use_id, _context):
        return {"continue_": True}

    return {"PreToolUse": [HookMatcher(matcher=None, hooks=[_continue])]}


def build_tool_deny_hook(
    agent_disabled: list[str] | None = None,
) -> dict | None:
    """Build a PreToolUse hook dict that denies disabled tools.

    Merges DEFAULT_DISABLED_TOOLS with any agent-specific disabled tools.
    Returns a hooks dict fragment (``{"PreToolUse": [...]}``) suitable for
    merging into ``ClaudeAgentOptions.hooks``, or ``None`` if there is
    nothing to deny.
    """
    import re

    from claude_agent_sdk import HookMatcher

    tools = list(DEFAULT_DISABLED_TOOLS)
    if agent_disabled:
        tools.extend(t for t in agent_disabled if t not in DEFAULT_DISABLED_TOOLS)
    if not tools:
        return None

    matcher = "|".join(re.escape(t) for t in tools)

    async def _deny(input_data, _tool_use_id, _context):
        return {
            "hookSpecificOutput": {
                "hookEventName": input_data["hook_event_name"],
                "permissionDecision": "deny",
                "permissionDecisionReason": "Tool disabled by configuration",
            }
        }

    return {"PreToolUse": [HookMatcher(matcher=matcher, hooks=[_deny])]}


def agent_plugins(agent_name: str, workspace: Path) -> list[dict]:
    """Load plugin list from per-agent config, resolving relative paths against workspace."""
    from fera.config import load_agent_config

    cfg = load_agent_config(agent_name)
    plugins = cfg.get("plugins", [])
    result = []
    for p in plugins:
        path = Path(p["path"])
        if not path.is_absolute():
            path = workspace / path
        result.append({"type": p["type"], "path": str(path)})
    return result


def ensure_dirs(agent_name: str, fera_home: Path) -> None:
    """Ensure workspace and data directory structure exists for the given agent."""
    ws = _workspace_dir(agent_name, fera_home)
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "memory").mkdir(exist_ok=True)
    (ws / "persona").mkdir(exist_ok=True)
    _data_dir(agent_name, fera_home).mkdir(parents=True, exist_ok=True)
