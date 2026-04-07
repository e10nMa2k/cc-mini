from __future__ import annotations

import json

from core.tool import Tool, ToolResult
from features.worker_manager import WorkerManager


class AgentTool(Tool):
    name = "Agent"
    description = (
        "Spawn a background worker for research, implementation, or "
        "verification. Returns immediately with a task_id. Final results "
        "arrive later as a <task-notification> user message."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": "Short label for the worker task"},
            "prompt": {"type": "string", "description": "Self-contained instructions for the worker"},
            "subagent_type": {
                "type": "string",
                "enum": ["worker"],
                "default": "worker",
                "description": "Only 'worker' is currently supported",
            },
        },
        "required": ["description", "prompt"],
    }

    def get_activity_description(self, **kwargs) -> str | None:
        desc = kwargs.get("description", "")
        return f"Running agent: {desc}" if desc else "Running agent…"

    def __init__(self, manager: WorkerManager):
        self._manager = manager

    def execute(
        self,
        description: str,
        prompt: str,
        subagent_type: str = "worker",
    ) -> ToolResult:
        try:
            payload = self._manager.spawn(
                description=description,
                prompt=prompt,
                subagent_type=subagent_type,
            )
        except ValueError as exc:
            return ToolResult(content=f"Error: {exc}", is_error=True)
        return ToolResult(content=json.dumps(payload, ensure_ascii=False))


class SendMessageTool(Tool):
    name = "SendMessage"
    description = (
        "Continue an existing idle worker by task_id. Use this after a worker "
        "has already reported back and you want it to take another step."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Worker task id to continue"},
            "message": {"type": "string", "description": "Next self-contained instruction"},
        },
        "required": ["to", "message"],
    }

    def __init__(self, manager: WorkerManager):
        self._manager = manager

    def execute(self, to: str, message: str) -> ToolResult:
        try:
            payload = self._manager.continue_task(task_id=to, message=message)
        except ValueError as exc:
            return ToolResult(content=f"Error: {exc}", is_error=True)
        return ToolResult(content=json.dumps(payload, ensure_ascii=False))


class TaskStopTool(Tool):
    name = "TaskStop"
    description = "Stop a running worker by task_id."
    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Worker task id"},
        },
        "required": ["task_id"],
    }

    def __init__(self, manager: WorkerManager):
        self._manager = manager

    def execute(self, task_id: str) -> ToolResult:
        try:
            payload = self._manager.stop_task(task_id=task_id)
        except ValueError as exc:
            return ToolResult(content=f"Error: {exc}", is_error=True)
        return ToolResult(content=json.dumps(payload, ensure_ascii=False))
