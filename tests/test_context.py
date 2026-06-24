from unittest.mock import patch, MagicMock
import subprocess
from core.context import (
    _get_claude_md_section,
    _get_git_section,
    build_skill_system_prompt,
    build_system_prompt,
)


def test_build_system_prompt_contains_base_instructions():
    prompt = build_system_prompt(cwd="/tmp")
    assert "software engineering tasks" in prompt
    assert "tools" in prompt.lower()


def test_build_system_prompt_contains_env_info():
    prompt = build_system_prompt(cwd="/tmp")
    assert "Primary working directory: /tmp" in prompt
    assert "Platform:" in prompt
    assert "Shell:" in prompt


def test_build_system_prompt_contains_working_directory():
    prompt = build_system_prompt(cwd="/some/test/dir")
    assert "/some/test/dir" in prompt


def test_build_system_prompt_includes_git_status_when_available():
    fake_result = MagicMock()
    fake_result.stdout = "main"

    with patch("core.context.subprocess.run", return_value=fake_result):
        prompt = build_system_prompt(cwd="/tmp")
    assert "Git Status" in prompt
    assert "main" in prompt


def test_build_system_prompt_includes_claude_md(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# Test Project\nSome instructions here.")

    prompt = build_system_prompt(cwd=str(tmp_path))
    assert "CLAUDE.md" in prompt
    assert "Test Project" in prompt


def test_build_system_prompt_without_claude_md(tmp_path):
    prompt = build_system_prompt(cwd=str(tmp_path))
    # Should not have the CLAUDE.md section header (beyond the base prompt)
    assert "# Test Project" not in prompt


def test_get_git_section_returns_branch_and_log(tmp_path):
    def fake_run(cmd, **kwargs):
        result = MagicMock()
        if "branch" in cmd:
            result.stdout = "feature-branch"
        elif "status" in cmd:
            result.stdout = " M file.py"
        elif "log" in cmd:
            result.stdout = "abc1234 some commit"
        else:
            result.stdout = ""
        return result

    with patch("core.context.subprocess.run", side_effect=fake_run):
        status = _get_git_section(str(tmp_path))

    assert "feature-branch" in status
    assert "M file.py" in status
    assert "abc1234" in status


def test_get_git_section_returns_empty_on_non_git_dir():
    def fake_run(cmd, **kwargs):
        result = MagicMock()
        result.stdout = ""
        return result

    with patch("core.context.subprocess.run", side_effect=fake_run):
        status = _get_git_section("/tmp/not-a-git-repo")
    assert status == ""


def test_get_git_section_returns_empty_on_exception():
    with patch("core.context.subprocess.run", side_effect=OSError("fail")):
        status = _get_git_section("/tmp")
    assert status == ""


def test_get_claude_md_section_reads_file(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("hello world")

    result = _get_claude_md_section(str(tmp_path))
    assert "hello world" in result
    assert "CLAUDE.md" in result


def test_get_claude_md_section_returns_empty_when_missing(tmp_path):
    result = _get_claude_md_section(str(tmp_path))
    assert result == ""


def test_get_claude_md_section_truncates_large_file(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("x" * 20_000)

    result = _get_claude_md_section(str(tmp_path))
    # Section includes header, so content is truncated to fit within 10k chars
    assert len(result) <= 10_100  # Allow some margin for the header


def test_build_skill_system_prompt_is_lean_and_explicit(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# Project Rules\nUse focused tests.")

    prompt = build_skill_system_prompt(
        cwd=str(tmp_path),
        model="test-model",
        tool_names=["Read", "Bash"],
    )

    assert "authorized security testing" in prompt
    assert "software engineering tasks" in prompt
    assert "# Executing actions with care" in prompt
    assert "# Output efficiency" in prompt
    assert f"Primary working directory: {tmp_path}" in prompt
    assert "# Project Rules" in prompt
    assert "# Skill Execution" in prompt
    assert "# Tool behavior" in prompt
    assert "If a tool call is denied" in prompt
    assert "<system-reminder>" in prompt
    assert "prompt injection" in prompt
    assert "Available tools: Read, Bash" in prompt
    assert "Return a concise final result" in prompt

    assert "# Git Status" not in prompt
    assert "# Auto Memory" not in prompt
    assert "# Companion" not in prompt
    assert "# Available Skills" not in prompt
    assert "<task-notification>" not in prompt
    assert "Plan mode is active" not in prompt
    assert "advisor_20260301" not in prompt
    assert "automatically compress prior messages" not in prompt
