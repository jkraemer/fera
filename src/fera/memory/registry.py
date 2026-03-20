from __future__ import annotations

import contextvars
from pathlib import Path

from fera.memory.embeddings import Embedder
from fera.memory.index import MemoryIndex

current_agent: contextvars.ContextVar[str] = contextvars.ContextVar("current_agent")


class AgentRegistry:
    """Discovers agents and lazily creates per-agent MemoryIndex instances."""

    def __init__(self, agents_dir: Path):
        self._agents_dir = agents_dir
        self._indexes: dict[str, MemoryIndex] = {}
        self._embedder = Embedder()

    def discover(self) -> list[str]:
        """Return sorted list of agent names (dirs with a workspace/ subdir)."""
        if not self._agents_dir.exists():
            return []
        return sorted(
            d.name
            for d in self._agents_dir.iterdir()
            if d.is_dir() and (d / "workspace").is_dir()
        )

    def has_agent(self, agent: str) -> bool:
        return (self._agents_dir / agent / "workspace").is_dir()

    def workspace_dir(self, agent: str) -> Path:
        return self._agents_dir / agent / "workspace"

    def data_dir(self, agent: str) -> Path:
        return self._agents_dir / agent / "data"

    def get_index(self, agent: str) -> MemoryIndex:
        """Get or lazily create a MemoryIndex for the given agent."""
        if agent not in self._indexes:
            if not self.has_agent(agent):
                raise KeyError(f"Unknown agent: {agent}")
            self._indexes[agent] = MemoryIndex(
                memory_dir=str(self.workspace_dir(agent)),
                db_path=str(self.data_dir(agent) / "memory.db"),
                embedder=self._embedder,
            )
        return self._indexes[agent]

    def sync_agent(self, agent: str) -> None:
        """Sync a specific agent's index with its workspace files."""
        self.get_index(agent).sync()
