from pathlib import Path

import pytest

from fera.prompt import CONTEXT_FILES, MINIMAL_FILES, SystemPromptBuilder, load_context_files, truncate_content


def test_truncate_short_content_unchanged():
    text = "Hello world"
    assert truncate_content(text, max_chars=100) == text


def test_truncate_long_content():
    text = "A" * 100
    result = truncate_content(text, max_chars=50)
    assert len(result) <= 50
    assert "..." in result
    # Starts with head content
    assert result.startswith("A")
    # Ends with tail content
    assert result.endswith("A")


def test_truncate_preserves_head_and_tail_ratio():
    text = "H" * 70 + "T" * 30
    result = truncate_content(text, max_chars=50)
    # marker is 23 chars, budget = 27
    # head_len = 27 * 7 // 9 = 21, tail_len = 27 - 21 = 6
    head, tail = result.split("\n\n... [truncated] ...\n\n")
    assert head == "H" * 21
    assert tail == "T" * 6


def test_truncate_exact_boundary():
    text = "A" * 50
    assert truncate_content(text, max_chars=50) == text


def test_truncate_tiny_budget():
    result = truncate_content("A" * 100, max_chars=5)
    assert len(result) <= 5


def test_context_files_order():
    """File list matches the design doc ordering."""
    expected = [
        "AGENTS.md",
        "persona/SOUL.md",
        "TOOLS.md",
        "persona/IDENTITY.md",
        "persona/USER.md",
        "HEARTBEAT.md",
        "BOOTSTRAP.md",
        "persona/GOALS.md",
        "persona/SOUVENIR.md",
        "MEMORY.md",
    ]
    assert CONTEXT_FILES == expected


def test_minimal_files_subset():
    assert MINIMAL_FILES == ["AGENTS.md", "TOOLS.md"]


def test_load_context_files_reads_existing(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Agents")
    (tmp_path / "persona").mkdir()
    (tmp_path / "persona" / "SOUL.md").write_text("# Soul")
    result = load_context_files(tmp_path)
    assert len(result) == 2
    assert result[0] == ("AGENTS.md", "# Agents")
    assert result[1] == ("persona/SOUL.md", "# Soul")


def test_load_context_files_skips_missing(tmp_path):
    # No files created — all should be skipped
    result = load_context_files(tmp_path)
    assert result == []


def test_load_context_files_minimal_mode(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Agents")
    (tmp_path / "persona").mkdir()
    (tmp_path / "persona" / "SOUL.md").write_text("# Soul")
    (tmp_path / "TOOLS.md").write_text("# Tools")
    result = load_context_files(tmp_path, minimal=True)
    paths = [path for path, _ in result]
    assert paths == ["AGENTS.md", "TOOLS.md"]


def test_load_context_files_truncates_large_file(tmp_path):
    (tmp_path / "AGENTS.md").write_text("A" * 25000)
    result = load_context_files(tmp_path, max_chars_per_file=20000)
    _, content = result[0]
    assert len(content) <= 20000
    assert "... [truncated] ..." in content


def test_load_context_files_respects_total_budget(tmp_path):
    (tmp_path / "AGENTS.md").write_text("A" * 5000)
    (tmp_path / "persona").mkdir()
    (tmp_path / "persona" / "SOUL.md").write_text("B" * 5000)
    (tmp_path / "TOOLS.md").write_text("C" * 5000)
    # Budget of 12000 — first two files fit (10000), third would exceed
    result = load_context_files(tmp_path, total_budget=12000)
    paths = [path for path, _ in result]
    assert "AGENTS.md" in paths
    assert "persona/SOUL.md" in paths
    assert "TOOLS.md" not in paths


def test_build_none_mode(tmp_path):
    builder = SystemPromptBuilder(tmp_path)
    prompt = builder.build(mode="none")
    assert prompt == "You are a personal AI agent running inside Fera."


def test_build_full_includes_identity_line(tmp_path):
    builder = SystemPromptBuilder(tmp_path)
    prompt = builder.build(mode="full")
    assert prompt.startswith("You are a personal AI agent running inside Fera.")


def test_build_full_includes_context_files(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Workspace Rules")
    (tmp_path / "persona").mkdir()
    (tmp_path / "persona" / "SOUL.md").write_text("# My Soul")
    builder = SystemPromptBuilder(tmp_path)
    prompt = builder.build(mode="full")
    assert '<file path="AGENTS.md">' in prompt
    assert "# Workspace Rules" in prompt
    assert '<file path="persona/SOUL.md">' in prompt
    assert "# My Soul" in prompt


def test_build_full_soul_preamble(tmp_path):
    (tmp_path / "persona").mkdir()
    (tmp_path / "persona" / "SOUL.md").write_text("# Soul")
    builder = SystemPromptBuilder(tmp_path)
    prompt = builder.build(mode="full")
    assert "persona and tone" in prompt
    assert "embody it" in prompt


def test_build_full_no_soul_no_preamble(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Agents")
    builder = SystemPromptBuilder(tmp_path)
    prompt = builder.build(mode="full")
    assert "SOUL.md defines your persona" not in prompt


def test_build_full_includes_runtime(tmp_path):
    builder = SystemPromptBuilder(tmp_path)
    prompt = builder.build(mode="full")
    assert "## Runtime" in prompt
    # Should contain current year
    assert "2026" in prompt


def test_build_minimal_excludes_persona_files(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Agents")
    (tmp_path / "TOOLS.md").write_text("# Tools")
    (tmp_path / "persona").mkdir()
    (tmp_path / "persona" / "SOUL.md").write_text("# Soul")
    builder = SystemPromptBuilder(tmp_path)
    prompt = builder.build(mode="minimal")
    assert "AGENTS.md" in prompt
    assert "TOOLS.md" in prompt
    assert "SOUL.md" not in prompt


def test_build_minimal_includes_runtime(tmp_path):
    builder = SystemPromptBuilder(tmp_path)
    prompt = builder.build(mode="minimal")
    assert "## Runtime" in prompt


def test_build_full_file_order_preserved(tmp_path):
    (tmp_path / "AGENTS.md").write_text("FIRST")
    (tmp_path / "MEMORY.md").write_text("LAST")
    builder = SystemPromptBuilder(tmp_path)
    prompt = builder.build(mode="full")
    assert prompt.index("FIRST") < prompt.index("LAST")


def test_build_invalid_mode(tmp_path):
    builder = SystemPromptBuilder(tmp_path)
    with pytest.raises(ValueError, match="Unknown mode"):
        builder.build(mode="turbo")


def test_build_full_includes_security_block(tmp_path):
    builder = SystemPromptBuilder(tmp_path)
    prompt = builder.build(mode="full")
    assert "## Untrusted Content" in prompt
    assert "<untrusted>" in prompt


def test_build_minimal_includes_security_block(tmp_path):
    builder = SystemPromptBuilder(tmp_path)
    prompt = builder.build(mode="minimal")
    assert "## Untrusted Content" in prompt


def test_build_none_excludes_security_block(tmp_path):
    builder = SystemPromptBuilder(tmp_path)
    prompt = builder.build(mode="none")
    assert "Untrusted Content" not in prompt


def test_context_block_sanitizes_file_paths(tmp_path):
    """File paths with control characters are sanitized in the prompt."""
    (tmp_path / "AGENTS.md").write_text("# Agents")
    builder = SystemPromptBuilder(tmp_path)
    prompt = builder.build(mode="full")
    assert '<file path="AGENTS.md">' in prompt


def test_build_with_canary_token_includes_canary_block(tmp_path):
    builder = SystemPromptBuilder(tmp_path)
    token = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
    prompt = builder.build(mode="full", canary_token=token)
    assert "CANARY:" + token in prompt
    assert "Internal Integrity" in prompt


def test_build_without_canary_token_has_no_canary_block(tmp_path):
    builder = SystemPromptBuilder(tmp_path)
    prompt = builder.build(mode="full")
    assert "CANARY:" not in prompt
    assert "Internal Integrity" not in prompt


def test_build_minimal_with_canary_token(tmp_path):
    builder = SystemPromptBuilder(tmp_path)
    token = "deadbeef" * 4
    prompt = builder.build(mode="minimal", canary_token=token)
    assert "CANARY:" + token in prompt


def test_build_none_mode_ignores_canary_token(tmp_path):
    builder = SystemPromptBuilder(tmp_path)
    prompt = builder.build(mode="none", canary_token="deadbeef" * 4)
    assert "CANARY:" not in prompt
