from unittest.mock import patch, MagicMock
import subprocess
from core.context import build_system_prompt, _get_git_section, _get_claude_md_section


def test_build_system_prompt_contains_base_instructions():
    prompt = build_system_prompt(cwd="/tmp")
    assert "software engineering tasks" in prompt
    assert "tools" in prompt.lower()


def test_build_system_prompt_contains_date():
    prompt = build_system_prompt(cwd="/tmp")
    assert "Today's date:" in prompt


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
