import json
import os
from pathlib import Path

import pytest

from fera.config import (
    load_config, DEFAULT_CONFIG, FERA_HOME,
    AGENTS_DIR, DEFAULT_AGENT,
    workspace_dir, data_dir,
    load_agent_config, substitute_env_vars,
    save_config, ensure_auth_token,
    load_cron_config,
    load_models, resolve_model,
)


def test_fera_home_defaults_to_user_home(monkeypatch):
    monkeypatch.delenv("FERA_HOME", raising=False)
    # Re-evaluate by importing the function that reads the env
    from fera.config import _resolve_fera_home
    assert _resolve_fera_home() == Path.home()


def test_fera_home_overridden_by_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("FERA_HOME", str(tmp_path))
    from fera.config import _resolve_fera_home
    assert _resolve_fera_home() == tmp_path


def test_derived_paths():
    assert DEFAULT_AGENT == "main"
    assert AGENTS_DIR == FERA_HOME / "agents"


def test_workspace_dir_returns_path_for_agent(tmp_path):
    assert workspace_dir("research", tmp_path) == tmp_path / "agents" / "research" / "workspace"


def test_data_dir_returns_path_for_agent(tmp_path):
    assert data_dir("research", tmp_path) == tmp_path / "agents" / "research" / "data"


def test_data_dir_defaults_to_fera_home():
    assert data_dir("main") == FERA_HOME / "agents" / "main" / "data"


def test_default_config():
    assert DEFAULT_CONFIG["gateway"]["host"] == "127.0.0.1"
    assert DEFAULT_CONFIG["gateway"]["port"] == 8389


def test_load_config_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
    config = load_config()
    assert config["gateway"]["port"] == 8389


def test_load_config_from_file(tmp_path, monkeypatch):
    monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"gateway": {"port": 9999}}))
    config = load_config()
    assert config["gateway"]["port"] == 9999


def test_load_config_partial_override(tmp_path, monkeypatch):
    monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"gateway": {"port": 7777}}))
    config = load_config()
    # host should still be default
    assert config["gateway"]["host"] == "127.0.0.1"
    assert config["gateway"]["port"] == 7777


def test_pool_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
    config = load_config()
    pool = config["gateway"]["pool"]
    assert pool["max_clients"] == 5
    assert pool["idle_timeout_minutes"] == 30


def test_pool_partial_override(monkeypatch, tmp_path):
    monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"gateway": {"pool": {"max_clients": 3}}}))
    config = load_config()
    pool = config["gateway"]["pool"]
    assert pool["max_clients"] == 3
    assert pool["idle_timeout_minutes"] == 30  # default preserved


def test_default_config_has_empty_mcp_servers():
    assert DEFAULT_CONFIG["mcp_servers"] == {}


def test_default_config_has_no_adapters():
    assert "adapters" not in DEFAULT_CONFIG


def test_load_config_mcp_servers_from_file(tmp_path, monkeypatch):
    monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
    server = {"type": "sse", "url": "https://example.com/sse"}
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"mcp_servers": {"my_tool": server}}))
    config = load_config()
    assert config["mcp_servers"]["my_tool"] == server


# --- load_agent_config ---

def test_load_agent_config_substitutes_env_vars_in_raw_text(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_TOKEN", "bot-token-value")
    monkeypatch.setenv("TG_USERS", "111, 222")
    agent_dir = tmp_path / "agents" / "main"
    agent_dir.mkdir(parents=True)
    (agent_dir / "config.json").write_text(
        '{"adapters": {"telegram": {"bot_token": "${TG_TOKEN}", "allowed_users": [${TG_USERS}]}}}'
    )
    result = load_agent_config("main", fera_home=tmp_path)
    assert result["adapters"]["telegram"]["bot_token"] == "bot-token-value"
    assert result["adapters"]["telegram"]["allowed_users"] == [111, 222]


def test_load_config_substitutes_env_vars_in_raw_text(tmp_path, monkeypatch):
    monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
    monkeypatch.setenv("GW_PORT", "9999")
    (tmp_path / "config.json").write_text('{"gateway": {"port": ${GW_PORT}}}')
    config = load_config()
    assert config["gateway"]["port"] == 9999


def test_load_agent_config_returns_empty_when_no_file(tmp_path):
    result = load_agent_config("main", fera_home=tmp_path)
    assert result == {}


def test_load_agent_config_reads_agent_config_file(tmp_path):
    agent_dir = tmp_path / "agents" / "main"
    agent_dir.mkdir(parents=True)
    server = {"type": "sse", "url": "https://agent.example.com/sse"}
    (agent_dir / "config.json").write_text(json.dumps({"mcp_servers": {"agent_tool": server}}))
    result = load_agent_config("main", fera_home=tmp_path)
    assert result["mcp_servers"]["agent_tool"] == server


def test_load_agent_config_uses_fera_home_default(tmp_path, monkeypatch):
    monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
    # No file — should return empty without error
    result = load_agent_config("main")
    assert result == {}


def test_load_agent_config_malformed_json_includes_path(tmp_path):
    agent_dir = tmp_path / "agents" / "main"
    agent_dir.mkdir(parents=True)
    (agent_dir / "config.json").write_text('{"broken": ,}')
    with pytest.raises(json.JSONDecodeError, match=str(agent_dir / "config.json")):
        load_agent_config("main", fera_home=tmp_path)


# --- substitute_env_vars ---

def test_substitute_env_vars_replaces_known_var(monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "secret123")
    result = substitute_env_vars({"Authorization": "Bearer ${MY_TOKEN}"})
    assert result == {"Authorization": "Bearer secret123"}


def test_substitute_env_vars_leaves_unknown_var_as_is(monkeypatch):
    monkeypatch.delenv("MISSING_VAR", raising=False)
    result = substitute_env_vars("${MISSING_VAR}")
    assert result == "${MISSING_VAR}"


def test_substitute_env_vars_recurses_into_dicts(monkeypatch):
    monkeypatch.setenv("API_KEY", "key42")
    data = {"headers": {"X-API-Key": "${API_KEY}"}, "url": "https://example.com"}
    result = substitute_env_vars(data)
    assert result["headers"]["X-API-Key"] == "key42"
    assert result["url"] == "https://example.com"


def test_substitute_env_vars_recurses_into_lists(monkeypatch):
    monkeypatch.setenv("TOKEN", "tok")
    result = substitute_env_vars(["${TOKEN}", "plain"])
    assert result == ["tok", "plain"]


def test_substitute_env_vars_leaves_non_strings_unchanged():
    result = substitute_env_vars({"port": 8080, "enabled": True})
    assert result == {"port": 8080, "enabled": True}


# --- save_config ---

def test_save_config_writes_to_disk(tmp_path, monkeypatch):
    monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
    save_config({"gateway": {"auth_token": "abc123"}})
    data = json.loads((tmp_path / "config.json").read_text())
    assert data["gateway"]["auth_token"] == "abc123"


def test_save_config_preserves_existing_keys(tmp_path, monkeypatch):
    monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"custom": "value"}))
    save_config({"gateway": {"auth_token": "tok"}})
    data = json.loads((tmp_path / "config.json").read_text())
    assert data["custom"] == "value"
    assert data["gateway"]["auth_token"] == "tok"


# --- ensure_auth_token ---

def test_ensure_auth_token_generates_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
    config = {"gateway": {"host": "127.0.0.1", "port": 8389}}
    token = ensure_auth_token(config)
    assert isinstance(token, str)
    assert len(token) == 64  # 32 bytes hex
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["gateway"]["auth_token"] == token


def test_ensure_auth_token_returns_existing():
    config = {"gateway": {"auth_token": "my-token"}}
    token = ensure_auth_token(config, save=False)
    assert token == "my-token"


def test_ensure_auth_token_resolves_env_var(monkeypatch):
    monkeypatch.setenv("FERA_TOKEN", "env-token-value")
    config = {"gateway": {"auth_token": "${FERA_TOKEN}"}}
    token = ensure_auth_token(config, save=False)
    assert token == "env-token-value"


def test_default_config_has_heartbeat_section():
    from fera.config import DEFAULT_CONFIG
    hb = DEFAULT_CONFIG["heartbeat"]
    assert hb["enabled"] is False
    assert hb["interval_minutes"] == 30
    assert hb["active_hours"] == "08:00-22:00"
    assert hb["session"] == "default"


def test_default_config_has_dream_cycle_section():
    from fera.config import DEFAULT_CONFIG
    dc = DEFAULT_CONFIG["dream_cycle"]
    assert dc["enabled"] is False
    assert dc["time"] == "03:00"
    assert dc["agents"] == []
    assert dc["model"] is None


# --- load_cron_config ---

def test_load_cron_config_returns_empty_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
    result = load_cron_config()
    assert result == {"jobs": {}}


def test_load_cron_config_reads_file(tmp_path, monkeypatch):
    monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
    (tmp_path / "cron.json").write_text(json.dumps({
        "jobs": {
            "morning-digest": {
                "payload": "Check Redmine.",
            }
        }
    }))
    result = load_cron_config()
    assert "morning-digest" in result["jobs"]
    assert result["jobs"]["morning-digest"]["payload"] == "Check Redmine."


def test_load_cron_config_applies_env_substitution(tmp_path, monkeypatch):
    monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
    monkeypatch.setenv("MY_PAYLOAD", "Do the thing.")
    (tmp_path / "cron.json").write_text('{"jobs": {"job1": {"payload": "${MY_PAYLOAD}", "session": "default"}}}')
    result = load_cron_config()
    assert result["jobs"]["job1"]["payload"] == "Do the thing."


def test_load_cron_config_applies_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
    (tmp_path / "cron.json").write_text(json.dumps({
        "jobs": {
            "job1": {"payload": "Do something.", "session": "default"}
        }
    }))
    result = load_cron_config()
    job = result["jobs"]["job1"]
    assert job["agent"] == "main"


def test_load_cron_config_allows_ephemeral_without_announce(tmp_path, monkeypatch):
    monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
    (tmp_path / "cron.json").write_text(json.dumps({
        "jobs": {
            "fire-and-forget": {"payload": "Do something."}
        }
    }))
    result = load_cron_config()
    assert "fire-and-forget" in result["jobs"]


def test_load_cron_config_validates_payload_required(tmp_path, monkeypatch):
    monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
    (tmp_path / "cron.json").write_text(json.dumps({
        "jobs": {
            "bad-job": {"session": "default"}
        }
    }))
    with pytest.raises(ValueError, match="payload"):
        load_cron_config()


# --- load_models / resolve_model ---

def test_load_models_returns_empty_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
    from fera.config import load_models
    assert load_models() == {}


def test_load_models_reads_models_json(tmp_path, monkeypatch):
    monkeypatch.setattr("fera.config.FERA_HOME", tmp_path)
    (tmp_path / "models.json").write_text(json.dumps({
        "models": {"opus": "claude-opus-4-6", "sonnet": "claude-sonnet-4-6"}
    }))
    from fera.config import load_models
    result = load_models()
    assert result == {"opus": "claude-opus-4-6", "sonnet": "claude-sonnet-4-6"}


def test_resolve_model_none_returns_none(tmp_path):
    from fera.config import resolve_model
    assert resolve_model(None, tmp_path) is None


def test_resolve_model_exact_alias_match(tmp_path):
    (tmp_path / "models.json").write_text(json.dumps({
        "models": {"haiku": "claude-haiku-4-5-20251001"}
    }))
    from fera.config import resolve_model
    assert resolve_model("haiku", tmp_path) == "claude-haiku-4-5-20251001"


def test_resolve_model_substring_match(tmp_path):
    (tmp_path / "models.json").write_text(json.dumps({
        "models": {"haiku": "claude-haiku-4-5-20251001", "sonnet": "claude-sonnet-4-6"}
    }))
    from fera.config import resolve_model
    assert resolve_model("hai", tmp_path) == "claude-haiku-4-5-20251001"


def test_resolve_model_ambiguous_raises(tmp_path):
    (tmp_path / "models.json").write_text(json.dumps({
        "models": {"sonnet-fast": "claude-sonnet-fast", "sonnet-large": "claude-sonnet-large"}
    }))
    from fera.config import resolve_model
    with pytest.raises(ValueError, match="sonnet"):
        resolve_model("sonnet", tmp_path)


def test_resolve_model_passthrough_unknown(tmp_path):
    (tmp_path / "models.json").write_text(json.dumps({"models": {}}))
    from fera.config import resolve_model
    assert resolve_model("claude-custom-model", tmp_path) == "claude-custom-model"


def test_resolve_model_no_models_file_passthrough(tmp_path):
    from fera.config import resolve_model
    assert resolve_model("claude-opus-4-6", tmp_path) == "claude-opus-4-6"
