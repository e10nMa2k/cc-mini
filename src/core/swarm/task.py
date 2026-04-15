"""
task.py — TaskState dataclass + thread-safe TaskRegistry

Mirrors claude-code's LocalAgentTaskState / LocalAgentTask.tsx.
cc-mini uses a synchronous threading model, so threading.Lock replaces React state.
"""
from __future__ import annotations

import random
import string
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .agent_runner import AgentRunner

TaskStatus = Literal["running", "completed", "failed", "killed"]

_ALPHABET = string.ascii_lowercase + string.digits


def _generate_task_id() -> str:
    """Generate a task ID in 'a-xxxxxxxx' format, matching claude-code's 'a' prefix for local agents."""
    suffix = "".join(random.choices(_ALPHABET, k=8))
    return f"a-{suffix}"


@dataclass
class TaskState:
    """
    Snapshot of a single background agent task's state.
    Mirrors LocalAgentTaskState (LocalAgentTask.tsx).
    """
    task_id: str
    description: str
    status: TaskStatus
    runner: AgentRunner
    tool_use_id: str | None = None
    result: str | None = None          # Final text output on completion
    error: str | None = None           # Error message on failure
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    tool_use_count: int = 0
    notified: bool = False             # Guards against duplicate task-notification delivery


class TaskRegistry:
    """
    Thread-safe registry of all background agent tasks.
    Python equivalent of AppState.tasks (React state) in claude-code.

    All writes hold _lock; reads return snapshot copies.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, TaskState] = {}

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def register(
        self,
        runner: AgentRunner,
        description: str,
        tool_use_id: str | None = None,
    ) -> TaskState:
        """Register a new task and return its TaskState. Mirrors registerAsyncAgent()."""
        task_id = _generate_task_id()
        state = TaskState(
            task_id=task_id,
            description=description,
            status="running",
            runner=runner,
            tool_use_id=tool_use_id,
        )
        with self._lock:
            self._tasks[task_id] = state
        return state

    def complete(self, task_id: str, result: str) -> bool:
        """Mark a task as completed. Mirrors completeAgentTask()."""
        with self._lock:
            state = self._tasks.get(task_id)
            if state is None or state.status != "running":
                return False
            state.status = "completed"
            state.result = result
            state.end_time = time.time()
        return True

    def fail(self, task_id: str, error: str) -> bool:
        """Mark a task as failed. Mirrors failAgentTask()."""
        with self._lock:
            state = self._tasks.get(task_id)
            if state is None or state.status != "running":
                return False
            state.status = "failed"
            state.error = error
            state.end_time = time.time()
        return True

    def kill(self, task_id: str) -> bool:
        """Abort a running task. Mirrors killAsyncAgent()."""
        with self._lock:
            state = self._tasks.get(task_id)
            if state is None or state.status != "running":
                return False
            state.status = "killed"
            state.end_time = time.time()

        # Call abort outside the lock to avoid deadlock
        state.runner.stop()
        return True

    def mark_notified(self, task_id: str) -> bool:
        """Mark a task as already notified (idempotency guard). Mirrors the task.notified flag."""
        with self._lock:
            state = self._tasks.get(task_id)
            if state is None or state.notified:
                return False
            state.notified = True
        return True

    def increment_tool_use(self, task_id: str) -> None:
        with self._lock:
            state = self._tasks.get(task_id)
            if state is not None:
                state.tool_use_count += 1

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, task_id: str) -> TaskState | None:
        with self._lock:
            return self._tasks.get(task_id)

    def get_running(self) -> list[TaskState]:
        """Return a snapshot list of all running tasks."""
        with self._lock:
            return [s for s in self._tasks.values() if s.status == "running"]

    def get_all(self) -> list[TaskState]:
        with self._lock:
            return list(self._tasks.values())

    def kill_all_running(self) -> list[str]:
        """Stop all running tasks; return the list of killed task_ids."""
        running_ids = [s.task_id for s in self.get_running()]
        for task_id in running_ids:
            self.kill(task_id)
        return running_ids
