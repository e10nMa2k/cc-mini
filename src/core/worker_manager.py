from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from queue import Empty, Queue
from typing import Callable
from xml.sax.saxutils import escape

from .engine import AbortedError


@dataclass
class WorkerUsage:
    total_tokens: int = 0
    tool_uses: int = 0
    duration_ms: int = 0


@dataclass
class WorkerTask:
    task_id: str
    description: str
    engine: object
    status: str = "idle"
    summary: str = ""
    result: str = ""
    usage: WorkerUsage = field(default_factory=WorkerUsage)
    thread: threading.Thread | None = None
    # Live progress tracking
    tool_use_count: int = 0
    current_activity: str = ""


class WorkerManager:
    def __init__(self, build_worker_engine: Callable[[], object]):
        self._build_worker_engine = build_worker_engine
        self._tasks: dict[str, WorkerTask] = {}
        self._lock = threading.Lock()
        self._notifications: Queue[str] = Queue()

    def spawn(
        self,
        *,
        description: str,
        prompt: str,
        subagent_type: str = "worker",
    ) -> dict[str, str]:
        if subagent_type != "worker":
            raise ValueError("Only subagent_type='worker' is supported.")

        task = WorkerTask(
            task_id=f"agent-{uuid.uuid4().hex[:8]}",
            description=description.strip() or "Worker task",
            engine=self._build_worker_engine(),
        )
        with self._lock:
            self._tasks[task.task_id] = task
        self._start(task, prompt)
        return {
            "task_id": task.task_id,
            "status": "started",
            "description": task.description,
        }

    def continue_task(self, *, task_id: str, message: str) -> dict[str, str]:
        task = self._get_task(task_id)
        if self._is_running(task):
            raise ValueError("Task is still running. Wait for it to finish before continuing it.")
        self._start(task, message)
        return {
            "task_id": task.task_id,
            "status": "started",
            "description": task.description,
        }

    def stop_task(self, *, task_id: str) -> dict[str, str]:
        task = self._get_task(task_id)
        if not self._is_running(task):
            return {
                "task_id": task.task_id,
                "status": task.status or "idle",
                "description": task.description,
            }
        try:
            task.engine.abort()
        except Exception:
            pass
        return {
            "task_id": task.task_id,
            "status": "stopping",
            "description": task.description,
        }

    def drain_notifications(self) -> list[str]:
        drained: list[str] = []
        while True:
            try:
                drained.append(self._notifications.get_nowait())
            except Empty:
                return drained

    def has_running_tasks(self) -> bool:
        with self._lock:
            return any(self._is_running(task) for task in self._tasks.values())

    def get_running_status(self) -> list[dict]:
        """Return status of all running workers for live display."""
        with self._lock:
            return [
                {
                    "task_id": t.task_id,
                    "description": t.description,
                    "tool_uses": t.tool_use_count,
                    "activity": t.current_activity,
                }
                for t in self._tasks.values()
                if self._is_running(t)
            ]

    def _get_task(self, task_id: str) -> WorkerTask:
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            raise ValueError(f"Unknown task id: {task_id}")
        return task

    @staticmethod
    def _is_running(task: WorkerTask) -> bool:
        return task.thread is not None and task.thread.is_alive()

    def _start(self, task: WorkerTask, prompt: str) -> None:
        task.status = "running"
        task.summary = ""
        task.result = ""
        task.usage = WorkerUsage()
        task.thread = threading.Thread(
            target=self._run_task,
            name=task.task_id,
            args=(task, prompt),
            daemon=True,
        )
        task.thread.start()

    def _run_task(self, task: WorkerTask, prompt: str) -> None:
        started = time.monotonic()
        parts: list[str] = []
        total_tokens = 0
        tool_uses = 0
        task.tool_use_count = 0
        task.current_activity = "Initializing…"
        try:
            for event in task.engine.submit(prompt):
                kind = event[0]
                if kind == "text":
                    parts.append(event[1])
                    task.current_activity = "Thinking…"
                elif kind == "tool_call":
                    tool_uses += 1
                    task.tool_use_count = tool_uses
                    tool_name = event[1] if len(event) > 1 else ""
                    task.current_activity = f"Running {tool_name}…"
                elif kind == "tool_result":
                    task.current_activity = "Thinking…"
                elif kind == "usage":
                    usage = event[1]
                    total_tokens += (
                        int(getattr(usage, "input_tokens", 0) or 0)
                        + int(getattr(usage, "output_tokens", 0) or 0)
                        + int(getattr(usage, "cache_read_input_tokens", 0) or 0)
                        + int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
                    )
                elif kind == "error":
                    parts.append(event[1])
            status = "completed"
            summary = f'Agent "{task.description}" completed'
        except AbortedError:
            status = "killed"
            summary = f'Agent "{task.description}" was stopped'
        except Exception as exc:
            status = "failed"
            summary = f'Agent "{task.description}" failed: {exc}'
            parts.append(str(exc))

        task.status = status
        task.summary = summary
        task.current_activity = ""
        task.result = "".join(parts).strip()
        task.usage = WorkerUsage(
            total_tokens=total_tokens,
            tool_uses=tool_uses,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        self._notifications.put(self._render_notification(task))

    def _render_notification(self, task: WorkerTask) -> str:
        parts = [
            "<task-notification>",
            f"<task-id>{escape(task.task_id)}</task-id>",
            f"<status>{escape(task.status)}</status>",
            f"<summary>{escape(task.summary)}</summary>",
        ]
        if task.result:
            parts.append(f"<result>{escape(task.result)}</result>")
        parts.extend(
            [
                "<usage>",
                f"  <total_tokens>{task.usage.total_tokens}</total_tokens>",
                f"  <tool_uses>{task.usage.tool_uses}</tool_uses>",
                f"  <duration_ms>{task.usage.duration_ms}</duration_ms>",
                "</usage>",
                "</task-notification>",
            ]
        )
        return "\n".join(parts)
