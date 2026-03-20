import pytest

from fera.setup import init_agent, ensure_agent, main, TEMPLATES_DIR


def test_templates_dir_exists():
    """Templates directory ships with the package."""
    assert TEMPLATES_DIR.is_dir()
    assert (TEMPLATES_DIR / "MEMORY.md").exists()
    assert (TEMPLATES_DIR / "persona" / "SOUL.md").exists()


def test_init_agent_creates_structure(tmp_path):
    workspace = tmp_path / "main" / "workspace"
    data = tmp_path / "main" / "data"
    init_agent("main", agents_dir=tmp_path)
    assert workspace.is_dir()
    assert (workspace / "memory").is_dir()
    assert (workspace / "persona").is_dir()
    assert data.is_dir()


def test_init_agent_copies_top_level_templates(tmp_path):
    init_agent("main", agents_dir=tmp_path)
    workspace = tmp_path / "main" / "workspace"
    assert (workspace / "MEMORY.md").exists()
    assert (workspace / "AGENTS.md").exists()
    assert (workspace / "HEARTBEAT.md").exists()


def test_init_agent_copies_persona_templates(tmp_path):
    init_agent("main", agents_dir=tmp_path)
    persona = tmp_path / "main" / "workspace" / "persona"
    assert (persona / "SOUL.md").exists()
    assert (persona / "USER.md").exists()
    assert (persona / "IDENTITY.md").exists()


def test_init_agent_refuses_existing(tmp_path):
    init_agent("main", agents_dir=tmp_path)
    with pytest.raises(FileExistsError, match="main"):
        init_agent("main", agents_dir=tmp_path)


def test_init_agent_returns_agent_dir(tmp_path):
    result = init_agent("research", agents_dir=tmp_path)
    assert result == tmp_path / "research"


def test_init_agent_uses_default_agents_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("fera.setup.AGENTS_DIR", tmp_path)
    result = init_agent("scout")
    assert result == tmp_path / "scout"
    assert (tmp_path / "scout" / "workspace" / "MEMORY.md").exists()


def test_ensure_agent_initializes_if_missing(tmp_path, monkeypatch):
    """ensure_agent runs init_agent when workspace doesn't exist."""
    monkeypatch.setattr("fera.setup.AGENTS_DIR", tmp_path)
    ensure_agent("main")
    assert (tmp_path / "main" / "workspace" / "MEMORY.md").exists()


def test_ensure_agent_is_noop_if_exists(tmp_path, monkeypatch):
    """ensure_agent skips init if workspace already exists."""
    monkeypatch.setattr("fera.setup.AGENTS_DIR", tmp_path)
    init_agent("main", agents_dir=tmp_path)
    # Modify a file to prove ensure_agent doesn't overwrite
    mem = tmp_path / "main" / "workspace" / "MEMORY.md"
    mem.write_text("# Custom content\n")
    ensure_agent("main")
    assert "Custom content" in mem.read_text()


# --- main ---

def test_main_creates_agent(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("fera.setup.AGENTS_DIR", tmp_path)
    monkeypatch.setattr("sys.argv", ["fera-create-agent", "coding"])
    main()
    assert (tmp_path / "coding" / "workspace" / "MEMORY.md").exists()
    assert "coding" in capsys.readouterr().out


def test_main_exits_on_existing_agent(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("fera.setup.AGENTS_DIR", tmp_path)
    init_agent("coding", agents_dir=tmp_path)
    monkeypatch.setattr("sys.argv", ["fera-create-agent", "coding"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    assert "coding" in capsys.readouterr().err


def test_main_exits_without_args(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["fera-create-agent"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    assert "Usage" in capsys.readouterr().err


# --- workspace path substitution ---

def test_init_agent_workspace_path_in_bootstrap(tmp_path):
    """BOOTSTRAP.md contains the actual workspace path after init."""
    init_agent("main", agents_dir=tmp_path)
    workspace = tmp_path / "main" / "workspace"
    content = (workspace / "BOOTSTRAP.md").read_text()
    assert str(workspace) in content


def test_init_agent_workspace_path_in_agents(tmp_path):
    """AGENTS.md contains the actual workspace path after init."""
    init_agent("main", agents_dir=tmp_path)
    workspace = tmp_path / "main" / "workspace"
    content = (workspace / "AGENTS.md").read_text()
    assert str(workspace) in content


def test_init_agent_no_leftover_placeholder(tmp_path):
    """No {{WORKSPACE_PATH}} placeholder remains in any file after init."""
    init_agent("main", agents_dir=tmp_path)
    workspace = tmp_path / "main" / "workspace"
    for md_file in workspace.rglob("*.md"):
        assert "{{WORKSPACE_PATH}}" not in md_file.read_text(), \
            f"Placeholder not substituted in {md_file.relative_to(workspace)}"
