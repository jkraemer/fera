"""Agent workspace initialization from templates."""

from __future__ import annotations

import shutil
from pathlib import Path

from fera.config import AGENTS_DIR

TEMPLATES_DIR = Path(__file__).parent / "templates"


def init_agent(name: str, *, agents_dir: Path | None = None) -> Path:
    """Initialize a new agent workspace from templates.

    Copies template files into agents/<name>/workspace/ and creates
    the data/ directory. Raises FileExistsError if the agent already
    exists.

    Returns the path to the new agent directory.
    """
    agents_dir = agents_dir or AGENTS_DIR
    agent_dir = agents_dir / name
    workspace = agent_dir / "workspace"

    if workspace.exists():
        raise FileExistsError(f"Agent '{name}' already exists at {agent_dir}")

    # Create directory structure
    workspace.mkdir(parents=True)
    (workspace / "memory").mkdir()
    (agent_dir / "data").mkdir()

    # Copy templates into workspace
    for item in TEMPLATES_DIR.iterdir():
        dest = workspace / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    # Replace workspace path placeholder in all markdown files
    for md_file in workspace.rglob("*.md"):
        text = md_file.read_text()
        if "{{WORKSPACE_PATH}}" in text:
            md_file.write_text(text.replace("{{WORKSPACE_PATH}}", str(workspace)))

    return agent_dir


def ensure_agent(name: str, *, agents_dir: Path | None = None) -> Path:
    """Ensure an agent workspace exists, initializing from templates if needed.

    Safe to call on every startup — no-op if the agent already exists.
    Returns the path to the agent directory.
    """
    agents_dir = agents_dir or AGENTS_DIR
    agent_dir = agents_dir / name
    workspace = agent_dir / "workspace"

    if not workspace.exists():
        return init_agent(name, agents_dir=agents_dir)

    return agent_dir


def main() -> None:
    """CLI entry point: fera-create-agent <name>"""
    import sys

    if len(sys.argv) != 2:
        print("Usage: fera-create-agent <name>", file=sys.stderr)
        sys.exit(1)

    name = sys.argv[1]
    try:
        agent_dir = init_agent(name)
        print(f"Created agent '{name}' at {agent_dir}")
    except FileExistsError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
