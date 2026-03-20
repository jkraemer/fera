from __future__ import annotations

from pathlib import Path


def _resolve_safe(workspace: Path, subpath: str) -> Path:
    """Resolve subpath within workspace, raising ValueError on traversal."""
    target = (workspace / subpath).resolve()
    if not target.is_relative_to(workspace.resolve()):
        raise ValueError("path outside workspace")
    return target


def list_files(workspace: Path, subpath: str = "") -> list[dict]:
    """List files and directories in workspace/subpath."""
    target = _resolve_safe(workspace, subpath)
    if not target.is_dir():
        raise FileNotFoundError(f"not a directory: {subpath}")
    return sorted(
        [
            {"name": item.name, "type": "directory" if item.is_dir() else "file"}
            for item in target.iterdir()
        ],
        key=lambda e: (e["type"] != "directory", e["name"]),
    )


def get_file(workspace: Path, path: str) -> str:
    """Read a file from the workspace."""
    target = _resolve_safe(workspace, path)
    if not target.is_file():
        raise FileNotFoundError(f"not found: {path}")
    return target.read_text()


def set_file(workspace: Path, path: str, content: str) -> None:
    """Write a file in the workspace."""
    target = _resolve_safe(workspace, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
