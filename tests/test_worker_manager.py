import time

from core.engine import AbortedError
from features.worker_manager import WorkerManager


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
    manager = WorkerManager(build_worker_engine=lambda: engine)

    launched = manager.spawn(description="Inspect", prompt="read the file")
    notification = _wait_for_notification(manager)

    assert launched["task_id"].startswith("agent-")
    assert "<status>completed</status>" in notification
    assert "finished:read the file" in notification
    assert "<tool_uses>1</tool_uses>" in notification
    assert "<total_tokens>7</total_tokens>" in notification


def test_worker_manager_can_continue_completed_task():
    engine = _FakeEngine("complete")
    manager = WorkerManager(build_worker_engine=lambda: engine)

    launched = manager.spawn(description="Inspect", prompt="first")
    _wait_for_notification(manager)

    manager.continue_task(task_id=launched["task_id"], message="second")
    _wait_for_notification(manager)

    assert engine.prompts == ["first", "second"]


def test_worker_manager_can_stop_running_task():
    engine = _FakeEngine("abortable")
    manager = WorkerManager(build_worker_engine=lambda: engine)

    launched = manager.spawn(description="Long task", prompt="wait")
    manager.stop_task(task_id=launched["task_id"])
    notification = _wait_for_notification(manager)

    assert "<status>killed</status>" in notification
