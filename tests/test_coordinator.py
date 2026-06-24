from features.coordinator import (
    current_session_mode,
    get_coordinator_system_prompt,
    get_coordinator_user_context,
    get_worker_system_prompt,
    is_coordinator_mode,
    match_session_mode,
)


def test_is_coordinator_mode_reads_env(monkeypatch):
    monkeypatch.delenv("CC_MINI_COORDINATOR", raising=False)
    assert is_coordinator_mode() is False

    monkeypatch.setenv("CC_MINI_COORDINATOR", "1")
    assert is_coordinator_mode() is True
    assert current_session_mode() == "coordinator"


def test_match_session_mode_switches_env(monkeypatch):
    monkeypatch.delenv("CC_MINI_COORDINATOR", raising=False)

    warning = match_session_mode("coordinator")

    assert warning == "Entered coordinator mode to match resumed session."
    assert is_coordinator_mode() is True


def test_get_coordinator_user_context_hidden_when_disabled(monkeypatch):
    monkeypatch.delenv("CC_MINI_COORDINATOR", raising=False)
    assert get_coordinator_user_context(["Read", "Bash"]) == {}


def test_get_coordinator_user_context_lists_worker_tools(monkeypatch):
    monkeypatch.setenv("CC_MINI_COORDINATOR", "1")

    context = get_coordinator_user_context(["Read", "Bash"])

    assert "workerToolsContext" in context
    assert "Bash, Read" in context["workerToolsContext"]


def test_coordinator_system_prompt_mentions_task_notifications():
    prompt = get_coordinator_system_prompt()
    assert "task-notification" in prompt
    assert "Agent" in prompt
    assert "SendMessage" in prompt


def test_coordinator_prompt_lists_assign_only_skills():
    prompt = get_coordinator_system_prompt(("review", "test"))
    assert "review, test" in prompt
    assert "allowed_skills" in prompt
    assert "cannot invoke these skills yourself" in prompt
    assert "Explore" in prompt


def test_worker_prompt_lists_exact_immutable_authorization():
    prompt = get_worker_system_prompt(("review",))
    assert "review" in prompt
    assert "SkillTool" in prompt
    assert "cannot change" in prompt
    assert "test" not in prompt
