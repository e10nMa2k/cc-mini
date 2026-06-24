from unittest.mock import MagicMock, patch
import threading
import pytest
from core.engine import AbortedError, Engine
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


def _make_tools_response(tool_uses):
    from core.llm import LLMMessage, LLMUsage

    first_final = LLMMessage(content=tool_uses, usage=LLMUsage())
    first_stream = MagicMock()
    first_stream.__enter__ = MagicMock(return_value=first_stream)
    first_stream.__exit__ = MagicMock(return_value=False)
    first_stream.text_stream = iter([])
    first_stream.get_final_message = MagicMock(return_value=first_final)
    return first_stream


def test_engine_returns_text_events():
    engine = _make_engine()
    with patch.object(engine._client, "stream_messages", return_value=_make_text_response("hello")):
        events = list(engine.submit("hi"))
    text_events = [e for e in events if e[0] == "text"]
    assert any("hello" in e[1] for e in text_events)


def test_engine_exposes_configured_client_as_read_only_property():
    configured_client = MagicMock()
    with patch("core.engine.LLMClient", return_value=configured_client):
        engine = _make_engine()

    assert engine.client is configured_client
    with pytest.raises(AttributeError):
        engine.client = MagicMock()


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


def test_pre_tool_use_snapshot_excludes_current_assistant_response():
    observed = []

    class SnapshotTool(EchoTool):
        def execute(self, message: str) -> ToolResult:
            observed.append(engine.get_pre_tool_use_snapshot())
            assert engine.get_messages()[-1]["role"] == "assistant"
            return super().execute(message)

    engine = Engine(
        tools=[SnapshotTool()],
        system_prompt="test",
        permission_checker=PermissionChecker(auto_approve=True),
    )
    streams = _make_tool_then_text_response("Echo", {"message": "hi"}, "tu_1", "done")

    with patch.object(engine._client, "stream_messages", side_effect=streams):
        list(engine.submit("current request"))

    assert observed == [[{"role": "user", "content": "current request"}]]


def test_pre_tool_use_snapshot_is_deep_copied_and_stable_for_same_response():
    snapshots = []

    class SnapshotTool(Tool):
        name = "Snapshot"
        description = "capture snapshot"
        input_schema = {"type": "object", "properties": {}}

        def execute(self) -> ToolResult:
            snapshots.append(engine.get_pre_tool_use_snapshot())
            return ToolResult(content="ok")

    engine = _engine_with_tools([SnapshotTool()])
    engine.set_messages([{
        "role": "assistant",
        "content": [{"type": "text", "text": "earlier"}],
    }])
    streams = [
        _make_tools_response([
            {"type": "tool_use", "id": "tu_1", "name": "Snapshot", "input": {}},
            {"type": "tool_use", "id": "tu_2", "name": "Snapshot", "input": {}},
        ]),
        _make_text_response("done"),
    ]

    with patch.object(engine._client, "stream_messages", side_effect=streams):
        list(engine.submit("request"))

    assert snapshots[0] == snapshots[1]
    assert snapshots[0] is not snapshots[1]
    snapshots[0][0]["content"][0]["text"] = "mutated"
    assert snapshots[1][0]["content"][0]["text"] == "earlier"
    assert engine.get_messages()[0]["content"][0]["text"] == "earlier"


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


# ---------------------------------------------------------------------------
# 401 / 402 / 429 error handling
# ---------------------------------------------------------------------------

def test_engine_handles_auth_error_401():
    """401 authentication error stops the conversation with an error event."""
    engine = _make_engine()
    exc = Exception("Invalid API key")

    with patch.object(engine._client, "stream_messages", side_effect=exc), \
         patch.object(engine._client, "is_authentication_error", return_value=True):
        events = list(engine.submit("hi"))

    error_events = [e for e in events if e[0] == "error"]
    assert any("Authentication" in e[1] for e in error_events)


def test_engine_handles_payment_required_402():
    """402 payment-required is a non-retryable API error."""
    engine = _make_engine()
    exc = Exception("Insufficient credits")

    with patch.object(engine._client, "stream_messages", side_effect=exc), \
         patch.object(engine._client, "is_authentication_error", return_value=False), \
         patch.object(engine._client, "is_retryable_error", return_value=False), \
         patch.object(engine._client, "is_api_error", return_value=True):
        events = list(engine.submit("hi"))

    error_events = [e for e in events if e[0] == "error"]
    assert any("API error" in e[1] for e in error_events)


def test_engine_handles_rate_limit_429():
    """429 rate-limit triggers retry then succeeds."""
    engine = _make_engine()
    exc = Exception("Rate limited")
    text_stream = _make_text_response("recovered")

    with patch.object(engine._client, "stream_messages", side_effect=[exc, text_stream]), \
         patch.object(engine._client, "is_authentication_error", return_value=False), \
         patch.object(engine._client, "is_retryable_error", side_effect=lambda e: e is exc), \
         patch.object(engine._client, "is_api_error", return_value=False), \
         patch("core.engine.time.sleep"):
        events = list(engine.submit("hi"))

    text_events = [e for e in events if e[0] == "text"]
    error_events = [e for e in events if e[0] == "error"]
    assert any("recovered" in e[1] for e in text_events)
    assert any("retrying" in e[1].lower() for e in error_events)


# ---------------------------------------------------------------------------
# finish_reason validation
# ---------------------------------------------------------------------------

def test_engine_warns_on_max_tokens_finish_reason():
    """Engine emits an error event when stop_reason is max_tokens."""
    engine = _make_engine()
    from core.llm import LLMMessage, LLMUsage

    final_msg = LLMMessage(
        content=[{"type": "text", "text": "truncated"}],
        usage=LLMUsage(),
        stop_reason="max_tokens",
    )
    stream = MagicMock()
    stream.__enter__ = MagicMock(return_value=stream)
    stream.__exit__ = MagicMock(return_value=False)
    stream.text_stream = iter(["truncated"])
    stream.get_final_message = MagicMock(return_value=final_msg)

    with patch.object(engine._client, "stream_messages", return_value=stream):
        events = list(engine.submit("hi"))

    error_events = [e for e in events if e[0] == "error"]
    assert any("max_tokens" in e[1].lower() or "truncated" in e[1].lower()
               for e in error_events)


def test_engine_no_warning_on_end_turn_finish_reason():
    """No error event when stop_reason is a normal end_turn."""
    engine = _make_engine()
    from core.llm import LLMMessage, LLMUsage

    final_msg = LLMMessage(
        content=[{"type": "text", "text": "done"}],
        usage=LLMUsage(),
        stop_reason="end_turn",
    )
    stream = MagicMock()
    stream.__enter__ = MagicMock(return_value=stream)
    stream.__exit__ = MagicMock(return_value=False)
    stream.text_stream = iter(["done"])
    stream.get_final_message = MagicMock(return_value=final_msg)

    with patch.object(engine._client, "stream_messages", return_value=stream):
        events = list(engine.submit("hi"))

    error_events = [e for e in events if e[0] == "error"]
    assert len(error_events) == 0


# ---------------------------------------------------------------------------
# Mid-stream error
# ---------------------------------------------------------------------------

def test_engine_handles_mid_stream_error():
    """A retryable error mid-stream triggers retry and recovers."""
    engine = _make_engine()

    def failing_text_stream():
        yield "partial"
        raise Exception("Connection reset mid-stream")

    stream_fail = MagicMock()
    stream_fail.__enter__ = MagicMock(return_value=stream_fail)
    stream_fail.__exit__ = MagicMock(return_value=False)
    stream_fail.text_stream = failing_text_stream()

    stream_ok = _make_text_response("success")

    with patch.object(engine._client, "stream_messages", side_effect=[stream_fail, stream_ok]), \
         patch.object(engine._client, "is_authentication_error", return_value=False), \
         patch.object(engine._client, "is_retryable_error",
                      side_effect=lambda e: "Connection reset" in str(e)), \
         patch.object(engine._client, "is_api_error", return_value=False), \
         patch("core.engine.time.sleep"):
        events = list(engine.submit("hi"))

    text_events = [e for e in events if e[0] == "text"]
    assert any("success" in e[1] for e in text_events)


# ---------------------------------------------------------------------------
# Active sequential tool cancellation
# ---------------------------------------------------------------------------

class BlockingTool(Tool):
    name = "Block"
    description = "Blocks until aborted"
    input_schema = {"type": "object", "properties": {}}

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.abort_calls = 0
        self.execute_calls = 0

    def execute(self) -> ToolResult:
        self.execute_calls += 1
        self.started.set()
        if not self.release.wait(timeout=2):
            raise RuntimeError("test did not release blocking tool")
        return ToolResult(content="released")

    def abort(self) -> None:
        self.abort_calls += 1
        self.release.set()


class CountingTool(Tool):
    name = "Count"
    description = "Counts executions"
    input_schema = {"type": "object", "properties": {}}

    def __init__(self):
        self.execute_calls = 0

    def execute(self) -> ToolResult:
        self.execute_calls += 1
        return ToolResult(content="counted")


class RaisingTool(Tool):
    name = "Raise"
    description = "Raises"
    input_schema = {"type": "object", "properties": {}}

    def execute(self) -> ToolResult:
        raise RuntimeError("boom")


def _engine_with_tools(tools):
    return Engine(
        tools=tools,
        system_prompt="test",
        permission_checker=PermissionChecker(auto_approve=True),
    )


def test_tool_default_abort_is_noop():
    EchoTool().abort()
    EchoTool().abort()


def test_engine_abort_propagates_to_active_sequential_tool():
    tool = BlockingTool()
    engine = _engine_with_tools([tool])
    stream = _make_tools_response([{
        "type": "tool_use", "id": "tu_block", "name": "Block", "input": {},
    }])
    caught = []

    def run():
        try:
            list(engine.submit("block"))
        except AbortedError as exc:
            caught.append(exc)

    with patch.object(engine._client, "stream_messages", return_value=stream):
        thread = threading.Thread(target=run)
        thread.start()
        assert tool.started.wait(timeout=2)
        engine.abort()
        thread.join(timeout=2)

    assert not thread.is_alive()
    assert tool.abort_calls == 1
    assert caught
    assert engine._get_active_tool() is None


def test_engine_abort_with_no_active_tool_is_safe():
    engine = _make_engine()
    engine.abort()
    engine.abort()
    assert engine._get_active_tool() is None


def test_abort_after_registration_prevents_tool_execute():
    tool = BlockingTool()
    engine = _engine_with_tools([tool])
    stream = _make_tools_response([{
        "type": "tool_use", "id": "tu_block", "name": "Block", "input": {},
    }])
    original_register = engine._register_active_tool

    def register_then_abort(active_tool):
        original_register(active_tool)
        engine.abort()

    with patch.object(engine, "_register_active_tool", side_effect=register_then_abort), \
         patch.object(engine._client, "stream_messages", return_value=stream):
        with pytest.raises(AbortedError):
            list(engine.submit("block"))

    assert tool.execute_calls == 0
    assert tool.abort_calls == 1
    assert engine._get_active_tool() is None


def test_tool_exception_clears_active_reference():
    engine = _engine_with_tools([RaisingTool()])
    streams = [
        _make_tools_response([{
            "type": "tool_use", "id": "tu_raise", "name": "Raise", "input": {},
        }]),
        _make_text_response("done"),
    ]

    with patch.object(engine._client, "stream_messages", side_effect=streams):
        events = list(engine.submit("raise"))

    result = next(event[3] for event in events if event[0] == "tool_result")
    assert result.is_error
    assert engine._get_active_tool() is None


def test_clear_active_tool_ignores_stale_reference():
    first = BlockingTool()
    second = CountingTool()
    engine = _engine_with_tools([first, second])
    engine._register_active_tool(first)

    engine._clear_active_tool(second)
    assert engine._get_active_tool() is first
    engine._clear_active_tool(first)
    assert engine._get_active_tool() is None


def test_abort_prevents_next_sequential_tool_from_starting():
    first = BlockingTool()
    second = CountingTool()
    engine = _engine_with_tools([first, second])
    stream = _make_tools_response([
        {"type": "tool_use", "id": "tu_1", "name": "Block", "input": {}},
        {"type": "tool_use", "id": "tu_2", "name": "Count", "input": {}},
    ])
    caught = []

    def run():
        try:
            list(engine.submit("run both"))
        except AbortedError as exc:
            caught.append(exc)

    with patch.object(engine._client, "stream_messages", return_value=stream):
        thread = threading.Thread(target=run)
        thread.start()
        assert first.started.wait(timeout=2)
        engine.abort()
        thread.join(timeout=2)

    assert caught
    assert second.execute_calls == 0


def test_parallel_read_only_tools_do_not_use_active_slot():
    barrier = threading.Barrier(2)
    observed = []

    class ReadOne(Tool):
        name = "ReadOne"
        description = "read one"
        input_schema = {"type": "object", "properties": {}}

        def is_read_only(self):
            return True

        def execute(self):
            observed.append(engine._get_active_tool())
            barrier.wait(timeout=2)
            return ToolResult(content="one")

    class ReadTwo(ReadOne):
        name = "ReadTwo"

    engine = _engine_with_tools([ReadOne(), ReadTwo()])
    streams = [
        _make_tools_response([
            {"type": "tool_use", "id": "tu_1", "name": "ReadOne", "input": {}},
            {"type": "tool_use", "id": "tu_2", "name": "ReadTwo", "input": {}},
        ]),
        _make_text_response("done"),
    ]

    with patch.object(engine._client, "stream_messages", side_effect=streams):
        list(engine.submit("read"))

    assert observed == [None, None]
    assert engine._get_active_tool() is None
