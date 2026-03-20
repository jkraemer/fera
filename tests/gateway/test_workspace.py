import pytest
from fera.gateway.workspace import list_files, get_file, set_file


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "MEMORY.md").write_text("# Memory\n")
    (ws / "BOOTSTRAP.md").write_text("# Bootstrap\n")
    (ws / "persona").mkdir()
    (ws / "persona" / "SOUL.md").write_text("# Soul\n")
    return ws


def test_list_root(workspace):
    result = list_files(workspace)
    names = {e["name"] for e in result}
    assert "MEMORY.md" in names
    assert "persona" in names


def test_list_includes_type(workspace):
    result = list_files(workspace)
    by_name = {e["name"]: e for e in result}
    assert by_name["MEMORY.md"]["type"] == "file"
    assert by_name["persona"]["type"] == "directory"


def test_list_subdir(workspace):
    result = list_files(workspace, "persona")
    names = {e["name"] for e in result}
    assert "SOUL.md" in names


def test_list_path_traversal(workspace):
    with pytest.raises(ValueError, match="outside workspace"):
        list_files(workspace, "../")


def test_list_nonexistent(workspace):
    with pytest.raises(FileNotFoundError):
        list_files(workspace, "nonexistent")


def test_get_file(workspace):
    content = get_file(workspace, "MEMORY.md")
    assert content == "# Memory\n"


def test_get_nested_file(workspace):
    content = get_file(workspace, "persona/SOUL.md")
    assert content == "# Soul\n"


def test_get_path_traversal(workspace):
    with pytest.raises(ValueError, match="outside workspace"):
        get_file(workspace, "../../etc/passwd")


def test_get_not_found(workspace):
    with pytest.raises(FileNotFoundError):
        get_file(workspace, "nonexistent.md")


def test_set_file(workspace):
    set_file(workspace, "MEMORY.md", "# Updated\n")
    assert (workspace / "MEMORY.md").read_text() == "# Updated\n"


def test_set_creates_new_file(workspace):
    set_file(workspace, "NEW.md", "# New\n")
    assert (workspace / "NEW.md").read_text() == "# New\n"


def test_set_creates_parent_dirs(workspace):
    set_file(workspace, "notes/2026/jan.md", "content")
    assert (workspace / "notes" / "2026" / "jan.md").read_text() == "content"


def test_set_path_traversal(workspace):
    with pytest.raises(ValueError, match="outside workspace"):
        set_file(workspace, "../outside.md", "evil")
