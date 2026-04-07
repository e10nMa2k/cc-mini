from unittest.mock import patch
import os
from core.permissions import PermissionChecker
from tools.file_read import FileReadTool
from tools.bash import BashTool
from tools.file_edit import FileEditTool


def test_read_only_tool_always_allowed():
    checker = PermissionChecker()
    result = checker.check(FileReadTool(), {"file_path": "/tmp/test.txt"})
    assert result == "allow"


def test_auto_approve_allows_everything():
    checker = PermissionChecker(auto_approve=True)
    assert checker.check(BashTool(), {"command": "rm -rf /"}) == "allow"
    assert checker.check(FileEditTool(), {"file_path": "/etc/passwd", "old_string": "x", "new_string": "y"}) == "allow"


def _mock_prompt_user(checker, response: str):
    """Patch _prompt_user to return a canned response without touching stdin."""
    def fake_prompt(tool, inputs):
        if response == "a":
            checker._always_allow.add(tool.name)
            return "allow"
        return "allow" if response == "y" else "deny"
    return patch.object(checker, "_prompt_user", side_effect=fake_prompt)


def test_bash_prompts_user_and_allows_on_y():
    checker = PermissionChecker()
    with _mock_prompt_user(checker, "y"):
        result = checker.check(BashTool(), {"command": "echo hello"})
    assert result == "allow"


def test_bash_prompts_user_and_denies_on_n():
    checker = PermissionChecker()
    with _mock_prompt_user(checker, "n"):
        result = checker.check(BashTool(), {"command": "rm something"})
    assert result == "deny"


def test_always_caches_approval():
    checker = PermissionChecker()
    with _mock_prompt_user(checker, "a"):
        checker.check(BashTool(), {"command": "echo first"})
    # Second call should NOT prompt — already cached via _always_allow
    result = checker.check(BashTool(), {"command": "echo second"})
    assert result == "allow"
