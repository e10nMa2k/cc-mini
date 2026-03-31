from unittest.mock import MagicMock, patch
from mini_claude.engine import Engine
from mini_claude.tools.base import Tool, ToolResult
from mini_claude.permissions import PermissionChecker


class EchoTool(Tool):
    name = "Echo"
    description = "Returns the input message"
    input_schema = {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    }

    def execute(self, message: str) -> ToolResult:
        return ToolResult(content=f"Echo: {message}")


def _make_engine(auto_approve=True):
    return Engine(
        tools=[EchoTool()],
        system_prompt="You are a test assistant.",
        permission_checker=PermissionChecker(auto_approve=auto_approve),
    )


def _make_text_response(text: str):
    """Simulate an API response with just text (no tool calls)."""
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


def _make_tool_then_text_response(tool_name, tool_input, tool_use_id, text):
    """Simulate: first response has tool_use, second response has text."""
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.id = tool_use_id
    tool_block.name = tool_name
    tool_block.input = tool_input

    first_final = MagicMock()
    first_final.content = [tool_block]
    first_stream = MagicMock()
    first_stream.__enter__ = MagicMock(return_value=first_stream)
    first_stream.__exit__ = MagicMock(return_value=False)
    first_stream.text_stream = iter([])
    first_stream.get_final_message = MagicMock(return_value=first_final)

    second_stream = _make_text_response(text)
    return [first_stream, second_stream]


def test_engine_returns_text_events():
    engine = _make_engine()
    with patch.object(engine._client.messages, "stream", return_value=_make_text_response("hello")):
        events = list(engine.submit("hi"))
    text_events = [e for e in events if e[0] == "text"]
    assert any("hello" in e[1] for e in text_events)


def test_engine_executes_tool_and_loops():
    engine = _make_engine()
    streams = _make_tool_then_text_response("Echo", {"message": "world"}, "tu_1", "done")

    with patch.object(engine._client.messages, "stream", side_effect=streams):
        events = list(engine.submit("use the echo tool"))

    tool_result_events = [e for e in events if e[0] == "tool_result"]
    assert len(tool_result_events) == 1
    _, tool_name, _, result = tool_result_events[0]
    assert tool_name == "Echo"
    assert "Echo: world" in result.content


def test_engine_denied_tool_returns_error_result():
    engine = _make_engine(auto_approve=False)
    streams = _make_tool_then_text_response("Echo", {"message": "hi"}, "tu_2", "ok")

    with patch("builtins.input", return_value="n"):
        with patch.object(engine._client.messages, "stream", side_effect=streams):
            events = list(engine.submit("echo hi"))

    tool_result_events = [e for e in events if e[0] == "tool_result"]
    assert tool_result_events[0][3].is_error


def test_engine_unknown_tool_returns_error():
    engine = _make_engine()
    streams = _make_tool_then_text_response("UnknownTool", {}, "tu_3", "done")

    with patch.object(engine._client.messages, "stream", side_effect=streams):
        events = list(engine.submit("use unknown"))

    tool_result_events = [e for e in events if e[0] == "tool_result"]
    assert tool_result_events[0][3].is_error
    assert "Unknown tool" in tool_result_events[0][3].content
