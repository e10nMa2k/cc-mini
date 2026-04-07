import pytest
from tools.file_read import FileReadTool
from tools.glob_tool import GlobTool


@pytest.fixture
def tmp_file(tmp_path):
    f = tmp_path / "sample.txt"
    f.write_text("line one\nline two\nline three\n")
    return str(f)


def test_file_read_returns_numbered_content(tmp_file):
    result = FileReadTool().execute(file_path=tmp_file)
    assert not result.is_error
    assert "1\tline one" in result.content
    assert "2\tline two" in result.content


def test_file_read_respects_offset_and_limit(tmp_file):
    result = FileReadTool().execute(file_path=tmp_file, offset=1, limit=1)
    assert not result.is_error
    assert "line two" in result.content
    assert "line one" not in result.content


def test_file_read_missing_file():
    result = FileReadTool().execute(file_path="/nonexistent/path/file.txt")
    assert result.is_error
    assert "not found" in result.content.lower() or "no such" in result.content.lower()


def test_file_read_is_read_only():
    assert FileReadTool().is_read_only() is True


def test_glob_finds_files(tmp_path):
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "b.py").write_text("x")
    (tmp_path / "c.txt").write_text("x")

    result = GlobTool().execute(pattern="*.py", path=str(tmp_path))
    assert not result.is_error
    assert "a.py" in result.content
    assert "b.py" in result.content
    assert "c.txt" not in result.content


def test_glob_no_matches(tmp_path):
    result = GlobTool().execute(pattern="*.xyz", path=str(tmp_path))
    assert not result.is_error
    assert "No files found" in result.content


def test_glob_missing_dir():
    result = GlobTool().execute(pattern="*.py", path="/no/such/dir")
    assert result.is_error


def test_glob_is_read_only():
    assert GlobTool().is_read_only() is True


from tools.grep_tool import GrepTool


def test_grep_finds_pattern(tmp_path):
    (tmp_path / "a.py").write_text("def hello():\n    pass\n")
    (tmp_path / "b.py").write_text("def world():\n    pass\n")

    result = GrepTool().execute(pattern="hello", path=str(tmp_path))
    assert not result.is_error
    assert "a.py" in result.content
    assert "b.py" not in result.content


def test_grep_no_match(tmp_path):
    (tmp_path / "a.py").write_text("nothing here\n")
    result = GrepTool().execute(pattern="xyz123", path=str(tmp_path))
    assert not result.is_error
    assert "No matches" in result.content


def test_grep_case_insensitive(tmp_path):
    (tmp_path / "a.txt").write_text("Hello World\n")
    result = GrepTool().execute(pattern="hello", path=str(tmp_path), **{"-i": True})
    assert not result.is_error
    assert "a.txt" in result.content


def test_grep_is_read_only():
    assert GrepTool().is_read_only() is True


from tools.file_edit import FileEditTool


def test_file_edit_replaces_unique_string(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("def hello():\n    pass\n")
    FileEditTool.mark_file_read(str(f))

    result = FileEditTool().execute(
        file_path=str(f),
        old_string="    pass",
        new_string='    return "hi"',
    )
    assert not result.is_error
    assert 'return "hi"' in f.read_text()


def test_file_edit_fails_on_duplicate_string(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("pass\npass\n")
    FileEditTool.mark_file_read(str(f))

    result = FileEditTool().execute(file_path=str(f), old_string="pass", new_string="x")
    assert result.is_error
    assert "2" in result.content  # mentions count


def test_file_edit_replace_all(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("x = 1\nx = 2\n")
    FileEditTool.mark_file_read(str(f))

    result = FileEditTool().execute(file_path=str(f), old_string="x", new_string="y", replace_all=True)
    assert not result.is_error
    assert "y = 1" in f.read_text()
    assert "y = 2" in f.read_text()


def test_file_edit_missing_file():
    result = FileEditTool().execute(
        file_path="/no/such/file.py", old_string="x", new_string="y"
    )
    assert result.is_error


def test_file_edit_string_not_found(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("hello\n")
    FileEditTool.mark_file_read(str(f))
    result = FileEditTool().execute(file_path=str(f), old_string="xyz", new_string="abc")
    assert result.is_error
    assert "not found" in result.content.lower()


from tools.bash import BashTool


def test_bash_runs_command():
    result = BashTool().execute(command="echo hello")
    assert not result.is_error
    assert "hello" in result.content


def test_bash_captures_stderr():
    result = BashTool().execute(command="echo err >&2")
    assert "err" in result.content


def test_bash_nonzero_exit_code():
    result = BashTool().execute(command="exit 1")
    assert "exit code: 1" in result.content


def test_bash_timeout():
    result = BashTool().execute(command="sleep 10", timeout=1)
    assert result.is_error
    assert "timed out" in result.content.lower()


def test_bash_is_not_read_only():
    assert BashTool().is_read_only() is False
