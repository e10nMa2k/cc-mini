"""
core.swarm — cc-mini Agent Swarm

Implements claude-code-style recursive agent swarm:
  - A coordinator agent decomposes tasks and spawns parallel workers
  - Workers can further spawn sub-workers (tree-shaped recursion)
  - Background workers report completion via task-notification messages
  - SwarmSession transparently injects notifications into the main Engine

Quick start:

    from core.swarm import create_swarm

    swarm = create_swarm()  # reads API key from environment
    session = swarm.create_session()

    for event in session.submit("Refactor the src/auth/ module"):
        if event[0] == "text":
            print(event[1], end="", flush=True)

    # Block until all background agents finish, processing their notifications
    for event in session.wait_for_completion():
        if event[0] == "text":
            print(event[1], end="", flush=True)
"""
from __future__ import annotations

from .task import TaskRegistry, TaskState, TaskStatus
from .agent_runner import AgentRunner
from .coordinator import SwarmCoordinator, SwarmSession
from .tools import AgentTool, SendMessageTool, TaskStopTool, build_swarm_tools
from .prompts import get_coordinator_system_prompt, get_worker_system_prompt

__all__ = [
    # Data layer
    "TaskRegistry",
    "TaskState",
    "TaskStatus",
    # Execution layer
    "AgentRunner",
    # Coordination layer
    "SwarmCoordinator",
    "SwarmSession",
    # Tools
    "AgentTool",
    "SendMessageTool",
    "TaskStopTool",
    "build_swarm_tools",
    # Prompts
    "get_coordinator_system_prompt",
    "get_worker_system_prompt",
    # Factory
    "create_swarm",
]


class Swarm:
    """
    Single-configuration swarm context holding a shared registry and coordinator.
    Obtain an interactive session via create_session().
    """

    def __init__(
        self,
        registry: TaskRegistry,
        coordinator: SwarmCoordinator,
        provider: str = "anthropic",
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        max_depth: int = 3,
    ) -> None:
        self.registry = registry
        self.coordinator = coordinator
        self._provider = provider
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._max_depth = max_depth

    def create_session(
        self,
        extra_system_prompt: str | None = None,
        extra_tools=None,
    ) -> SwarmSession:
        """
        Create a SwarmSession pre-configured with swarm tools.

        Args:
            extra_system_prompt: Text appended after the coordinator system prompt.
            extra_tools:         Additional tools (e.g. AskUserQuestion) to include.
        """
        from core.engine import Engine
        from core.permissions import PermissionChecker
        from .tools import _get_base_tools

        swarm_tools = build_swarm_tools(
            registry=self.registry,
            coordinator=self.coordinator,
            provider=self._provider,
            model=self._model,
            api_key=self._api_key,
            base_url=self._base_url,
            max_depth=self._max_depth,
        )

        all_tools = _get_base_tools() + swarm_tools
        if extra_tools:
            all_tools += list(extra_tools)

        system = get_coordinator_system_prompt(
            worker_tools=[t.name for t in _get_base_tools()]
        )
        if extra_system_prompt:
            system = system + "\n\n" + extra_system_prompt

        engine = Engine(
            tools=all_tools,
            system_prompt=system,
            permission_checker=PermissionChecker(auto_approve=True),
            provider=self._provider,
            model=self._model,
            api_key=self._api_key,
            base_url=self._base_url,
        )

        return SwarmSession(engine=engine, coordinator=self.coordinator)


def create_swarm(
    provider: str = "anthropic",
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    max_depth: int = 3,
) -> Swarm:
    """
    Convenience factory: create and return a configured Swarm instance.

    Example:
        swarm = create_swarm()
        session = swarm.create_session()

        for event in session.submit("Refactor the auth module"):
            if event[0] == "text":
                print(event[1], end="")

        for event in session.wait_for_completion():
            if event[0] == "text":
                print(event[1], end="")
    """
    registry = TaskRegistry()
    coordinator = SwarmCoordinator(registry)
    return Swarm(
        registry=registry,
        coordinator=coordinator,
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        max_depth=max_depth,
    )
