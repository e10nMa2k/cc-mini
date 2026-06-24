import json
import time
from unittest.mock import MagicMock

from core.engine import AbortedError
from features.agents.worker_manager import WorkerManager
from features.skills import Skill, clear_skills, register_skill
from tools.agent import AgentTool, SendMessageTool


def _register_model_skill(name: str) -> None:
    register_skill(Skill(
        name=name,
        model_invocable=True,
        allowed_tools_declared=True,
        _prompt_text="run",
    ))


class _FakeUsage:
    input_tokens = 3
    output_tokens = 4
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _FakeEngine:
    def __init__(self, mode: str):
        self.mode = mode
        self.aborted = False
        self.prompts: list[str] = []

    def submit(self, prompt: str):
        self.prompts.append(prompt)
        if self.mode == "complete":
            yield ("tool_call", "Read", {"file_path": "/tmp/example.py"})
            yield ("usage", _FakeUsage())
            yield ("text", f"finished:{prompt}")
            return
        if self.mode == "abortable":
            while not self.aborted:
                time.sleep(0.01)
            raise AbortedError()
        if self.mode == "error":
            raise RuntimeError("boom")
        raise AssertionError(f"Unexpected mode: {self.mode}")

    def abort(self) -> None:
        self.aborted = True


def _wait_for_notification(manager: WorkerManager, timeout: float = 1.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        notifications = manager.drain_notifications()
        if notifications:
            return notifications[0]
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for worker notification")


def test_worker_manager_spawns_and_reports_completion():
    engine = _FakeEngine("complete")
    manager = WorkerManager({"worker": lambda allowed: engine})

    launched = manager.spawn(description="Inspect", prompt="read the file")
    notification = _wait_for_notification(manager)

    assert launched["task_id"].startswith("agent-")
    assert "<status>completed</status>" in notification
    assert "finished:read the file" in notification
    assert "<tool_uses>1</tool_uses>" in notification
    assert "<total_tokens>7</total_tokens>" in notification


def test_worker_manager_can_continue_completed_task():
    engine = _FakeEngine("complete")
    manager = WorkerManager({"worker": lambda allowed: engine})

    launched = manager.spawn(description="Inspect", prompt="first")
    _wait_for_notification(manager)

    manager.continue_task(task_id=launched["task_id"], message="second")
    _wait_for_notification(manager)

    assert engine.prompts == ["first", "second"]


def test_worker_manager_can_stop_running_task():
    engine = _FakeEngine("abortable")
    manager = WorkerManager({"worker": lambda allowed: engine})

    launched = manager.spawn(description="Long task", prompt="wait")
    manager.stop_task(task_id=launched["task_id"])
    notification = _wait_for_notification(manager)

    assert "<status>killed</status>" in notification


def test_worker_manager_dispatches_explore_to_explore_factory():
    worker_engine = _FakeEngine("complete")
    explore_engine = _FakeEngine("complete")
    manager = WorkerManager({
        "worker": lambda allowed: worker_engine,
        "Explore": lambda allowed: explore_engine,
    })

    manager.spawn(description="Search files", prompt="find all .py files", subagent_type="Explore")
    notification = _wait_for_notification(manager)

    assert "<status>completed</status>" in notification
    assert explore_engine.prompts == ["find all .py files"]
    assert worker_engine.prompts == []


def test_worker_skill_grant_is_validated_and_frozen():
    clear_skills()
    _register_model_skill("review")
    _register_model_skill("test")
    engine = _FakeEngine("complete")
    factory_calls = []

    def factory(allowed):
        factory_calls.append(allowed)
        return engine

    manager = WorkerManager(
        {"worker": factory},
        grantable_skill_names=("review", "test"),
    )
    requested = ["test", "review", "test"]
    launched = manager.spawn(
        description="Run checks",
        prompt="check",
        allowed_skills=requested,
    )
    requested.append("later")
    _wait_for_notification(manager)

    assert factory_calls == [("test", "review")]
    assert manager._tasks[launched["task_id"]].allowed_skills == ("test", "review")
    clear_skills()


def test_invalid_skill_grant_rejects_before_factory_or_thread():
    clear_skills()
    _register_model_skill("review")
    factory_calls = []
    manager = WorkerManager(
        {"worker": lambda allowed: factory_calls.append(allowed)},
        grantable_skill_names=("review",),
    )

    try:
        manager.spawn(
            description="Bad grant",
            prompt="check",
            allowed_skills=["review", "missing"],
        )
    except ValueError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("invalid grant should fail")

    assert factory_calls == []
    assert manager._tasks == {}
    clear_skills()


def test_explore_rejects_non_empty_skill_grant():
    clear_skills()
    _register_model_skill("review")
    factory_calls = []
    manager = WorkerManager(
        {"Explore": lambda allowed: factory_calls.append(allowed)},
        grantable_skill_names=("review",),
    )

    try:
        manager.spawn(
            description="Explore",
            prompt="search",
            subagent_type="Explore",
            allowed_skills=["review"],
        )
    except ValueError as exc:
        assert "cannot receive" in str(exc)
    else:
        raise AssertionError("Explore grant should fail")

    assert factory_calls == []
    clear_skills()


def test_continuation_reuses_engine_and_authorization():
    clear_skills()
    _register_model_skill("review")
    engine = _FakeEngine("complete")
    manager = WorkerManager(
        {"worker": lambda allowed: engine},
        grantable_skill_names=("review",),
    )
    launched = manager.spawn(
        description="Review",
        prompt="first",
        allowed_skills=["review"],
    )
    _wait_for_notification(manager)

    manager.continue_task(task_id=launched["task_id"], message="use test too")
    _wait_for_notification(manager)

    task = manager._tasks[launched["task_id"]]
    assert task.engine is engine
    assert task.allowed_skills == ("review",)
    assert engine.prompts == ["first", "use test too"]
    clear_skills()


def test_registry_change_cannot_expand_or_preserve_disabled_grant():
    clear_skills()
    _register_model_skill("review")
    manager = WorkerManager(
        {"worker": lambda allowed: _FakeEngine("complete")},
        grantable_skill_names=("review",),
    )
    register_skill(Skill(
        name="later", model_invocable=True, allowed_tools_declared=True,
        _prompt_text="run",
    ))

    for requested in (["later"], ["review"]):
        if requested == ["review"]:
            register_skill(Skill(
                name="review", model_invocable=False,
                allowed_tools_declared=True, _prompt_text="run",
            ))
        try:
            manager.spawn(description="Invalid", prompt="run", allowed_skills=requested)
        except ValueError:
            pass
        else:
            raise AssertionError("registry changes must not expand authorization")

    assert manager._tasks == {}
    clear_skills()


def test_agent_tool_freezes_schema_and_forwards_skill_grant():
    manager = MagicMock()
    manager.spawn.return_value = {
        "task_id": "agent-1", "status": "started", "description": "Review",
    }
    tool = AgentTool(manager, grantable_skill_names=("review", "test"))

    assert tool.input_schema["properties"]["allowed_skills"]["items"]["enum"] == [
        "review", "test",
    ]
    result = tool.execute(
        description="Review",
        prompt="inspect",
        allowed_skills=["review"],
    )

    assert json.loads(result.content)["task_id"] == "agent-1"
    manager.spawn.assert_called_once_with(
        description="Review",
        prompt="inspect",
        subagent_type="worker",
        allowed_skills=["review"],
    )


def test_agent_tool_defaults_to_empty_grant_and_renders_errors():
    manager = MagicMock()
    manager.spawn.side_effect = ValueError("invalid grant")
    tool = AgentTool(manager, grantable_skill_names=("review",))

    result = tool.execute(description="Review", prompt="inspect")

    assert result.is_error
    assert "invalid grant" in result.content
    assert manager.spawn.call_args.kwargs["allowed_skills"] == []


def test_send_message_schema_has_no_skill_authorization_field():
    tool = SendMessageTool(MagicMock())
    assert set(tool.input_schema["properties"]) == {"to", "message"}
