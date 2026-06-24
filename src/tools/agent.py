from __future__ import annotations

import json
from typing import Iterable

from core.tool import Tool, ToolResult
from features.agents import BUILTIN_AGENT_DEFINITIONS
from features.agents.worker_manager import WorkerManager


def _build_agent_description() -> str:
    agent_list = "\n".join(
        f"- {d.agent_type}: {d.when_to_use} (Tools: {d.tools_description})"
        for d in BUILTIN_AGENT_DEFINITIONS
    )
    return (
        "Launch a new agent to handle complex, multi-step tasks. Each agent type has "
        "specific capabilities and tools available to it.\n\n"
        f"Available agent types and the tools they have access to:\n{agent_list}\n\n"
        "When using the Agent tool, specify a subagent_type parameter to select which "
        "agent type to use. If omitted, the general-purpose agent is used.\n\n"
        "When NOT to use the Agent tool:\n"
        "- If the target is already known, use the direct tool: Read for a known path, "
        "the Grep tool for a specific symbol or string. Reserve this tool for open-ended "
        "questions that span the codebase, or tasks that match an available agent type.\n\n"
        "Usage notes:\n"
        "- Always include a short description summarizing what the agent will do\n"
        "- Launch multiple agents concurrently whenever possible, to maximize performance; "
        "to do that, use a single message with multiple tool uses\n"
        "- When the agent is done, it will return a single message back to you. The result "
        "returned by the agent is not visible to the user. To show the user the result, you "
        "should send a text message back to the user with a concise summary of the result.\n"
        "- You can optionally run agents in the background using the run_in_background "
        "parameter. When an agent runs in the background, you will be automatically notified "
        "when it completes — do NOT sleep, poll, or proactively check on its progress. "
        "Continue with other work or respond to the user instead.\n"
        "- **Foreground vs background**: Use foreground (default) when you need the agent's "
        "results before you can proceed — e.g., research agents whose findings inform your "
        "next steps. Use background when you have genuinely independent work to do in parallel.\n"
        "- To continue a previously spawned agent, use SendMessage with the agent's ID or "
        "name as the `to` field — that resumes it with full context. A new Agent call starts "
        "a fresh agent with no memory of prior runs, so the prompt must be self-contained.\n"
        "- Clearly tell the agent whether you expect it to write code or just to do research "
        "(search, file reads, web fetches, etc.), since it is not aware of the user's intent\n"
        "- If the agent description mentions that it should be used proactively, then you "
        "should try your best to use it without the user having to ask for it first.\n"
        "- If the user specifies that they want you to run agents \"in parallel\", you MUST "
        "send a single message with multiple Agent tool use content blocks. For example, if "
        "you need to launch both a build-validator agent and a test-runner agent in parallel, "
        "send a single message with both tool calls.\n\n"
        "## Writing the prompt\n\n"
        "Brief the agent like a smart colleague who just walked into the room — it hasn't "
        "seen this conversation, doesn't know what you've tried, doesn't understand why this "
        "task matters.\n"
        "- Explain what you're trying to accomplish and why.\n"
        "- Describe what you've already learned or ruled out.\n"
        "- Give enough context about the surrounding problem that the agent can make judgment "
        "calls rather than just following a narrow instruction.\n"
        "- If you need a short response, say so (\"report in under 200 words\").\n"
        "- Lookups: hand over the exact command. Investigations: hand over the question — "
        "prescribed steps become dead weight when the premise is wrong.\n\n"
        "Terse command-style prompts produce shallow, generic work.\n\n"
        "**Never delegate understanding.** Don't write \"based on your findings, fix the bug\" "
        "or \"based on the research, implement it.\" Those phrases push synthesis onto the agent "
        "instead of doing it yourself. Write prompts that prove you understood: include file "
        "paths, line numbers, what specifically to change."
    )


class AgentTool(Tool):
    name = "Agent"
    description = _build_agent_description()

    def get_activity_description(self, **kwargs) -> str | None:
        desc = kwargs.get("description", "")
        return f"Running agent: {desc}" if desc else "Running agent…"

    def __init__(
        self,
        manager: WorkerManager,
        grantable_skill_names: Iterable[str] = (),
    ):
        self._manager = manager
        self._grantable_skill_names = tuple(dict.fromkeys(grantable_skill_names))
        self._input_schema = {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "Short (3-5 word) label for the agent task"},
                "prompt": {"type": "string", "description": "Self-contained instructions for the agent"},
                "subagent_type": {
                    "type": "string",
                    "enum": ["worker", "Explore"],
                    "default": "worker",
                    "description": "Agent type to use. 'worker' for general-purpose tasks; 'Explore' for fast read-only codebase exploration.",
                },
                "allowed_skills": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": list(self._grantable_skill_names),
                    },
                    "default": [],
                    "description": "Fixed model-invocable skill grant for a general worker",
                },
            },
            "required": ["description", "prompt"],
        }

    @property
    def input_schema(self) -> dict:
        return self._input_schema

    def execute(
        self,
        description: str,
        prompt: str,
        subagent_type: str = "worker",
        allowed_skills: list[str] | None = None,
    ) -> ToolResult:
        try:
            payload = self._manager.spawn(
                description=description,
                prompt=prompt,
                subagent_type=subagent_type,
                allowed_skills=allowed_skills or [],
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
