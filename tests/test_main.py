from unittest.mock import MagicMock, patch, PropertyMock
from core.engine import Engine, AbortedError
from core.tool import Tool, ToolResult
from core.permissions import PermissionChecker
from core.config import AppConfig
from features.skills import Skill, clear_skills, register_skill
from features.skills import build_skills_prompt_section
from features.coordinator import get_worker_system_prompt
from tui.app import _build_bound_engine, _install_skill_tool


class DummyTool(Tool):
    name = "Dummy"
    description = "A dummy tool for testing"
    input_schema = {
        "type": "object",
        "properties": {"msg": {"type": "string"}},
        "required": ["msg"],
    }

    def execute(self, msg: str) -> ToolResult:
        return ToolResult(content=f"got: {msg}")


def _make_text_stream(text: str):
    block = MagicMock()
    block.type = "text"
    block.text = text

    final_msg = MagicMock()
    final_msg.content = [block]

    stream = MagicMock()
    stream.__enter__ = MagicMock(return_value=stream)
    stream.__exit__ = MagicMock(return_value=False)
    stream.text_stream = iter([text])
    stream.get_final_message = MagicMock(return_value=final_msg)
    return stream


def _make_engine():
    return Engine(
        tools=[DummyTool()],
        system_prompt="test",
        permission_checker=PermissionChecker(auto_approve=True),
    )


class _FakeEscListener:
    """A no-op replacement for EscListener that doesn't touch the terminal."""
    pressed = False

    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def pause(self):
        pass

    def resume(self):
        pass

    def check_esc_nonblocking(self):
        return False


@patch("tui.query.EscListener", _FakeEscListener)
def test_run_query_prints_text(capsys):
    """run_query should print text events to stdout in print_mode."""
    from tui.query import run_query

    engine = _make_engine()
    with patch.object(engine._client, "stream_messages", return_value=_make_text_stream("hello world")):
        run_query(engine, "hi", print_mode=True)

    captured = capsys.readouterr()
    assert "hello world" in captured.out


@patch("tui.query.EscListener", _FakeEscListener)
def test_run_query_handles_tool_call_event():
    """run_query should display tool call info via rich console."""
    from tui.query import run_query

    engine = _make_engine()

    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.id = "tu_1"
    tool_block.name = "Dummy"
    tool_block.input = {"msg": "test"}

    first_final = MagicMock()
    first_final.content = [tool_block]
    first_stream = MagicMock()
    first_stream.__enter__ = MagicMock(return_value=first_stream)
    first_stream.__exit__ = MagicMock(return_value=False)
    first_stream.text_stream = iter([])
    first_stream.get_final_message = MagicMock(return_value=first_final)

    second_stream = _make_text_stream("done")

    with patch.object(engine._client, "stream_messages", side_effect=[first_stream, second_stream]):
        run_query(engine, "use tool", print_mode=True)


@patch("tui.query.EscListener", _FakeEscListener)
def test_run_query_handles_keyboard_interrupt():
    """run_query should gracefully handle KeyboardInterrupt."""
    from tui.query import run_query

    engine = _make_engine()

    def raise_interrupt(*a, **kw):
        raise KeyboardInterrupt()

    with patch.object(engine._client, "stream_messages", side_effect=raise_interrupt):
        run_query(engine, "hi", print_mode=True)
    # Should not propagate the exception


def _app_config():
    return AppConfig(
        provider="anthropic",
        api_key=None,
        base_url=None,
        model="claude-sonnet-4",
        max_tokens=1024,
    )


def _model_skill(name="review"):
    skill = Skill(
        name=name,
        model_invocable=True,
        allowed_tools_declared=True,
        context="fork",
        _prompt_text="run",
    )
    register_skill(skill)
    return skill


def test_bound_engine_returns_final_skill_tool_schema():
    clear_skills()
    skill = _model_skill()
    engine = _build_bound_engine(
        base_tools=[DummyTool()],
        system_prompt="test",
        permissions=PermissionChecker(auto_approve=True),
        app_config=_app_config(),
        cwd="/tmp/project",
        authorized_skills=(skill,),
    )

    assert list(engine._tools) == ["Dummy", "SkillTool"]
    assert engine._tools["SkillTool"].input_schema["properties"]["skill_name"]["enum"] == ["review"]
    clear_skills()


def test_bound_engine_omits_skill_tool_without_authorization():
    engine = _build_bound_engine(
        base_tools=[DummyTool()],
        system_prompt="test",
        permissions=PermissionChecker(auto_approve=True),
        app_config=_app_config(),
        cwd="/tmp/project",
    )
    assert list(engine._tools) == ["Dummy"]


def test_reinstalling_tools_removes_skill_tool_for_coordinator_mode():
    clear_skills()
    skill = _model_skill()
    permissions = PermissionChecker(auto_approve=True)
    config = _app_config()
    engine = _build_bound_engine(
        base_tools=[DummyTool()],
        system_prompt="test",
        permissions=permissions,
        app_config=config,
        cwd="/tmp/project",
        authorized_skills=(skill,),
    )

    _install_skill_tool(
        engine,
        base_tools=[DummyTool()],
        authorized_skills=(),
        permissions=permissions,
        app_config=config,
        cwd="/tmp/project",
        cost_tracker=None,
        mode_allows_execution=lambda: True,
    )

    assert "SkillTool" not in engine._tools
    clear_skills()


def test_worker_bound_engine_matches_exact_skill_grant():
    clear_skills()
    review = _model_skill("review")
    _model_skill("test")
    authorized = (review,)
    prompt = (
        "worker base\n\n"
        + build_skills_prompt_section(authorized)
        + "\n\n"
        + get_worker_system_prompt(("review",))
    )
    engine = _build_bound_engine(
        base_tools=[DummyTool()],
        system_prompt=prompt,
        permissions=PermissionChecker(auto_approve=True),
        app_config=_app_config(),
        cwd="/tmp/project",
        authorized_skills=authorized,
    )

    enum = engine._tools["SkillTool"].input_schema["properties"]["skill_name"]["enum"]
    assert enum == ["review"]
    assert "review:" in engine.system_prompt
    assert "test:" not in engine.system_prompt
    assert "fixed skill authorization is: review" in engine.system_prompt
    clear_skills()
