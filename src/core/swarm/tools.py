"""
tools.py — AgentTool / SendMessageTool / TaskStopTool

The three tools that form the public interface layer of the cc-mini swarm.

Mirrors in claude-code:
  AgentTool.tsx   → AgentTool
  SendMessageTool → SendMessageTool
  TaskStopTool    → TaskStopTool

Design constraints:
  - Inherits core.tool.Tool (synchronous ABC)
  - execute(**kwargs) returns ToolResult
  - AgentTool supports synchronous mode (blocks until result) and
    background mode (run_in_background=True)
  - build_swarm_tools() is the sole assembly point; external callers
    should use only this factory
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from core.tool import Tool, ToolResult
from core.config import resolve_model
from core.permissions import PermissionChecker
from .agent_runner import AgentRunner
from .coordinator import SwarmCoordinator
from .prompts import get_worker_system_prompt
from .task import TaskRegistry

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# AgentTool
# ---------------------------------------------------------------------------

class AgentTool(Tool):
    """
    Spawn a child agent to handle a subtask.

    Synchronous mode (run_in_background=False, default):
      Runs the child Engine in the calling thread, blocking until completion.
      Returns the agent's text output directly.
      Mirrors claude-code's sync agent path (!shouldRunAsync).

    Background mode (run_in_background=True):
      Launches a background thread and returns an async_launched confirmation
      immediately. On completion the agent fires a task-notification via
      SwarmCoordinator.
      Mirrors claude-code's async agent path (shouldRunAsync=true).

    Recursion:
      The child Engine is provisioned with its own AgentTool (when
      allow_subagents=True), enabling further sub-worker spawning and forming
      a tree-shaped swarm. Depth is capped at max_depth.
      Leaf agents (e.g. verifier) set allow_subagents=False to cut recursion.
    """

    def __init__(
        self,
        registry: TaskRegistry,
        coordinator: SwarmCoordinator,
        provider: str = "anthropic",
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        allow_subagents: bool = True,               # False = leaf node, no recursion
        disallowed_subtypes: set[str] | None = None, # Subtypes blocked from spawning
        max_depth: int = 3,                          # Maximum recursion depth
        _current_depth: int = 0,                     # Internal depth counter
    ) -> None:
        self._registry = registry
        self._coordinator = coordinator
        self._provider = provider
        self._model = resolve_model(model, provider=provider)
        self._api_key = api_key
        self._base_url = base_url
        self._allow_subagents = allow_subagents
        self._disallowed_subtypes = disallowed_subtypes or set()
        self._max_depth = max_depth
        self._current_depth = _current_depth

    @property
    def name(self) -> str:
        return "Agent"

    @property
    def description(self) -> str:
        return (
            "Spawn a new agent to handle a subtask. "
            "Use run_in_background=true to run async and continue working. "
            "Results arrive as <task-notification> messages."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Short (3-5 word) description of the task",
                },
                "prompt": {
                    "type": "string",
                    "description": (
                        "Self-contained task prompt for the agent. "
                        "Include all necessary context — the agent cannot see the conversation history."
                    ),
                },
                "subagent_type": {
                    "type": "string",
                    "description": "Agent specialization: general-purpose, researcher, implementer, verifier",
                    "enum": ["general-purpose", "researcher", "implementer", "verifier"],
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": "Run async in background. You will be notified on completion.",
                },
                "timeout_s": {
                    "type": "number",
                    "description": (
                        "Optional wall-clock timeout in seconds for background agents. "
                        "The agent is aborted and marked killed if it exceeds this limit."
                    ),
                },
            },
            "required": ["description", "prompt"],
        }

    def is_read_only(self) -> bool:
        return False

    def get_activity_description(self, **kwargs) -> str | None:
        desc = kwargs.get("description", "")
        return f"Spawning agent: {desc}" if desc else "Spawning agent"

    def execute(
        self,
        description: str,
        prompt: str,
        subagent_type: str = "general-purpose",
        run_in_background: bool = False,
        timeout_s: float | None = None,
        **kwargs,
    ) -> ToolResult:
        # Recursion depth guard
        if self._current_depth >= self._max_depth:
            return ToolResult(
                content=f"Cannot spawn agent: maximum recursion depth ({self._max_depth}) reached.",
                is_error=True,
            )

        # Leaf node or explicitly blocked subtype
        if not self._allow_subagents:
            return ToolResult(
                content="Agent spawning is disabled for this agent type.",
                is_error=True,
            )
        if subagent_type in self._disallowed_subtypes:
            return ToolResult(
                content=f"Agent type '{subagent_type}' is not allowed from this context.",
                is_error=True,
            )

        engine = self._build_child_engine(subagent_type)

        if run_in_background:
            return self._spawn_background(engine, description, prompt, timeout_s)
        else:
            return self._spawn_sync(engine, description, prompt)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_child_engine(self, subagent_type: str):
        """Construct the child Engine, provisioned with a recursive AgentTool when allowed."""
        from core.engine import Engine

        # verifier agents are leaf nodes — no further recursion
        child_allow_subagents = (
            self._allow_subagents and subagent_type != "verifier"
        )
        child_disallowed = set(self._disallowed_subtypes)
        if subagent_type == "verifier":
            child_disallowed.add("verifier")

        child_tools: list[Tool] = list(_get_base_tools())
        if child_allow_subagents:
            child_tools.append(AgentTool(
                registry=self._registry,
                coordinator=self._coordinator,
                provider=self._provider,
                model=self._model,
                api_key=self._api_key,
                base_url=self._base_url,
                allow_subagents=child_allow_subagents,
                disallowed_subtypes=child_disallowed,
                max_depth=self._max_depth,
                _current_depth=self._current_depth + 1,
            ))
            child_tools.append(SendMessageTool(self._registry))
            child_tools.append(TaskStopTool(self._registry))

        system_prompt = get_worker_system_prompt(
            agent_type=subagent_type,
            can_spawn_subagents=child_allow_subagents,
        )

        return Engine(
            tools=child_tools,
            system_prompt=system_prompt,
            permission_checker=PermissionChecker(auto_approve=True),
            provider=self._provider,
            model=self._model,
            api_key=self._api_key,
            base_url=self._base_url,
        )

    def _spawn_sync(self, engine, description: str, prompt: str) -> ToolResult:
        """
        Execute the child agent in the calling thread and return its result.
        Mirrors claude-code's sync agent path (!shouldRunAsync).
        """
        result_parts: list[str] = []
        error_msg: str | None = None

        try:
            for event in engine.submit(prompt):
                kind = event[0]
                if kind == "text":
                    result_parts.append(event[1])
                elif kind == "error":
                    error_msg = event[1]
        except Exception as exc:
            return ToolResult(
                content=f"Agent '{description}' failed: {exc}",
                is_error=True,
            )

        result = "".join(result_parts)
        if error_msg and not result:
            return ToolResult(content=f"Agent error: {error_msg}", is_error=True)

        content = (
            f"Agent '{description}' completed.\n\n{result}"
            if result
            else f"Agent '{description}' completed (no output)."
        )
        return ToolResult(content=content)

    def _spawn_background(
        self,
        engine,
        description: str,
        prompt: str,
        timeout_s: float | None,
    ) -> ToolResult:
        """
        Launch the child agent in a background thread and return immediately.
        The coordinator receives a task-notification when the agent finishes.
        Mirrors claude-code's async agent path (shouldRunAsync=true).
        """
        runner = AgentRunner(engine)
        state = self._registry.register(
            runner=runner,
            description=description,
        )
        task_id = state.task_id

        def on_done(tid: str, result: str | None, error: str | None) -> None:
            self._coordinator.on_task_done(tid, result, error)

        runner.start(task_id=task_id, prompt=prompt, on_done=on_done, timeout_s=timeout_s)

        return ToolResult(
            content=(
                f"Async agent launched successfully.\n"
                f"task_id: {task_id}\n"
                f"description: {description}\n"
                f"The agent is working in the background. "
                f"You will be notified automatically when it completes.\n"
                f"Do not duplicate this agent's work. "
                f"Briefly tell the user what you launched and end your response."
            )
        )


# ---------------------------------------------------------------------------
# SendMessageTool
# ---------------------------------------------------------------------------

class SendMessageTool(Tool):
    """
    Send a follow-up message to a running background agent.
    The agent processes the message after completing its current tool round.
    Mirrors claude-code's SendMessageTool / queuePendingMessage().
    """

    def __init__(self, registry: TaskRegistry) -> None:
        self._registry = registry

    @property
    def name(self) -> str:
        return "SendMessage"

    @property
    def description(self) -> str:
        return (
            "Send a follow-up message to a running agent. "
            "Use the task_id from a previous Agent call or task-notification. "
            "The agent will process this message after its current work."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "task_id of the target agent",
                },
                "message": {
                    "type": "string",
                    "description": "Message to send to the agent",
                },
            },
            "required": ["to", "message"],
        }

    def is_read_only(self) -> bool:
        return False

    def get_activity_description(self, **kwargs) -> str | None:
        to = kwargs.get("to", "")
        return f"Sending message to agent {to}" if to else "Sending agent message"

    def execute(self, to: str, message: str, **kwargs) -> ToolResult:
        state = self._registry.get(to)
        if state is None:
            return ToolResult(
                content=f"Agent '{to}' not found.",
                is_error=True,
            )
        if state.status != "running":
            return ToolResult(
                content=f"Agent '{to}' is not running (status: {state.status}).",
                is_error=True,
            )

        state.runner.send_message(message)
        return ToolResult(content=f"Message sent to agent '{to}'.")


# ---------------------------------------------------------------------------
# TaskStopTool
# ---------------------------------------------------------------------------

class TaskStopTool(Tool):
    """
    Stop a running background agent.
    Mirrors claude-code's TaskStopTool / killAsyncAgent().
    """

    def __init__(self, registry: TaskRegistry) -> None:
        self._registry = registry

    @property
    def name(self) -> str:
        return "TaskStop"

    @property
    def description(self) -> str:
        return (
            "Stop a running agent. "
            "Use when you realize the agent is going in the wrong direction, "
            "or when the user changes requirements."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "task_id of the agent to stop",
                },
            },
            "required": ["task_id"],
        }

    def is_read_only(self) -> bool:
        return False

    def get_activity_description(self, **kwargs) -> str | None:
        task_id = kwargs.get("task_id", "")
        return f"Stopping agent {task_id}" if task_id else "Stopping agent"

    def execute(self, task_id: str, **kwargs) -> ToolResult:
        stopped = self._registry.kill(task_id)
        if stopped:
            return ToolResult(content=f"Agent '{task_id}' stopped.")
        state = self._registry.get(task_id)
        if state is None:
            return ToolResult(
                content=f"Agent '{task_id}' not found.",
                is_error=True,
            )
        return ToolResult(
            content=f"Agent '{task_id}' is already in terminal state: {state.status}."
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_swarm_tools(
    registry: TaskRegistry,
    coordinator: SwarmCoordinator,
    provider: str = "anthropic",
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    max_depth: int = 3,
) -> list[Tool]:
    """
    Return the three swarm tools: AgentTool, SendMessageTool, TaskStopTool.

    Typically merged with base tools before passing to the coordinator Engine:

        base_tools  = _get_base_tools()   # Read, Write, Bash, ...
        swarm_tools = build_swarm_tools(registry, coordinator, ...)
        engine = Engine(tools=base_tools + swarm_tools, ...)
    """
    agent_tool = AgentTool(
        registry=registry,
        coordinator=coordinator,
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        allow_subagents=True,
        max_depth=max_depth,
        _current_depth=0,
    )
    return [
        agent_tool,
        SendMessageTool(registry),
        TaskStopTool(registry),
    ]


def _get_base_tools() -> list[Tool]:
    """
    Load the standard base tool set (Read, Write, Edit, Bash, Glob, Grep).
    Returns an empty list if the tools module is unavailable.
    """
    try:
        from tools.file_read import FileReadTool
        from tools.file_write import FileWriteTool
        from tools.file_edit import FileEditTool
        from tools.bash import BashTool
        from tools.glob_tool import GlobTool
        from tools.grep_tool import GrepTool
        return [
            FileReadTool(),
            FileWriteTool(),
            FileEditTool(),
            BashTool(),
            GlobTool(),
            GrepTool(),
        ]
    except ImportError:
        return []
