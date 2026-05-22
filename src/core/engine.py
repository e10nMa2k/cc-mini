from __future__ import annotations
import time
from typing import TYPE_CHECKING, Any, Iterator
from .config import DEFAULT_MODEL, default_max_tokens_for_model, resolve_model
from .llm import LLMClient
from .tools.base import Tool, ToolResult
from .permissions import PermissionChecker

if TYPE_CHECKING:
    from .cost_tracker import CostTracker
    from .session import SessionStore

_MAX_RETRIES = 3
_RETRY_BACKOFF = (1, 3, 10)


class AbortedError(Exception):
    """Raised when the current turn is aborted by the user (Esc / Ctrl+C)."""


def _normalize_content_block(block: Any) -> dict[str, Any]:
    """Convert SDK content blocks into plain API dictionaries.

    Anthropic-compatible backends can reject SDK-specific object fields that
    are harmless against Anthropic's own endpoint, so only persist the wire
    fields we actually want to send back.
    """
    if isinstance(block, dict):
        normalized = dict(block)
    else:
        normalized = {}
        for field in (
            "type", "text", "id", "name", "input", "tool_use_id",
            "content", "is_error", "source",
        ):
            if hasattr(block, field):
                normalized[field] = getattr(block, field)

    block_type = normalized.get("type")
    if block_type == "text":
        return {"type": "text", "text": normalized.get("text", "")}
    if block_type == "tool_use":
        return {
            "type": "tool_use",
            "id": normalized.get("id", ""),
            "name": normalized.get("name", ""),
            "input": _normalize_json_value(normalized.get("input", {})),
        }
    if block_type == "tool_result":
        result = {
            "type": "tool_result",
            "tool_use_id": normalized.get("tool_use_id", ""),
            "content": _normalize_json_value(normalized.get("content", "")),
        }
        if "is_error" in normalized:
            result["is_error"] = bool(normalized["is_error"])
        return result
    if block_type == "image":
        return {
            "type": "image",
            "source": _normalize_json_value(normalized.get("source", {})),
        }
    return {
        key: _normalize_json_value(value)
        for key, value in normalized.items()
        if value is not None
    }


def _normalize_json_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _normalize_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_json_value(item) for item in value]
    if hasattr(value, "model_dump"):
        return _normalize_json_value(value.model_dump())
    if hasattr(value, "dict"):
        return _normalize_json_value(value.dict())
    if hasattr(value, "__dict__"):
        data = {
            key: val for key, val in vars(value).items()
            if not key.startswith("_") and not callable(val)
        }
        if data:
            return _normalize_json_value(data)
    return value


def _normalize_message_content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return [_normalize_content_block(block) for block in content]
    return _normalize_json_value(content)


class Engine:
    def __init__(self, tools: list[Tool], system_prompt: str,
                 permission_checker: PermissionChecker,
                 provider: str = "openai",
                 #model: str = DEFAULT_MODEL,
                 model: str = "",
                 max_tokens: int | None = None,
                 api_key: str | None = None,
                 base_url: str | None = None,
                 effort: str | None = None,
                 session_store: SessionStore | None = None,
                 cost_tracker: CostTracker | None = None):
        self._provider = provider
        self._model = resolve_model(model, provider=provider)

        self._max_tokens = max_tokens or default_max_tokens_for_model(
            self._model,
            provider=provider,
        )
        self._effort = effort
        self._client = LLMClient(
            provider=provider,
            api_key=api_key,
            base_url=base_url,
        )
        self._tools = {t.name: t for t in tools}
        self._system_prompt = system_prompt
        self._permissions = permission_checker
        self._messages: list[dict] = []
        self._aborted = False
        self._turn_start_len: int | None = None
        self._active_stream = None  # reference to current HTTP stream
        self._session_store = session_store
        self._cost_tracker = cost_tracker

    # -- message accessors (for compact / resume / commands) ----------------

    def get_messages(self) -> list[dict]:
        return list(self._messages)

    def set_messages(self, messages: list[dict]) -> None:
        self._messages = [
            {
                "role": message["role"],
                "content": _normalize_message_content(message.get("content", "")),
            }
            for message in messages
        ]

    def get_system_prompt(self) -> str:
        return self._system_prompt

    def set_session_store(self, store: SessionStore | None) -> None:
        self._session_store = store

    def set_tools(self, tools: list[Tool]) -> None:
        self._tools = {t.name: t for t in tools}

    def get_model(self) -> str:
        return self._model

    def set_model(self, model: str) -> None:
        self._model = resolve_model(model, provider=self._provider)
        self._max_tokens = default_max_tokens_for_model(
            self._model,
            provider=self._provider,
        )

    def _persist(self, message: dict) -> None:
        """Append message to session store if available."""
        if self._session_store is not None:
            try:
                self._session_store.append_message(message)
            except Exception:
                pass  # don't break the conversation on I/O errors

    @property
    def messages(self) -> list[dict]:
        return self._messages

    @messages.setter
    def messages(self, value: list[dict]) -> None:
        self._messages = value

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @system_prompt.setter
    def system_prompt(self, value: str) -> None:
        self._system_prompt = value

    def last_assistant_text(self) -> str:
        """Extract text from the last assistant message."""
        if not self._messages:
            return ""
        last = self._messages[-1]
        if last.get("role") != "assistant":
            return ""
        content = last.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if hasattr(block, "text"):
                    parts.append(block.text)
                elif isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            return "".join(parts)
        return ""

    def abort(self):
        """Abort the current turn immediately.

        Matches claude-code-main's AbortController.abort(): sets flag and
        closes the active HTTP stream so the generator unblocks at once.
        """
        self._aborted = True
        if self._active_stream is not None:
            try:
                self._active_stream.close()
            except Exception:
                pass

    def cancel_turn(self):
        """Roll back messages to the state before the current turn started.

        Uses _turn_start_len (set at the beginning of submit()) to restore
        messages to the exact state before the turn. This is more robust than
        trying to walk back individual messages, especially when a turn has
        multiple tool_use/tool_result cycles.
        """
        if self._turn_start_len is not None:
            del self._messages[self._turn_start_len:]
            self._turn_start_len = None

    def submit(self, user_input: str | list) -> Iterator[tuple]:
        """Send user message; yield events until the conversation turn completes.

        Yields:
          ("text", str)                         — streamed text chunk, 流式响应
          ("tool_call", name, input)            — before each tool executes
          ("tool_result", name, input, result)  — after each tool executes
          ("waiting",)                          — text done, waiting for tool_use
          ("error", str)                        — non-fatal API error shown to user

        Raises:
          AbortedError — if abort() was called (by Esc listener or Ctrl+C)
        
        while True:
            final = call_llm()  # 调用 LLM
            tool_uses = extract_tool_calls(final)
            
            if not tool_uses:
                break  # 没有工具调用，结束循环
            
            # 执行所有工具
            for tool_use in tool_uses:
                result = self._execute_tool(tool_use)
                tool_results.append(result)
            
            # 将工具结果添加回对话
            self._messages.append({
                "role": "user",
                "content": tool_results
            })
            # 继续循环，让 LLM 处理工具结果

            示例;
            用户: "读取 test.txt 文件"
            AI: [tool_use: read_file]
            工具: [tool_result: "file content"]
            AI: "文件内容是：..."
        """
        self._aborted = False
        self._turn_start_len = len(self._messages)
        self._messages.append({
            "role": "user",
            "content": _normalize_message_content(user_input),
        })
        self._persist(self._messages[-1])

        try:
            while True:
                if self._aborted:
                    raise AbortedError()

                tool_uses = []

                # API call with retry
                final = None
                for attempt in range(_MAX_RETRIES):
                    try:
                        _api_t0 = time.monotonic()
                        with self._client.stream_messages(
                            model=self._model,
                            max_tokens=self._max_tokens,
                            system=self._system_prompt,
                            tools=[t.to_api_schema() for t in self._tools.values()],
                            messages=self._messages,
                            effort=self._effort,
                        ) as stream:
                            self._active_stream = stream
                            got_text = False
                            for text in stream.text_stream:
                                if self._aborted:
                                    raise AbortedError()
                                got_text = True
                                yield ("text", text)

                            if self._aborted:
                                raise AbortedError()

                            if got_text:
                                yield ("waiting",)

                            final = stream.get_final_message()
                            _api_elapsed = time.monotonic() - _api_t0
                            # Track token usage / cost, 成本追踪
                            if final.usage and self._cost_tracker:
                                self._cost_tracker.add_usage(self._model, {
                                    "input_tokens": getattr(final.usage, "input_tokens", 0) or 0,
                                    "output_tokens": getattr(final.usage, "output_tokens", 0) or 0,
                                    "cache_read_input_tokens": getattr(final.usage, "cache_read_input_tokens", 0) or 0,
                                    "cache_creation_input_tokens": getattr(final.usage, "cache_creation_input_tokens", 0) or 0,
                                }, api_duration_s=_api_elapsed)
                                yield ("usage", final.usage)
                            for block in final.content:
                                if _block_type(block) == "tool_use":
                                    tool_uses.append(block)
                        break  # success, exit retry loop
                    except AbortedError:
                        raise
                    except Exception as e:
                        if self._client.is_authentication_error(e):
                            self._messages.pop()
                            yield ("error", f"Authentication failed: {self._client.error_message(e)}")
                            return
                        if self._client.is_retryable_error(e):
                            if attempt < _MAX_RETRIES - 1:
                                wait = _RETRY_BACKOFF[attempt]
                                yield ("error", f"API error, retrying in {wait}s... ({self._client.error_message(e)})")
                                time.sleep(wait)
                            else:
                                self._messages.pop()
                                yield ("error", f"API error after {_MAX_RETRIES} retries: {self._client.error_message(e)}")
                                return
                            continue
                        if self._client.is_api_error(e):
                            self._messages.pop()
                            yield ("error", f"API error: {self._client.error_message(e)}")
                            return
                        if self._aborted:
                            raise AbortedError()
                        raise
                    finally:
                        self._active_stream = None

                if final is None:
                    self._messages.pop()
                    return

                self._messages.append({
                    "role": "assistant",
                    "content": _normalize_message_content(final.content),
                })
                self._persist(self._messages[-1])

                if not tool_uses:
                    break
                # 调用工具,如有
                tool_results = []
                for tool_use in tool_uses:
                    if self._aborted:
                        raise AbortedError()
                    yield ("tool_call", _block_name(tool_use), _block_input(tool_use))
                    # 执行工具并拿到结果
                    result = self._execute_tool(tool_use)
                    yield ("tool_result", _block_name(tool_use), _block_input(tool_use), result)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": _block_id(tool_use),
                        "content": result.content,
                        "is_error": result.is_error,
                    })

                self._messages.append({
                    "role": "user",
                    "content": _normalize_message_content(tool_results),
                })
                self._persist(self._messages[-1])
        except AbortedError:
            self.cancel_turn()
            raise

    def _execute_tool(self, tool_use) -> ToolResult:
        tool_name = _block_name(tool_use)
        tool_input = _block_input(tool_use)
        tool = self._tools.get(tool_name)
        if tool is None:
            return ToolResult(content=f"Unknown tool: {tool_name}", is_error=True)

        if self._permissions.check(tool, tool_input) == "deny":
            return ToolResult(content="Permission denied.", is_error=True)

        try:
            # Snapshot file for diff if it's a write tool we want to track
            old_lines: list[str] | None = None
            if self._cost_tracker and tool_name in ("Edit", "Write"):
                fp = tool_input.get("file_path", "")
                try:
                    from pathlib import Path
                    p = Path(fp)
                    old_lines = p.read_text().splitlines() if p.exists() else []
                except Exception:
                    old_lines = None

            result = tool.execute(**tool_input)

            # Track line changes for Edit/Write
            if self._cost_tracker and old_lines is not None and not result.is_error:
                fp = tool_input.get("file_path", "")
                try:
                    from pathlib import Path
                    new_lines = Path(fp).read_text().splitlines()
                    added = max(len(new_lines) - len(old_lines), 0)
                    removed = max(len(old_lines) - len(new_lines), 0)
                    self._cost_tracker.add_lines_changed(added, removed)
                except Exception:
                    pass

            return result
        except Exception as e:
            return ToolResult(content=f"Tool error: {e}", is_error=True)


def _block_type(block: Any) -> str | None:
    if isinstance(block, dict):
        return block.get("type")
    return getattr(block, "type", None)


def _block_name(block: Any) -> str:
    if isinstance(block, dict):
        return str(block.get("name", ""))
    return str(getattr(block, "name", ""))


def _block_id(block: Any) -> str:
    if isinstance(block, dict):
        return str(block.get("id", ""))
    return str(getattr(block, "id", ""))

def _block_input(block: Any) -> dict[str, Any]:
    """
    获取block的input
    """
    if isinstance(block, dict):
        value = block.get("input", {})
    else:
        value = getattr(block, "input", {})
    return value if isinstance(value, dict) else {}
