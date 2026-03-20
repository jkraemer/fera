import json
import pytest
from pathlib import Path
from fera.gateway.mcp import GatewayMcpManager


@pytest.fixture
def mgr():
    return GatewayMcpManager()


# --- list_servers ---

def test_list_servers_empty_when_no_config(tmp_path, mgr):
    result = mgr.list_servers(tmp_path, agent_names=["main"])
    assert result == []


def test_list_servers_returns_global_servers(tmp_path, mgr):
    cfg = {"mcp_servers": {"brave": {"type": "sse", "url": "https://brave.example.com/sse"}}}
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    result = mgr.list_servers(tmp_path, agent_names=[])
    assert len(result) == 1
    assert result[0] == {"scope": "global", "name": "brave", "config": cfg["mcp_servers"]["brave"]}


def test_list_servers_returns_agent_servers(tmp_path, mgr):
    agent_dir = tmp_path / "agents" / "main"
    agent_dir.mkdir(parents=True)
    cfg = {"mcp_servers": {"my_tool": {"type": "sse", "url": "https://tool.example.com/sse"}}}
    (agent_dir / "config.json").write_text(json.dumps(cfg))
    result = mgr.list_servers(tmp_path, agent_names=["main"])
    assert len(result) == 1
    assert result[0] == {"scope": "main", "name": "my_tool", "config": cfg["mcp_servers"]["my_tool"]}


def test_list_servers_returns_both_scopes(tmp_path, mgr):
    (tmp_path / "config.json").write_text(json.dumps({"mcp_servers": {"global_srv": {"type": "sse", "url": "https://g.example.com"}}}))
    agent_dir = tmp_path / "agents" / "main"
    agent_dir.mkdir(parents=True)
    (agent_dir / "config.json").write_text(json.dumps({"mcp_servers": {"agent_srv": {"type": "sse", "url": "https://a.example.com"}}}))
    result = mgr.list_servers(tmp_path, agent_names=["main"])
    scopes = {r["scope"] for r in result}
    assert scopes == {"global", "main"}
