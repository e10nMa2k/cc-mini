from unittest.mock import patch
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


def test_prompt_callbacks_wrap_user_prompt():
    checker = PermissionChecker()
    events = []
    checker.set_prompt_callbacks(
        on_prompt_start=lambda: events.append("start"),
        on_prompt_end=lambda: events.append("end"),
    )

    with _mock_prompt_user(checker, "y"):
        result = checker.check(BashTool(), {"command": "echo hello"})

    assert result == "allow"
    assert events == ["start", "end"]


def test_fork_preserves_prompt_callbacks_for_nested_tools():
    checker = PermissionChecker()
    events = []
    checker.set_prompt_callbacks(
        on_prompt_start=lambda: events.append("start"),
        on_prompt_end=lambda: events.append("end"),
    )

    child = checker.fork()

    with _mock_prompt_user(child, "y"):
        result = child.check(BashTool(), {"command": "echo from child"})

    assert result == "allow"
    assert events == ["start", "end"]


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


def test_dream_mode_denies_sibling_directory_with_same_prefix(tmp_path):
    memory_dir = tmp_path / "memory"
    sibling_dir = tmp_path / "memory2"
    memory_dir.mkdir()
    sibling_dir.mkdir()

    checker = PermissionChecker()
    checker.enter_dream_mode(str(memory_dir))

    assert checker.check(
        FileEditTool(),
        {
            "file_path": str(memory_dir / "MEMORY.md"),
            "old_string": "x",
            "new_string": "y",
        },
    ) == "allow"
    assert checker.check(
        FileEditTool(),
        {
            "file_path": str(sibling_dir / "MEMORY.md"),
            "old_string": "x",
            "new_string": "y",
        },
    ) == "deny"


def test_fork_copies_effective_policy_and_isolates_mutable_state(tmp_path):
    sandbox = object()
    listener = object()
    checker = PermissionChecker(auto_approve=True, sandbox_manager=sandbox)
    checker._always_allow.add("Bash")
    checker.set_esc_listener(listener)
    checker._mode = "plan"
    checker._pre_plan_mode = "default"
    checker._pre_plan_always_allow = {"Write"}
    checker.enter_dream_mode(str(tmp_path))
    checker._plan_manager = object()

    child = checker.fork()

    assert child is not checker
    assert child._auto_approve is True
    assert child._sandbox is sandbox
    assert child._esc_listener is listener
    assert child._mode == "plan"
    assert child._pre_plan_mode == "default"
    assert child._dream_mode is True
    assert child._dream_memory_dir == str(tmp_path.resolve())
    assert child._plan_manager is None
    assert child._always_allow == {"Bash"}
    assert child._pre_plan_always_allow == {"Write"}

    child._always_allow.add("Edit")
    child._pre_plan_always_allow.add("Bash")
    assert checker._always_allow == {"Bash"}
    assert checker._pre_plan_always_allow == {"Write"}


def test_forked_plan_mode_cannot_use_parent_plan_file_exception(tmp_path):
    plan_file = tmp_path / "plan.md"

    class PlanManager:
        plan_file_path = str(plan_file)

    checker = PermissionChecker()
    checker.set_plan_manager(PlanManager())
    checker.enter_plan_mode()
    child = checker.fork()
    inputs = {
        "file_path": str(plan_file),
        "old_string": "x",
        "new_string": "y",
    }

    assert checker.check(FileEditTool(), inputs) == "allow"
    assert child.check(FileEditTool(), inputs) == "deny"
