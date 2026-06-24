from types import SimpleNamespace
from unittest.mock import MagicMock

from core.llm import (
    LLMClient,
    _to_openai_messages,
    _tool_schema_to_openai,
    default_companion_model,
    supports_reasoning_effort,
)


def test_to_openai_messages_maps_tool_roundtrip():
    messages = [
        {
            "role": "assistant",
            "content": [{
                "type": "tool_use",
                "id": "call_1",
                "name": "Echo",
                "input": {"message": "hello"},
            }],
        },
        {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": "call_1",
                "content": "Echo: hello",
                "is_error": False,
            }],
        },
    ]

    converted = _to_openai_messages("system prompt", messages)

    assert converted[0] == {"role": "system", "content": "system prompt"}
    assert converted[1]["role"] == "assistant"
    assert converted[1]["tool_calls"][0]["function"]["name"] == "Echo"
    assert converted[2] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "Echo: hello",
    }


def test_to_openai_messages_maps_image_input():
    messages = [{
        "role": "user",
        "content": [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": "abcd",
                },
            },
            {"type": "text", "text": "describe this"},
        ],
    }]

    converted = _to_openai_messages(None, messages)

    assert converted[0]["role"] == "user"
    assert converted[0]["content"][0]["type"] == "image_url"
    assert converted[0]["content"][0]["image_url"]["url"] == "data:image/png;base64,abcd"
    assert converted[0]["content"][1] == {"type": "text", "text": "describe this"}


def test_tool_schema_to_openai_wraps_function_schema():
    tool = {
        "name": "Read",
        "description": "Read a file",
        "input_schema": {"type": "object", "properties": {}},
    }

    converted = _tool_schema_to_openai(tool)

    assert converted["type"] == "function"
    assert converted["function"]["name"] == "Read"
    assert converted["function"]["parameters"] == {"type": "object", "properties": {}}


def test_openai_reasoning_effort_support():
    assert supports_reasoning_effort("openai", "gpt-5") is True
    assert supports_reasoning_effort("openai", "gpt-4.1-mini") is False
    assert supports_reasoning_effort("anthropic", "claude-sonnet-4") is False


def test_default_companion_model_uses_main_model_for_openai():
    assert default_companion_model("openai", "gpt-4.1-mini") == "gpt-4.1-mini"


def test_anthropic_requests_omit_openai_effort_parameter():
    client = object.__new__(LLMClient)
    client.provider = "anthropic"
    client._client = MagicMock()
    client._client.messages.create.return_value = SimpleNamespace(
        content=[], usage=None, stop_reason="end_turn",
    )

    client.create_message(
        model="claude-sonnet-4",
        max_tokens=128,
        messages=[],
        effort="high",
    )
    client.stream_messages(
        model="claude-sonnet-4",
        max_tokens=128,
        messages=[],
        effort="high",
    )

    assert "effort" not in client._client.messages.create.call_args.kwargs
    assert "effort" not in client._client.messages.stream.call_args.kwargs
