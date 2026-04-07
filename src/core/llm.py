from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterator

import anthropic
import httpx


_OPENAI_IMPORT_ERROR: Exception | None = None

try:
    from openai import OpenAI
    import openai
except Exception as exc:  # pragma: no cover - exercised in tests via stubs
    OpenAI = None  # type: ignore[assignment]
    openai = None  # type: ignore[assignment]
    _OPENAI_IMPORT_ERROR = exc


ProviderName = str

_ANTHROPIC_PROVIDER = "anthropic"
_OPENAI_PROVIDER = "openai"
_VALID_PROVIDERS = {_ANTHROPIC_PROVIDER, _OPENAI_PROVIDER}

MODEL_CONTEXT_WINDOW_DEFAULT = 200_000

def get_context_window_for_model(model: str) -> int:
    """Return the context window size for a given model."""
    lowered = model.lower()
    if "[1m]" in lowered or "1m" in lowered.split("-"):
        return 1_000_000
    return MODEL_CONTEXT_WINDOW_DEFAULT


# Upper limit for max output tokens — used for auto-escalation scenarios.
_MODEL_MAX_OUTPUT_UPPER: dict[str, int] = {
    "claude-opus-4-6": 64000,
    "claude-sonnet-4-6": 64000,
    "claude-opus-4-5": 64000,
    "claude-sonnet-4-5": 64000,
    "claude-sonnet-4": 64000,
    "claude-opus-4-1": 64000,
    "claude-opus-4": 64000,
    "claude-3-7-sonnet": 64000,
    "claude-3-5-sonnet": 8192,
    "claude-3-5-haiku": 8192,
}

def get_max_output_tokens_upper(model: str) -> int | None:
    """Return the upper limit of output tokens for a model, if known."""
    for prefix, limit in _MODEL_MAX_OUTPUT_UPPER.items():
        if model.startswith(prefix):
            return limit
    return None


@dataclass
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class LLMMessage:
    content: list[dict[str, Any]]
    usage: LLMUsage | None = None


def validate_provider(provider: str | None) -> ProviderName:
    normalized = (provider or _ANTHROPIC_PROVIDER).strip().lower()
    if normalized not in _VALID_PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider}")
    return normalized


def default_model_for_provider(provider: str) -> str:
    provider = validate_provider(provider)
    if provider == _OPENAI_PROVIDER:
        return "gpt-5.1-codex"
    return "claude-sonnet-4-6"


def default_companion_model(provider: str, model: str) -> str:
    provider = validate_provider(provider)
    if provider == _OPENAI_PROVIDER:
        return model
    return "claude-haiku-4-5-20251001"


def default_max_tokens_for_provider(provider: str) -> int:
    provider = validate_provider(provider)
    if provider == _OPENAI_PROVIDER:
        return 8192
    return 32000


def supports_reasoning_effort(provider: str, model: str) -> bool:
    provider = validate_provider(provider)
    if provider != _OPENAI_PROVIDER:
        return False
    lowered = model.lower()
    return lowered.startswith(("gpt-5", "o1", "o3", "o4"))


class LLMClient:
    def __init__(
        self,
        provider: str = _ANTHROPIC_PROVIDER,
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        self.provider = validate_provider(provider)
        self._api_key = api_key
        self._base_url = base_url
        if self.provider == _OPENAI_PROVIDER:
            if OpenAI is None:
                message = "OpenAI support requires the `openai` package to be installed."
                if _OPENAI_IMPORT_ERROR is not None:
                    message += f" Import failed: {_OPENAI_IMPORT_ERROR}"
                raise ValueError(message)
            self._client = OpenAI(api_key=api_key, base_url=base_url)
        else:
            self._client = anthropic.Anthropic(
                api_key=api_key,
                base_url=base_url,
                timeout=httpx.Timeout(600.0, connect=30.0),
            )

    def create_message(
        self,
        *,
        model: str,
        max_tokens: int,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        effort: str | None = None,
    ) -> LLMMessage:
        if self.provider == _OPENAI_PROVIDER:
            return self._openai_create_message(
                model=model,
                max_tokens=max_tokens,
                messages=messages,
                system=system,
                tools=tools,
                effort=effort,
            )
        return self._anthropic_create_message(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
            system=system,
            tools=tools,
        )

    def stream_messages(
        self,
        *,
        model: str,
        max_tokens: int,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        effort: str | None = None,
    ):
        if self.provider == _OPENAI_PROVIDER:
            return _OpenAIStream(
                client=self._client,
                model=model,
                max_tokens=max_tokens,
                messages=messages,
                system=system,
                tools=tools or [],
                effort=effort,
            )
        return _AnthropicStream(
            client=self._client,
            model=model,
            max_tokens=max_tokens,
            messages=messages,
            system=system,
            tools=tools or [],
        )

    def is_authentication_error(self, exc: Exception) -> bool:
        if self.provider == _OPENAI_PROVIDER:
            return openai is not None and isinstance(exc, openai.AuthenticationError)
        return isinstance(exc, anthropic.AuthenticationError)

    def is_retryable_error(self, exc: Exception) -> bool:
        if isinstance(exc, (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError)):
            return True
        if self.provider == _OPENAI_PROVIDER:
            return openai is not None and isinstance(
                exc,
                (
                    openai.RateLimitError,
                    openai.APIConnectionError,
                    openai.InternalServerError,
                ),
            )
        return isinstance(
            exc,
            (
                anthropic.RateLimitError,
                anthropic.APIConnectionError,
                anthropic.InternalServerError,
            ),
        )

    def is_api_error(self, exc: Exception) -> bool:
        if self.provider == _OPENAI_PROVIDER:
            return openai is not None and isinstance(exc, openai.APIError)
        return isinstance(exc, anthropic.APIError)

    @staticmethod
    def error_message(exc: Exception) -> str:
        return str(getattr(exc, "message", None) or exc)

    def _anthropic_create_message(
        self,
        *,
        model: str,
        max_tokens: int,
        messages: list[dict[str, Any]],
        system: str | None,
        tools: list[dict[str, Any]] | None,
    ) -> LLMMessage:
        kwargs: dict[str, Any] = dict(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
        )
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools
        response = self._client.messages.create(**kwargs)
        usage = _usage_from_anthropic(getattr(response, "usage", None))
        return LLMMessage(
            content=_normalize_anthropic_content(getattr(response, "content", [])),
            usage=usage,
        )

    def _openai_create_message(
        self,
        *,
        model: str,
        max_tokens: int,
        messages: list[dict[str, Any]],
        system: str | None,
        tools: list[dict[str, Any]] | None,
        effort: str | None,
    ) -> LLMMessage:
        params = _build_openai_request(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            tools=tools or [],
            effort=effort,
            stream=False,
        )
        response = self._client.chat.completions.create(**params)
        choice = response.choices[0].message if response.choices else None
        usage = _usage_from_openai(getattr(response, "usage", None))
        return LLMMessage(
            content=_normalize_openai_message(choice),
            usage=usage,
        )


class _AnthropicStream:
    def __init__(
        self,
        *,
        client: Any,
        model: str,
        max_tokens: int,
        messages: list[dict[str, Any]],
        system: str | None,
        tools: list[dict[str, Any]],
    ):
        self._raw = client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            messages=messages,
        )
        self._ctx = None
        self.text_stream: Iterator[str] = iter(())

    def __enter__(self):
        self._ctx = self._raw.__enter__()
        self.text_stream = self._ctx.text_stream
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._raw.__exit__(exc_type, exc, tb)

    def close(self) -> None:
        """Signal cancellation — best-effort close of the underlying stream."""
        try:
            self._raw.close()
        except Exception:
            pass

    def get_final_message(self) -> LLMMessage:
        final = self._ctx.get_final_message()
        return LLMMessage(
            content=_normalize_anthropic_content(getattr(final, "content", [])),
            usage=_usage_from_anthropic(getattr(final, "usage", None)),
        )


class _OpenAIStream:
    def __init__(
        self,
        *,
        client: Any,
        model: str,
        max_tokens: int,
        messages: list[dict[str, Any]],
        system: str | None,
        tools: list[dict[str, Any]],
        effort: str | None,
    ):
        self._client = client
        self._params = _build_openai_request(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            tools=tools,
            effort=effort,
            stream=True,
        )
        self._stream = None
        self._text_parts: list[str] = []
        self._tool_calls: dict[int, dict[str, Any]] = {}
        self._usage: LLMUsage | None = None
        self.text_stream: Iterator[str] = iter(())

    def __enter__(self):
        self._stream = self._client.chat.completions.create(**self._params)
        self.text_stream = self._iter_text()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def close(self) -> None:
        if self._stream is not None and hasattr(self._stream, "close"):
            self._stream.close()

    def _iter_text(self) -> Iterator[str]:
        for chunk in self._stream:
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                self._usage = _usage_from_openai(usage)
            for choice in _value(chunk, "choices", []) or []:
                delta = _value(choice, "delta", {}) or {}
                content = _value(delta, "content")
                if content:
                    self._text_parts.append(content)
                    yield content
                for tool_call in _value(delta, "tool_calls", []) or []:
                    index = int(_value(tool_call, "index", 0) or 0)
                    entry = self._tool_calls.setdefault(index, {
                        "id": "",
                        "name": "",
                        "arguments": "",
                    })
                    tool_id = _value(tool_call, "id")
                    if tool_id:
                        entry["id"] = tool_id
                    function = _value(tool_call, "function", {}) or {}
                    name = _value(function, "name")
                    if name:
                        entry["name"] = name
                    arguments = _value(function, "arguments")
                    if arguments:
                        entry["arguments"] += arguments

    def get_final_message(self) -> LLMMessage:
        content: list[dict[str, Any]] = []
        text = "".join(self._text_parts)
        if text:
            content.append({"type": "text", "text": text})
        for index in sorted(self._tool_calls):
            tool_call = self._tool_calls[index]
            raw_args = tool_call.get("arguments", "").strip()
            parsed_args: Any = {}
            if raw_args:
                try:
                    parsed_args = json.loads(raw_args)
                except json.JSONDecodeError:
                    parsed_args = {}
            content.append({
                "type": "tool_use",
                "id": tool_call.get("id", ""),
                "name": tool_call.get("name", ""),
                "input": parsed_args if isinstance(parsed_args, dict) else {},
            })
        return LLMMessage(content=content, usage=self._usage)


def _normalize_anthropic_content(content: Any) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for block in content or []:
        normalized = _normalize_anthropic_block(block)
        if normalized is not None:
            blocks.append(normalized)
    return blocks


def _normalize_anthropic_block(block: Any) -> dict[str, Any] | None:
    block_type = _value(block, "type")
    if block_type == "text":
        return {"type": "text", "text": _value(block, "text", "")}
    if block_type == "tool_use":
        return {
            "type": "tool_use",
            "id": _value(block, "id", ""),
            "name": _value(block, "name", ""),
            "input": _value(block, "input", {}) or {},
        }
    if block_type == "tool_result":
        normalized = {
            "type": "tool_result",
            "tool_use_id": _value(block, "tool_use_id", ""),
            "content": _value(block, "content", ""),
        }
        is_error = _value(block, "is_error")
        if is_error is not None:
            normalized["is_error"] = bool(is_error)
        return normalized
    if block_type == "image":
        return {
            "type": "image",
            "source": _value(block, "source", {}),
        }
    if isinstance(block, dict):
        return dict(block)
    if hasattr(block, "model_dump"):
        return block.model_dump()
    return None


def _normalize_openai_message(message: Any) -> list[dict[str, Any]]:
    if message is None:
        return []
    content: list[dict[str, Any]] = []
    text = _extract_openai_text(_value(message, "content"))
    if text:
        content.append({"type": "text", "text": text})
    for tool_call in _value(message, "tool_calls", []) or []:
        arguments = _value(_value(tool_call, "function", {}) or {}, "arguments", "") or ""
        parsed_args: Any = {}
        if arguments:
            try:
                parsed_args = json.loads(arguments)
            except json.JSONDecodeError:
                parsed_args = {}
        content.append({
            "type": "tool_use",
            "id": _value(tool_call, "id", ""),
            "name": _value(_value(tool_call, "function", {}) or {}, "name", ""),
            "input": parsed_args if isinstance(parsed_args, dict) else {},
        })
    return content


def _extract_openai_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for item in content:
        item_type = _value(item, "type")
        if item_type == "text":
            text = _value(item, "text")
            if isinstance(text, str):
                parts.append(text)
            elif isinstance(text, dict):
                parts.append(str(text.get("value", "")))
    return "".join(parts)


def _usage_from_anthropic(usage: Any) -> LLMUsage | None:
    if usage is None:
        return None
    return LLMUsage(
        input_tokens=int(_value(usage, "input_tokens", 0) or 0),
        output_tokens=int(_value(usage, "output_tokens", 0) or 0),
        cache_read_input_tokens=int(_value(usage, "cache_read_input_tokens", 0) or 0),
        cache_creation_input_tokens=int(_value(usage, "cache_creation_input_tokens", 0) or 0),
    )


def _usage_from_openai(usage: Any) -> LLMUsage | None:
    if usage is None:
        return None
    return LLMUsage(
        input_tokens=int(_value(usage, "prompt_tokens", 0) or 0),
        output_tokens=int(_value(usage, "completion_tokens", 0) or 0),
    )


def _build_openai_request(
    *,
    model: str,
    max_tokens: int,
    system: str | None,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    effort: str | None,
    stream: bool,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "model": model,
        "messages": _to_openai_messages(system, messages),
        "max_tokens": max_tokens,
        "stream": stream,
    }
    if tools:
        params["tools"] = [_tool_schema_to_openai(tool) for tool in tools]
    if effort and supports_reasoning_effort(_OPENAI_PROVIDER, model):
        params["reasoning_effort"] = effort
    return params


def _to_openai_messages(system: str | None, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})

    for message in messages:
        role = message.get("role")
        content = message.get("content", "")

        if role == "user" and isinstance(content, list):
            tool_results = [
                block for block in content
                if isinstance(block, dict) and block.get("type") == "tool_result"
            ]
            if tool_results and len(tool_results) == len(content):
                for block in tool_results:
                    out.append({
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": _tool_result_to_text(block.get("content", "")),
                    })
                continue

            out.append({
                "role": "user",
                "content": _user_content_blocks_to_openai(content),
            })
            continue

        if role == "assistant" and isinstance(content, list):
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "text":
                    text_parts.append(block.get("text", ""))
                elif block_type == "tool_use":
                    tool_calls.append({
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                        },
                    })
            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": "".join(text_parts) or None,
            }
            if tool_calls:
                assistant_message["tool_calls"] = tool_calls
            out.append(assistant_message)
            continue

        out.append({
            "role": role,
            "content": content,
        })

    return out


def _user_content_blocks_to_openai(content: list[Any]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            parts.append({"type": "text", "text": block.get("text", "")})
        elif block_type == "image":
            source = block.get("source", {})
            media_type = source.get("media_type", "image/png")
            data = source.get("data", "")
            parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{data}"},
            })
    if not parts:
        return [{"type": "text", "text": ""}]
    return parts


def _tool_schema_to_openai(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {}),
        },
    }


def _tool_result_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    return json.dumps(content, ensure_ascii=False)


def _value(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
