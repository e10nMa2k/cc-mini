from __future__ import annotations
import time
from typing import TYPE_CHECKING, Any, Iterator
import anthropic
from .config import DEFAULT_MODEL, default_max_tokens_for_model, resolve_model
from .tools.base import Tool, ToolResult
from .permissions import PermissionChecker

if TYPE_CHECKING:
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
                 model: str = DEFAULT_MODEL,
                 max_tokens: int | None = None,
                 api_key: str | None = None,
                 base_url: str | None = None,
                 session_store: SessionStore | None = None):
        self._model = resolve_model(model)
        self._max_tokens = max_tokens or default_max_tokens_for_model(self._model)
        self._client = anthropic.Anthropic(api_key=api_key, base_url=base_url)
        self._tools = {t.name: t for t in tools}
        self._system_prompt = system_prompt
        self._permissions = permission_checker
        self._messages: list[dict] = []
        self._aborted = False
        self._turn_start_len: int | None = None
        self._active_stream = None  # reference to current HTTP stream
        self._session_store = session_store

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
          ("text", str)                         — streamed text chunk
          ("tool_call", name, input)            — before each tool executes
          ("tool_result", name, input, result)  — after each tool executes
          ("waiting",)                          — text done, waiting for tool_use
          ("error", str)                        — non-fatal API error shown to user

        Raises:
          AbortedError — if abort() was called (by Esc listener or Ctrl+C)
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
                        with self._client.messages.stream(
                            model=self._model,
                            max_tokens=self._max_tokens,
                            system=self._system_prompt,
                            tools=[t.to_api_schema() for t in self._tools.values()],
                            messages=self._messages,
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
                            for block in final.content:
                                if block.type == "tool_use":
                                    tool_uses.append(block)
                        break  # success, exit retry loop
                    except AbortedError:
                        raise
                    except anthropic.AuthenticationError as e:
                        self._messages.pop()
                        yield ("error", f"Authentication failed: {e.message}")
                        return
                    except (anthropic.RateLimitError, anthropic.APIConnectionError,
                            anthropic.InternalServerError) as e:
                        if attempt < _MAX_RETRIES - 1:
                            wait = _RETRY_BACKOFF[attempt]
                            yield ("error", f"API error, retrying in {wait}s... ({e})")
                            time.sleep(wait)
                        else:
                            self._messages.pop()
                            yield ("error", f"API error after {_MAX_RETRIES} retries: {e}")
                            return
                    except anthropic.APIError as e:
                        self._messages.pop()
                        yield ("error", f"API error: {e.message}")
                        return
                    except Exception:
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

                tool_results = []
                for tool_use in tool_uses:
                    if self._aborted:
                        raise AbortedError()
                    yield ("tool_call", tool_use.name, tool_use.input)
                    result = self._execute_tool(tool_use)
                    yield ("tool_result", tool_use.name, tool_use.input, result)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
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
        tool = self._tools.get(tool_use.name)
        if tool is None:
            return ToolResult(content=f"Unknown tool: {tool_use.name}", is_error=True)

        if self._permissions.check(tool, tool_use.input) == "deny":
            return ToolResult(content="Permission denied.", is_error=True)

        try:
            return tool.execute(**tool_use.input)
        except Exception as e:
            return ToolResult(content=f"Tool error: {e}", is_error=True)
