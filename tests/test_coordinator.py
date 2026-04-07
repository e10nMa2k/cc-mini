from features.coordinator import (
    current_session_mode,
    get_coordinator_system_prompt,
    get_coordinator_user_context,
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
