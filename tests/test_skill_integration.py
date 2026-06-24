from __future__ import annotations

import json
from unittest.mock import MagicMock

from core.engine import Engine
from core.llm import LLMMessage, LLMUsage
from core.permissions import PermissionChecker
from features.cost_tracker import CostTracker
from features.skill_runner import SkillResult, SkillRunner, SkillUsage
from features.skills import Skill, clear_skills, register_skill
from tools.file_write import FileWriteTool
from tools.skill import SkillTool


def _stream(content, text_chunks=(), usage=None):
    message = LLMMessage(
        content=content,
        usage=usage or LLMUsage(),
        stop_reason="tool_use" if any(b.get("type") == "tool_use" for b in content) else "end_turn",
    )
    stream = MagicMock()
    stream.__enter__ = MagicMock(return_value=stream)
    stream.__exit__ = MagicMock(return_value=False)
    stream.text_stream = iter(text_chunks)
    stream.get_final_message = MagicMock(return_value=message)
    return stream


def _eligible(name: str, *, context="inline", allowed_tools=None) -> Skill:
    skill = Skill(
        name=name,
        context=context,
        model_invocable=True,
        allowed_tools=list(allowed_tools or []),
        allowed_tools_declared=True,
        _prompt_text="run $ARGUMENTS",
    )
    register_skill(skill)
    return skill


class _SequentialRunner:
    def __init__(self, marker_path):
        self.marker_path = marker_path
        self.calls = []

    def run(self, skill, arguments="", parent_snapshot=None):
        self.calls.append((skill.name, parent_snapshot))
        if skill.name == "review":
            parent_snapshot[0]["content"] = "mutated child copy"
            self.marker_path.write_text("first completed")
        else:
            assert self.marker_path.read_text() == "first completed"
        return SkillResult(
            skill_name=skill.name,
            status="completed",
            summary=f"{skill.name} done",
            duration_ms=1,
            tool_uses=0,
            usage=SkillUsage(),
        )

    def abort(self):
        pass


def test_parent_executes_same_response_skills_serially_with_shared_boundary(tmp_path):
    clear_skills()
    _eligible("review")
    _eligible("test")
    runner = _SequentialRunner(tmp_path / "marker.txt")
    engine = Engine(
        tools=[],
        system_prompt="parent",
        permission_checker=PermissionChecker(auto_approve=True),
    )
    skill_tool = SkillTool(
        runner=runner,
        authorized_skill_names=("review", "test"),
        snapshot_provider=engine.get_pre_tool_use_snapshot,
        mode_allows_execution=lambda: True,
    )
    engine.set_tools([skill_tool])
    first = _stream([
        {
            "type": "tool_use", "id": "skill-1", "name": "SkillTool",
            "input": {"skill_name": "review"},
        },
        {
            "type": "tool_use", "id": "skill-2", "name": "SkillTool",
            "input": {"skill_name": "test"},
        },
    ])
    second = _stream(
        [{"type": "text", "text": "parent continued"}],
        text_chunks=("parent continued",),
    )
    engine._client.stream_messages = MagicMock(side_effect=[first, second])

    events = list(engine.submit("run both skills"))

    assert [name for name, _ in runner.calls] == ["review", "test"]
    assert runner.calls[0][1] is not runner.calls[1][1]
    assert runner.calls[1][1] == [{"role": "user", "content": "run both skills"}]
    tool_results = [event for event in events if event[0] == "tool_result"]
    assert len(tool_results) == 2
    assert all(json.loads(event[3].content)["status"] == "completed" for event in tool_results)
    assert [event[1] for event in events if event[0] == "text"] == ["parent continued"]
    clear_skills()


def test_failed_skill_result_is_one_tool_error_and_parent_loop_continues():
    clear_skills()
    _eligible("review", context="fork")
    runner = MagicMock()
    runner.run.return_value = SkillResult(
        skill_name="review",
        status="failed",
        summary="",
        duration_ms=2,
        tool_uses=0,
        usage=SkillUsage(),
        error="child provider failed",
    )
    engine = Engine(
        tools=[],
        system_prompt="parent",
        permission_checker=PermissionChecker(auto_approve=True),
    )
    engine.set_tools([SkillTool(
        runner=runner,
        authorized_skill_names=("review",),
        snapshot_provider=engine.get_pre_tool_use_snapshot,
        mode_allows_execution=lambda: True,
    )])
    engine._client.stream_messages = MagicMock(side_effect=[
        _stream([{
            "type": "tool_use", "id": "skill-1", "name": "SkillTool",
            "input": {"skill_name": "review"},
        }]),
        _stream(
            [{"type": "text", "text": "recovered"}],
            text_chunks=("recovered",),
        ),
    ])

    events = list(engine.submit("review"))

    results = [event for event in events if event[0] == "tool_result"]
    assert len(results) == 1
    assert results[0][3].is_error
    assert json.loads(results[0][3].content)["error"] == "child provider failed"
    assert any(event == ("text", "recovered") for event in events)
    clear_skills()


def test_child_usage_and_file_changes_are_accounted_once(tmp_path):
    clear_skills()
    skill = _eligible("write", context="fork", allowed_tools=["Write"])
    tracker = CostTracker()
    target = tmp_path / "created.txt"

    def factory(**kwargs):
        child = Engine(provider="anthropic", **kwargs)
        child._client.stream_messages = MagicMock(side_effect=[
            _stream(
                [{
                    "type": "tool_use", "id": "write-1", "name": "Write",
                    "input": {"file_path": str(target), "content": "one line\n"},
                }],
                usage=LLMUsage(input_tokens=10, output_tokens=2),
            ),
            _stream(
                [{"type": "text", "text": "written"}],
                text_chunks=("written",),
                usage=LLMUsage(input_tokens=3, output_tokens=1),
            ),
        ])
        return child

    runner = SkillRunner(
        engine_factory=factory,
        caller_tools=[FileWriteTool()],
        permission_checker=PermissionChecker(auto_approve=True),
        cwd=str(tmp_path),
        default_model="claude-sonnet-4",
        cost_tracker=tracker,
    )

    result = runner.run(skill)

    assert result.status == "completed"
    assert result.usage == SkillUsage(input_tokens=13, output_tokens=3)
    usage = tracker._model_usage["claude-sonnet-4"]
    assert usage.input_tokens == 13
    assert usage.output_tokens == 3
    assert tracker._lines_added == 1
    assert tracker._lines_removed == 0
    assert target.read_text() == "one line\n"
    clear_skills()


class _DenyChecker(PermissionChecker):
    def __init__(self):
        super().__init__(auto_approve=False)

    def fork(self):
        return _DenyChecker()

    def _prompt_user(self, tool, inputs):
        return "deny"


def test_child_permission_denial_remains_active(tmp_path):
    clear_skills()
    skill = _eligible("write", context="fork", allowed_tools=["Write"])
    target = tmp_path / "denied.txt"

    def factory(**kwargs):
        child = Engine(provider="anthropic", **kwargs)
        child._client.stream_messages = MagicMock(side_effect=[
            _stream([{
                "type": "tool_use", "id": "write-1", "name": "Write",
                "input": {"file_path": str(target), "content": "blocked"},
            }]),
            _stream(
                [{"type": "text", "text": "permission was denied"}],
                text_chunks=("permission was denied",),
            ),
        ])
        return child

    runner = SkillRunner(
        engine_factory=factory,
        caller_tools=[FileWriteTool()],
        permission_checker=_DenyChecker(),
        cwd=str(tmp_path),
        default_model="claude-sonnet-4",
    )

    result = runner.run(skill)

    assert result.status == "completed"
    assert result.summary == "permission was denied"
    assert not target.exists()
    clear_skills()
