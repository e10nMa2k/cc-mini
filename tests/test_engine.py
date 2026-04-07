from unittest.mock import MagicMock, patch
from core.engine import Engine
from core.config import default_max_tokens_for_model
from core.tool import Tool, ToolResult
from core.permissions import PermissionChecker


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
    from core.llm import LLMMessage, LLMUsage

    final_msg = LLMMessage(
        content=[{"type": "text", "text": text}],
        usage=LLMUsage(),
    )

    stream = MagicMock()
    stream.__enter__ = MagicMock(return_value=stream)
    stream.__exit__ = MagicMock(return_value=False)
    stream.text_stream = iter([text])
    stream.get_final_message = MagicMock(return_value=final_msg)
    return stream


def _make_tool_then_text_response(tool_name, tool_input, tool_use_id, text):
    """Simulate: first response has tool_use, second response has text."""
    from core.llm import LLMMessage, LLMUsage

    first_final = LLMMessage(
        content=[{
            "type": "tool_use",
            "id": tool_use_id,
            "name": tool_name,
            "input": tool_input,
        }],
        usage=LLMUsage(),
    )
    first_stream = MagicMock()
    first_stream.__enter__ = MagicMock(return_value=first_stream)
    first_stream.__exit__ = MagicMock(return_value=False)
    first_stream.text_stream = iter([])
    first_stream.get_final_message = MagicMock(return_value=first_final)

    second_stream = _make_text_response(text)
    return [first_stream, second_stream]


def test_engine_returns_text_events():
    engine = _make_engine()
    with patch.object(engine._client, "stream_messages", return_value=_make_text_response("hello")):
        events = list(engine.submit("hi"))
    text_events = [e for e in events if e[0] == "text"]
    assert any("hello" in e[1] for e in text_events)


def test_engine_executes_tool_and_loops():
    engine = _make_engine()
    streams = _make_tool_then_text_response("Echo", {"message": "world"}, "tu_1", "done")

    with patch.object(engine._client, "stream_messages", side_effect=streams):
        events = list(engine.submit("use the echo tool"))

    tool_result_events = [e for e in events if e[0] == "tool_result"]
    assert len(tool_result_events) == 1
    _, tool_name, _, result = tool_result_events[0]
    assert tool_name == "Echo"
    assert "Echo: world" in result.content


def test_engine_denied_tool_returns_error_result():
    engine = _make_engine(auto_approve=False)
    streams = _make_tool_then_text_response("Echo", {"message": "hi"}, "tu_2", "ok")

    with patch.object(engine._permissions, "_prompt_user", return_value="deny"):
        with patch.object(engine._client, "stream_messages", side_effect=streams):
            events = list(engine.submit("echo hi"))

    tool_result_events = [e for e in events if e[0] == "tool_result"]
    assert tool_result_events[0][3].is_error


def test_engine_unknown_tool_returns_error():
    engine = _make_engine()
    streams = _make_tool_then_text_response("UnknownTool", {}, "tu_3", "done")

    with patch.object(engine._client, "stream_messages", side_effect=streams):
        events = list(engine.submit("use unknown"))

    tool_result_events = [e for e in events if e[0] == "tool_result"]
    assert tool_result_events[0][3].is_error
    assert "Unknown tool" in tool_result_events[0][3].content


def test_engine_uses_model_specific_default_max_tokens():
    engine = Engine(
        tools=[EchoTool()],
        system_prompt="You are a test assistant.",
        permission_checker=PermissionChecker(auto_approve=True),
        model="claude-sonnet-4",
    )

    with patch.object(engine._client, "stream_messages", return_value=_make_text_response("hello")) as stream:
        list(engine.submit("hi"))

    assert stream.call_args.kwargs["model"] == "claude-sonnet-4"
    assert stream.call_args.kwargs["max_tokens"] == default_max_tokens_for_model("claude-sonnet-4")


def test_engine_normalizes_assistant_tool_use_blocks_before_retrying():
    engine = _make_engine()
    streams = _make_tool_then_text_response("Echo", {"message": "world"}, "tu_1", "done")

    with patch.object(engine._client, "stream_messages", side_effect=streams) as stream:
        list(engine.submit("use the echo tool"))

    second_messages = stream.call_args_list[1].kwargs["messages"]
    assistant_message = second_messages[1]
    assistant_block = assistant_message["content"][0]

    assert isinstance(assistant_block, dict)
    assert assistant_block == {
        "type": "tool_use",
        "id": "tu_1",
        "name": "Echo",
        "input": {"message": "world"},
    }


def test_engine_normalizes_tool_result_blocks_before_follow_up_request():
    engine = _make_engine()
    streams = _make_tool_then_text_response("Echo", {"message": "world"}, "tu_1", "done")

    with patch.object(engine._client, "stream_messages", side_effect=streams) as stream:
        list(engine.submit("use the echo tool"))

    second_messages = stream.call_args_list[1].kwargs["messages"]
    tool_result_message = second_messages[2]

    assert tool_result_message["content"] == [{
        "type": "tool_result",
        "tool_use_id": "tu_1",
        "content": "Echo: world",
        "is_error": False,
    }]
