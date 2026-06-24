from __future__ import annotations

from typing import Callable, Iterable

from core.tool import Tool, ToolResult
from features.skill_runner import SkillRunner
from features.skills import get_skill, is_model_invocable


class SkillTool(Tool):
    """Model-facing adapter for isolated skill execution."""

    name = "SkillTool"
    description = (
        "Run an authorized reusable skill when its documented workflow matches "
        "the current task. Choose one skill_name from the schema and pass only "
        "the user-specific details as arguments. The skill runs in an isolated "
        "child agent with a restricted tool set and returns one structured result."
    )

    def __init__(
        self,
        *,
        runner: SkillRunner,
        authorized_skill_names: Iterable[str],
        snapshot_provider: Callable[[], list[dict]],
        mode_allows_execution: Callable[[], bool],
    ) -> None:
        self._runner = runner
        self._authorized_skill_names = tuple(dict.fromkeys(authorized_skill_names))
        self._authorized_skill_set = frozenset(self._authorized_skill_names)
        self._snapshot_provider = snapshot_provider
        self._mode_allows_execution = mode_allows_execution
        self._input_schema = {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "enum": list(self._authorized_skill_names),
                    "description": "Authorized skill to run",
                },
                "arguments": {
                    "type": "string",
                    "description": "Optional user-specific context or focus for the skill",
                },
            },
            "required": ["skill_name"],
        }

    @property
    def input_schema(self) -> dict:
        return self._input_schema

    def get_activity_description(self, **kwargs) -> str | None:
        skill_name = kwargs.get("skill_name", "")
        return f"Running skill: {skill_name}" if skill_name else "Running skill"

    def is_read_only(self) -> bool:
        return False

    def execute(self, skill_name: str, arguments: str = "") -> ToolResult:
        if skill_name not in self._authorized_skill_set:
            return ToolResult(
                content=f"Skill is not authorized for this agent: {skill_name}",
                is_error=True,
            )
        if not self._mode_allows_execution():
            return ToolResult(
                content="Skill execution is not allowed in the current mode.",
                is_error=True,
            )

        skill = get_skill(skill_name)
        if skill is None or not is_model_invocable(skill):
            return ToolResult(
                content=f"Skill is no longer eligible for model invocation: {skill_name}",
                is_error=True,
            )

        snapshot = self._snapshot_provider()
        result = self._runner.run(
            skill,
            arguments=arguments,
            parent_snapshot=snapshot,
        )
        return ToolResult(
            content=result.to_json(),
            is_error=result.status != "completed",
        )

    def abort(self) -> None:
        self._runner.abort()
