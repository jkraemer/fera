import pytest

from fera.memory.registry import AgentRegistry, current_agent


@pytest.fixture
def agents_dir(tmp_path):
    """Create an agents directory with two agents."""
    for name in ("main", "research"):
        workspace = tmp_path / name / "workspace"
        workspace.mkdir(parents=True)
        (workspace / "MEMORY.md").write_text(f"# {name}\n\nSome facts.\n")
        (tmp_path / name / "data").mkdir()
    return tmp_path


def test_discover_finds_agents(agents_dir):
    registry = AgentRegistry(agents_dir)
    assert registry.discover() == ["main", "research"]


def test_discover_skips_dirs_without_workspace(agents_dir):
    (agents_dir / "broken").mkdir()
    registry = AgentRegistry(agents_dir)
    assert "broken" not in registry.discover()


def test_has_agent(agents_dir):
    registry = AgentRegistry(agents_dir)
    assert registry.has_agent("main")
    assert not registry.has_agent("nonexistent")


def test_workspace_dir(agents_dir):
    registry = AgentRegistry(agents_dir)
    assert registry.workspace_dir("main") == agents_dir / "main" / "workspace"


def test_data_dir(agents_dir):
    registry = AgentRegistry(agents_dir)
    assert registry.data_dir("main") == agents_dir / "main" / "data"


def test_get_index_creates_lazily(agents_dir):
    registry = AgentRegistry(agents_dir)
    index = registry.get_index("main")
    assert index is not None
    # Same instance on second call
    assert registry.get_index("main") is index


def test_get_index_unknown_agent_raises(agents_dir):
    registry = AgentRegistry(agents_dir)
    with pytest.raises(KeyError, match="Unknown agent"):
        registry.get_index("nonexistent")


def test_sync_agent(agents_dir):
    registry = AgentRegistry(agents_dir)
    registry.sync_agent("main")
    index = registry.get_index("main")
    assert len(index.all_chunks()) > 0


def test_current_agent_contextvar():
    token = current_agent.set("main")
    assert current_agent.get() == "main"
    current_agent.reset(token)


def test_discover_empty_dir(tmp_path):
    registry = AgentRegistry(tmp_path)
    assert registry.discover() == []


def test_discover_nonexistent_dir(tmp_path):
    registry = AgentRegistry(tmp_path / "nope")
    assert registry.discover() == []


def test_get_index_shares_embedder_across_agents(agents_dir):
    registry = AgentRegistry(agents_dir)
    index_main = registry.get_index("main")
    index_research = registry.get_index("research")
    assert index_main.embedder is index_research.embedder
