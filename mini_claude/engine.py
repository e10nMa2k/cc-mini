from __future__ import annotations
from typing import Iterator
import anthropic
from .tools.base import Tool, ToolResult
from .permissions import PermissionChecker

_MODEL = "claude-opus-4-5"
_MAX_TOKENS = 8096


class Engine:
    def __init__(self, tools: list[Tool], system_prompt: str,
                 permission_checker: PermissionChecker):
        self._client = anthropic.Anthropic()
        self._tools = {t.name: t for t in tools}
        self._system_prompt = system_prompt
        self._permissions = permission_checker
        self._messages: list[dict] = []

    def submit(self, user_input: str) -> Iterator[tuple]:
        """Send user message; yield events until the conversation turn completes.

        Yields:
          ("text", str)                         — streamed text chunk
          ("tool_result", name, input, result)  — after each tool executes
        """
        self._messages.append({"role": "user", "content": user_input})

        while True:
            tool_uses = []

            with self._client.messages.stream(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                system=self._system_prompt,
                tools=[t.to_api_schema() for t in self._tools.values()],
                messages=self._messages,
            ) as stream:
                for text in stream.text_stream:
                    yield ("text", text)

                final = stream.get_final_message()
                for block in final.content:
                    if block.type == "tool_use":
                        tool_uses.append(block)

            self._messages.append({"role": "assistant", "content": final.content})

            if not tool_uses:
                break

            tool_results = []
            for tool_use in tool_uses:
                result = self._execute_tool(tool_use)
                yield ("tool_result", tool_use.name, tool_use.input, result)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": result.content,
                    "is_error": result.is_error,
                })

            self._messages.append({"role": "user", "content": tool_results})

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
