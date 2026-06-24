import json
from unittest.mock import MagicMock

import pytest

from features.skill_runner import SkillResult, SkillUsage
from features.skills import Skill, clear_skills, register_skill
from tools.skill import SkillTool


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_skills()
    yield
    clear_skills()


def _eligible(name="review", context="fork"):
    skill = Skill(
        name=name,
        context=context,
        model_invocable=True,
        allowed_tools_declared=True,
        _prompt_text="run",
    )
    register_skill(skill)
    return skill


def _result(status="completed"):
    return SkillResult(
        skill_name="review",
        status=status,
        summary="done",
        duration_ms=10,
        tool_uses=2,
        usage=SkillUsage(input_tokens=3, output_tokens=4),
        error=None if status == "completed" else "failed",
    )


def _tool(*, names=("review",), allowed=True, result=None):
    runner = MagicMock()
    runner.run.return_value = result or _result()
    snapshot_provider = MagicMock(return_value=[{"role": "user", "content": "request"}])
    tool = SkillTool(
        runner=runner,
        authorized_skill_names=names,
        snapshot_provider=snapshot_provider,
        mode_allows_execution=lambda: allowed,
    )
    return tool, runner, snapshot_provider


def test_schema_and_description_are_model_facing_and_stable():
    _eligible()
    tool, _, _ = _tool()
    assert "isolated" in tool.description
    assert tool.input_schema["required"] == ["skill_name"]
    assert tool.input_schema["properties"]["skill_name"]["enum"] == ["review"]
    assert "arguments" not in tool.input_schema["required"]

    _eligible("later")
    assert tool.input_schema["properties"]["skill_name"]["enum"] == ["review"]


def test_valid_execution_passes_snapshot_and_default_arguments():
    skill = _eligible(context="inline")
    tool, runner, snapshot_provider = _tool()
    result = tool.execute("review")
    assert not result.is_error
    payload = json.loads(result.content)
    assert payload["status"] == "completed"
    snapshot_provider.assert_called_once_with()
    runner.run.assert_called_once_with(
        skill,
        arguments="",
        parent_snapshot=[{"role": "user", "content": "request"}],
    )


def test_rejects_unauthorized_skill_without_running():
    _eligible("other")
    tool, runner, snapshot_provider = _tool()
    result = tool.execute("other")
    assert result.is_error
    runner.run.assert_not_called()
    snapshot_provider.assert_not_called()


def test_revalidates_registry_eligibility():
    skill = _eligible()
    tool, runner, _ = _tool()
    skill.disable_model_invocation = True
    result = tool.execute("review")
    assert result.is_error
    runner.run.assert_not_called()


def test_rejects_execution_when_mode_changes():
    _eligible()
    tool, runner, _ = _tool(allowed=False)
    result = tool.execute("review")
    assert result.is_error
    assert "current mode" in result.content
    runner.run.assert_not_called()


@pytest.mark.parametrize("status", ["failed", "timed_out", "aborted"])
def test_non_completed_structured_results_are_tool_errors(status):
    _eligible()
    tool, _, _ = _tool(result=_result(status))
    result = tool.execute("review", "focus")
    assert result.is_error
    assert json.loads(result.content)["status"] == status


def test_tool_is_serial_and_abort_delegates():
    tool, runner, _ = _tool()
    assert tool.is_read_only() is False
    assert tool.get_activity_description(skill_name="review") == "Running skill: review"
    tool.abort()
    runner.abort.assert_called_once_with()
