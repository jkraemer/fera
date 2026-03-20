from __future__ import annotations

from pathlib import Path

from fera.config import load_json_config


class GatewayMcpManager:
    """Handles config file reads for MCP server discovery."""

    def list_servers(self, fera_home: Path, agent_names: list[str]) -> list[dict]:
        """Return all configured MCP servers across global and agent scopes."""
        servers = []

        config_path = fera_home / "config.json"
        if config_path.exists():
            data = load_json_config(config_path)
            for name, cfg in data.get("mcp_servers", {}).items():
                servers.append({"scope": "global", "name": name, "config": cfg})

        for agent_name in agent_names:
            agent_cfg_path = fera_home / "agents" / agent_name / "config.json"
            if agent_cfg_path.exists():
                data = load_json_config(agent_cfg_path)
                for name, cfg in data.get("mcp_servers", {}).items():
                    servers.append({"scope": agent_name, "name": name, "config": cfg})

        return servers
