import asyncio
import json
import re

import fera
from fera.agent import (
    ALLOWED_TOOLS,
    DEFAULT_DISABLED_TOOLS,
    agent_plugins,
    build_allowed_tools,
    build_continue_hook,
    build_mcp_servers,
    build_tool_deny_hook,
    ensure_dirs,
    extra_mcp_servers,
    mcp_servers,
    merge_hooks,
)


def test_module_loads():
    assert hasattr(fera, "__version__")


def test_mcp_servers_configured():
    servers = mcp_servers("http://localhost:8390/sse", "main")
    assert "memory" in servers
    assert servers["memory"]["type"] == "sse"
    assert "agent=main" in servers["memory"]["url"]
    assert "http://localhost:8390/sse" in servers["memory"]["url"]


def test_allowed_tools_configured():
    assert "mcp__memory__memory_search" in ALLOWED_TOOLS
    assert "mcp__memory__memory_get" in ALLOWED_TOOLS


def test_ensure_dirs_creates_structure(tmp_path):
    ensure_dirs("research", tmp_path)
    ws = tmp_path / "agents" / "research" / "workspace"
    assert ws.is_dir()
    assert (ws / "memory").is_dir()
    assert (ws / "persona").is_dir()
    assert (tmp_path / "agents" / "research" / "data").is_dir()


# --- build_mcp_servers ---

def test_build_mcp_servers_without_extra_has_only_memory():
    servers = build_mcp_servers("http://localhost:8390/sse", "main")
    assert list(servers.keys()) == ["memory"]


def test_build_mcp_servers_with_extra_includes_extra_servers():
    extra = {"my_tool": {"type": "sse", "url": "https://example.com/sse"}}
    servers = build_mcp_servers("http://localhost:8390/sse", "main", extra=extra)
    assert "memory" in servers
    assert "my_tool" in servers
    assert servers["my_tool"]["url"] == "https://example.com/sse"


def test_build_mcp_servers_extra_does_not_mutate_input():
    extra = {"my_tool": {"type": "sse", "url": "https://example.com/sse"}}
    original = dict(extra)
    build_mcp_servers("http://localhost:8390/sse", "main", extra=extra)
    assert extra == original


# --- build_allowed_tools ---

def test_build_allowed_tools_without_extra_matches_builtin():
    tools = build_allowed_tools()
    assert tools == ALLOWED_TOOLS


def test_build_allowed_tools_does_not_return_same_list_object():
    # Callers must not accidentally mutate ALLOWED_TOOLS
    assert build_allowed_tools() is not ALLOWED_TOOLS


def test_build_allowed_tools_adds_wildcard_for_extra_server():
    extra = {"my_tool": {"type": "sse", "url": "https://example.com/sse"}}
    tools = build_allowed_tools(extra_servers=extra)
    assert "mcp__my_tool__*" in tools


def test_build_allowed_tools_retains_builtin_tools_with_extra():
    extra = {"my_tool": {"type": "sse", "url": "https://example.com/sse"}}
    tools = build_allowed_tools(extra_servers=extra)
    assert "mcp__memory__memory_search" in tools
    assert "mcp__memory__memory_get" in tools


def test_build_allowed_tools_adds_wildcard_per_extra_server():
    extra = {
        "tool_a": {"type": "sse", "url": "https://a.com/sse"},
        "tool_b": {"type": "sse", "url": "https://b.com/sse"},
    }
    tools = build_allowed_tools(extra_servers=extra)
    assert "mcp__tool_a__*" in tools
    assert "mcp__tool_b__*" in tools


# --- extra_mcp_servers ---

def test_extra_mcp_servers_merges_global_and_agent(tmp_path, monkeypatch):
    agent_cfg = {"mcp_servers": {"agent_tool": {"type": "sse", "url": "https://a.com"}}}
    cfg_path = tmp_path / "agents" / "main" / "config.json"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(json.dumps(agent_cfg))

    monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
    global_servers = {"global_tool": {"type": "sse", "url": "https://g.com"}}
    result = extra_mcp_servers("main", global_servers)
    assert "global_tool" in result
    assert "agent_tool" in result


def test_extra_mcp_servers_agent_overrides_global(tmp_path, monkeypatch):
    agent_cfg = {"mcp_servers": {"shared": {"type": "sse", "url": "https://agent.com"}}}
    cfg_path = tmp_path / "agents" / "main" / "config.json"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(json.dumps(agent_cfg))

    monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
    global_servers = {"shared": {"type": "sse", "url": "https://global.com"}}
    result = extra_mcp_servers("main", global_servers)
    assert result["shared"]["url"] == "https://agent.com"


def test_extra_mcp_servers_without_agent_config(tmp_path, monkeypatch):
    monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
    global_servers = {"global_tool": {"type": "sse", "url": "https://g.com"}}
    result = extra_mcp_servers("main", global_servers)
    assert result == global_servers


def test_extra_mcp_servers_substitutes_env_vars(tmp_path, monkeypatch):
    agent_cfg = {"mcp_servers": {"tool": {"type": "sse", "url": "${MY_URL}"}}}
    cfg_path = tmp_path / "agents" / "main" / "config.json"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(json.dumps(agent_cfg))

    monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
    monkeypatch.setenv("MY_URL", "https://resolved.com")
    result = extra_mcp_servers("main", {})
    assert result["tool"]["url"] == "https://resolved.com"


def test_ensure_dirs_is_idempotent(tmp_path):
    ensure_dirs("main", tmp_path)
    ensure_dirs("main", tmp_path)  # should not raise
    assert (tmp_path / "agents" / "main" / "workspace").is_dir()


# --- agent_plugins ---

def test_agent_plugins_returns_empty_when_no_config(tmp_path, monkeypatch):
    monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
    ws = tmp_path / "agents" / "main" / "workspace"
    result = agent_plugins("main", ws)
    assert result == []


def test_agent_plugins_returns_empty_when_no_plugins_key(tmp_path, monkeypatch):
    monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
    cfg_path = tmp_path / "agents" / "main" / "config.json"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text('{"mcp_servers": {}}')
    ws = tmp_path / "agents" / "main" / "workspace"
    result = agent_plugins("main", ws)
    assert result == []


def test_agent_plugins_resolves_relative_path(tmp_path, monkeypatch):
    monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
    cfg_path = tmp_path / "agents" / "main" / "config.json"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text('{"plugins": [{"type": "local", "path": "plugins/superpowers"}]}')
    ws = tmp_path / "agents" / "main" / "workspace"
    result = agent_plugins("main", ws)
    assert result == [{"type": "local", "path": str(ws / "plugins" / "superpowers")}]


def test_agent_plugins_leaves_absolute_path_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
    cfg_path = tmp_path / "agents" / "main" / "config.json"
    cfg_path.parent.mkdir(parents=True)
    abs_path = "/opt/plugins/superpowers"
    cfg_path.write_text(f'{{"plugins": [{{"type": "local", "path": "{abs_path}"}}]}}')
    ws = tmp_path / "agents" / "main" / "workspace"
    result = agent_plugins("main", ws)
    assert result == [{"type": "local", "path": abs_path}]


def test_agent_plugins_multiple_plugins(tmp_path, monkeypatch):
    monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
    cfg_path = tmp_path / "agents" / "main" / "config.json"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(
        '{"plugins": ['
        '{"type": "local", "path": "plugins/a"},'
        '{"type": "local", "path": "plugins/b"}'
        ']}'
    )
    ws = tmp_path / "agents" / "main" / "workspace"
    result = agent_plugins("main", ws)
    assert len(result) == 2
    assert result[0]["path"] == str(ws / "plugins" / "a")
    assert result[1]["path"] == str(ws / "plugins" / "b")


def test_build_allowed_tools_with_agent_allowed_replaces_all():
    agent_allowed = ["Read", "Glob", "mcp__memory__*"]
    tools = build_allowed_tools(
        extra_servers={"brave": {"type": "sse", "url": "https://brave.com"}},
        agent_allowed=agent_allowed,
    )
    assert tools == ["Read", "Glob", "mcp__memory__*"]


def test_build_allowed_tools_agent_allowed_none_is_default_behavior():
    extra = {"brave": {"type": "sse", "url": "https://brave.com"}}
    tools = build_allowed_tools(extra_servers=extra, agent_allowed=None)
    assert "mcp__memory__memory_search" in tools
    assert "mcp__brave__*" in tools


def test_build_allowed_tools_agent_allowed_empty_list_means_no_tools():
    tools = build_allowed_tools(agent_allowed=[])
    assert tools == []


# --- disabled_tools ---

def test_build_allowed_tools_disabled_removes_from_default():
    tools = build_allowed_tools(agent_disabled=["mcp__memory__memory_get"])
    assert "mcp__memory__memory_search" in tools
    assert "mcp__memory__memory_get" not in tools


def test_build_allowed_tools_disabled_removes_from_agent_allowed():
    agent_allowed = ["Read", "Glob", "AskUserQuestion", "mcp__memory__*"]
    tools = build_allowed_tools(
        agent_allowed=agent_allowed,
        agent_disabled=["AskUserQuestion"],
    )
    assert tools == ["Read", "Glob", "mcp__memory__*"]


def test_build_allowed_tools_disabled_removes_from_auto_generated():
    extra = {"redmine": {"type": "sse", "url": "https://r.com"}}
    tools = build_allowed_tools(
        extra_servers=extra,
        agent_disabled=["mcp__redmine__*"],
    )
    assert "mcp__memory__memory_search" in tools
    assert "mcp__redmine__*" not in tools


def test_build_allowed_tools_disabled_none_is_noop():
    tools = build_allowed_tools(agent_disabled=None)
    assert tools == ALLOWED_TOOLS


def test_build_allowed_tools_disabled_empty_list_is_noop():
    tools = build_allowed_tools(agent_disabled=[])
    assert tools == ALLOWED_TOOLS


def test_build_allowed_tools_disabled_nonexistent_tool_is_harmless():
    tools = build_allowed_tools(agent_disabled=["NonexistentTool"])
    assert tools == ALLOWED_TOOLS


# --- merge_hooks ---

def test_merge_hooks_empty():
    assert merge_hooks() == {}


def test_merge_hooks_skips_none():
    h = {"PreToolUse": ["a"]}
    assert merge_hooks(None, h, None) == {"PreToolUse": ["a"]}


def test_merge_hooks_concatenates_same_event():
    h1 = {"PreToolUse": ["a"]}
    h2 = {"PreToolUse": ["b"]}
    assert merge_hooks(h1, h2) == {"PreToolUse": ["a", "b"]}


def test_merge_hooks_keeps_different_events():
    h1 = {"PreToolUse": ["a"]}
    h2 = {"PreCompact": ["b"]}
    result = merge_hooks(h1, h2)
    assert result == {"PreToolUse": ["a"], "PreCompact": ["b"]}


# --- DEFAULT_DISABLED_TOOLS ---

def test_default_disabled_tools_is_empty():
    assert DEFAULT_DISABLED_TOOLS == []


# --- build_continue_hook ---

def test_build_continue_hook_returns_pre_tool_use():
    result = build_continue_hook()
    assert "PreToolUse" in result
    matchers = result["PreToolUse"]
    assert len(matchers) == 1
    assert matchers[0].matcher is None


def test_build_continue_hook_callback_returns_continue():
    result = build_continue_hook()
    callback = result["PreToolUse"][0].hooks[0]
    output = asyncio.run(callback({"hook_event_name": "PreToolUse"}, None, None))
    assert output == {"continue_": True}


# --- build_tool_deny_hook ---


def test_build_tool_deny_hook_returns_none_when_nothing_to_deny():
    result = build_tool_deny_hook()
    assert result is None


def test_build_tool_deny_hook_returns_pre_tool_use_matcher_with_agent_disabled():
    result = build_tool_deny_hook(agent_disabled=["EnterPlanMode"])
    assert result is not None
    assert "PreToolUse" in result
    matchers = result["PreToolUse"]
    assert len(matchers) == 1


def test_build_tool_deny_hook_matcher_matches_agent_disabled():
    result = build_tool_deny_hook(agent_disabled=["EnterPlanMode"])
    matcher = result["PreToolUse"][0]
    pattern = re.compile(matcher.matcher)
    assert pattern.search("EnterPlanMode")


def test_build_tool_deny_hook_no_duplicates():
    result = build_tool_deny_hook(agent_disabled=["AskUserQuestion"])
    matcher = result["PreToolUse"][0]
    # Should only appear once in the pattern
    assert matcher.matcher.count("AskUserQuestion") == 1


def test_build_tool_deny_hook_callback_denies():
    result = build_tool_deny_hook(agent_disabled=["AskUserQuestion"])
    callback = result["PreToolUse"][0].hooks[0]
    input_data = {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion"}
    output = asyncio.run(callback(input_data, None, None))
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_build_tool_deny_hook_escapes_special_chars():
    result = build_tool_deny_hook(agent_disabled=["mcp__foo__*"])
    matcher = result["PreToolUse"][0]
    pattern = re.compile(matcher.matcher)
    # The * should be escaped, not treated as regex wildcard
    assert pattern.search("mcp__foo__*")
    assert not pattern.search("mcp__foo__bar")
