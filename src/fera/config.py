from __future__ import annotations

import copy
import json
import os
import re
import secrets
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")


def substitute_text(text: str) -> str:
    """Apply ${VAR} substitution to raw text before JSON parsing."""
    return _ENV_VAR_RE.sub(
        lambda m: os.environ.get(m.group(1), m.group(0)),
        text,
    )


def load_json_config(path: Path) -> dict:
    """Read a JSON config file, applying ${VAR} substitution before parsing."""
    try:
        return json.loads(substitute_text(path.read_text()))
    except json.JSONDecodeError as exc:
        raise json.JSONDecodeError(
            f"{path}: {exc.msg}", exc.doc, exc.pos,
        ) from None


def _resolve_fera_home() -> Path:
    """Resolve FERA_HOME from env var, defaulting to $HOME."""
    return Path(os.environ.get("FERA_HOME", Path.home()))


FERA_HOME = _resolve_fera_home()
DEFAULT_AGENT = "main"
AGENTS_DIR = FERA_HOME / "agents"


def workspace_dir(agent_name: str, fera_home: Path = FERA_HOME) -> Path:
    """Return the workspace directory for a given agent."""
    return fera_home / "agents" / agent_name / "workspace"


def data_dir(agent_name: str, fera_home: Path = FERA_HOME) -> Path:
    """Return the data directory for a given agent."""
    return fera_home / "agents" / agent_name / "data"

def local_now(tz_name: str | None) -> datetime:
    """Current datetime in the configured timezone (UTC when None)."""
    tz = ZoneInfo(tz_name) if tz_name else timezone.utc
    return datetime.now(tz)


def local_date(tz_name: str | None) -> date:
    """Current date in the configured timezone (UTC when None)."""
    return local_now(tz_name).date()


DEFAULT_CONFIG = {
    "timezone": None,
    "alert_session": None,
    "gateway": {
        "host": "127.0.0.1",
        "port": 8389,
        "pool": {
            "max_clients": 5,
            "idle_timeout_minutes": 30,
        },
    },
    "memory": {
        "host": "127.0.0.1",
        "port": 8390,
    },
    "webui": {
        "host": "0.0.0.0",
        "port": 8080,
        "static_dir": "/opt/fera/webui/dist",
    },
    "mcp_servers": {},
    "heartbeat": {
        "enabled": False,
        "interval_minutes": 30,
        "active_hours": "08:00-22:00",
        "session": "default",
        "agent_tools": None,
    },
    "dream_cycle": {
        "enabled": False,
        "time": "03:00",
        "agents": [],
        "model": None,
    },
    "metrics": {
        "retention_days": 365,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def substitute_env_vars(obj: object) -> object:
    """Recursively substitute ${VAR} references with environment variable values.

    Unknown variables are left as-is.
    """
    if isinstance(obj, str):
        return _ENV_VAR_RE.sub(
            lambda m: os.environ.get(m.group(1), m.group(0)),
            obj,
        )
    if isinstance(obj, dict):
        return {k: substitute_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [substitute_env_vars(item) for item in obj]
    return obj


def load_agent_config(agent_name: str, fera_home: Path | None = None) -> dict:
    """Load per-agent config from $FERA_HOME/agents/{agent_name}/config.json."""
    home = fera_home or FERA_HOME
    config_path = home / "agents" / agent_name / "config.json"
    if config_path.exists():
        return load_json_config(config_path)
    return {}


def memory_url(config: dict | None = None) -> str:
    """Build the memory server SSE URL from config."""
    if config is None:
        config = load_config()
    mem = config["memory"]
    return f"http://{mem['host']}:{mem['port']}/sse"


def load_config(config_path: Path | None = None) -> dict:
    """Load config from *config_path* (default ``~/.fera/config.json``), merged with defaults."""
    if config_path is None:
        config_path = FERA_HOME / "config.json"
    if config_path.exists():
        user_config = load_json_config(config_path)
        return _deep_merge(DEFAULT_CONFIG, user_config)
    return copy.deepcopy(DEFAULT_CONFIG)


def save_config(update: dict) -> None:
    """Merge update into user config file and write it back."""
    config_path = FERA_HOME / "config.json"
    existing = {}
    if config_path.exists():
        existing = json.loads(config_path.read_text())
    merged = _deep_merge(existing, update)
    config_path.write_text(json.dumps(merged, indent=2) + "\n")


def load_cron_config() -> dict:
    """Load cron job definitions from $FERA_HOME/cron.json.

    Applies env var substitution, fills defaults, and validates.
    Returns {"jobs": {name: spec, ...}}.
    """
    path = FERA_HOME / "cron.json"
    if not path.exists():
        return {"jobs": {}}
    raw = load_json_config(path)
    jobs = raw.get("jobs", {})
    for name, spec in jobs.items():
        if "payload" not in spec:
            raise ValueError(f"Job '{name}': 'payload' is required")
        spec.setdefault("agent", DEFAULT_AGENT)
        if "session" not in spec or spec["session"] is None:
            spec.pop("session", None)
    return {"jobs": jobs}


def load_models(fera_home: Path | None = None) -> dict[str, str]:
    """Load the model alias catalog from $FERA_HOME/models.json.

    Returns a dict mapping alias -> full model ID, or {} if the file is missing.
    """
    home = fera_home or FERA_HOME
    path = home / "models.json"
    if not path.exists():
        return {}
    raw = load_json_config(path)
    return raw.get("models", {})


def resolve_model(raw: str | None, fera_home: Path) -> str | None:
    """Resolve a model alias to a full model ID.

    - None -> None (SDK default)
    - Exact alias match -> full model ID
    - Unique substring match -> full model ID
    - Ambiguous substring match -> ValueError
    - No match -> raw string passed through (assumed full model ID)
    """
    if raw is None:
        return None
    models = load_models(fera_home)
    if raw in models:
        return models[raw]
    matches = [k for k in models if raw in k]
    if len(matches) == 1:
        return models[matches[0]]
    if len(matches) > 1:
        raise ValueError(
            f"Ambiguous model alias {raw!r} — matches: {', '.join(sorted(matches))}"
        )
    return raw


def ensure_auth_token(config: dict, *, save: bool = True) -> str:
    """Return the gateway auth token, generating one if not configured."""
    raw = config.get("gateway", {}).get("auth_token")
    if raw is not None:
        return substitute_env_vars(raw)
    token = secrets.token_hex(32)
    config.setdefault("gateway", {})["auth_token"] = token
    if save:
        save_config({"gateway": {"auth_token": token}})
    return token
