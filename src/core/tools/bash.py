from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from .base import Tool, ToolResult

if TYPE_CHECKING:
    from ..sandbox.manager import SandboxManager

_DEFAULT_TIMEOUT = 120


class BashTool(Tool):
    name = "Bash"
    description = (
        "Executes a given bash command and returns its output.\n\n"
        "The working directory persists between commands, but shell state does not. "
        "The shell environment is initialized from the user's profile (bash or zsh).\n\n"
        "IMPORTANT: Avoid using this tool to run `find`, `grep`, `cat`, `head`, `tail`, "
        "`sed`, `awk`, or `echo` commands, unless explicitly instructed or after you have "
        "verified that a dedicated tool cannot accomplish your task. Instead, use the appropriate "
        "dedicated tool as this will provide a much better experience for the user:\n\n"
        " - File search: Use Glob (NOT find or ls)\n"
        " - Content search: Use Grep (NOT grep or rg)\n"
        " - Read files: Use Read (NOT cat/head/tail)\n"
        " - Edit files: Use Edit (NOT sed/awk)\n"
        " - Write files: Use Write (NOT echo >/cat <<EOF)\n"
        " - Communication: Output text directly (NOT echo/printf)\n"
        "While the Bash tool can do similar things, it's better to use the built-in tools "
        "as they provide a better user experience and make it easier to review tool calls and give permission.\n\n"
        "# Instructions\n"
        " - If your command will create new directories or files, first use this tool to run `ls` "
        "to verify the parent directory exists and is the correct location.\n"
        " - Always quote file paths that contain spaces with double quotes in your command.\n"
        " - Try to maintain your current working directory throughout the session by using absolute paths "
        "and avoiding usage of `cd`. You may use `cd` if the User explicitly requests it.\n"
        " - You may specify an optional timeout in seconds (default 120s).\n"
        " - When issuing multiple commands:\n"
        "   - If the commands are independent and can run in parallel, make multiple Bash tool calls in a single message.\n"
        "   - If the commands depend on each other and must run sequentially, use a single Bash call with '&&' to chain them together.\n"
        "   - Use ';' only when you need to run commands sequentially but don't care if earlier commands fail.\n"
        "   - DO NOT use newlines to separate commands (newlines are ok in quoted strings).\n"
        " - For git commands:\n"
        "   - Prefer to create a new commit rather than amending an existing commit.\n"
        "   - Before running destructive operations (e.g., git reset --hard, git push --force, git checkout --), "
        "consider whether there is a safer alternative that achieves the same goal.\n"
        "   - Never skip hooks (--no-verify) or bypass signing unless the user has explicitly asked for it. "
        "If a hook fails, investigate and fix the underlying issue.\n"
        " - Avoid unnecessary `sleep` commands:\n"
        "   - Do not sleep between commands that can run immediately \u2014 just run them.\n"
        "   - Do not retry failing commands in a sleep loop \u2014 diagnose the root cause.\n"
        "   - If you must sleep, keep the duration short (1-5 seconds) to avoid blocking the user."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The bash command to execute"},
            "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 120},
            "dangerously_disable_sandbox": {
                "type": "boolean",
                "description": "If true and allowed by config, run outside sandbox",
            },
        },
        "required": ["command"],
    }

    def get_activity_description(self, **kwargs) -> str | None:
        command = kwargs.get("command", "")
        # Show a truncated version of the command
        preview = command[:60] + "…" if len(command) > 60 else command
        return f"Running {preview}" if command else None

    def __init__(self, sandbox_manager: SandboxManager | None = None):
        self._sandbox = sandbox_manager

    def execute(
        self,
        command: str,
        timeout: int = _DEFAULT_TIMEOUT,
        dangerously_disable_sandbox: bool = False,
    ) -> ToolResult:
        # Sandbox decision
        use_sandbox = (
            self._sandbox is not None
            and self._sandbox.should_sandbox(command, dangerously_disable_sandbox)
        )

        actual_command = self._sandbox.wrap(command) if use_sandbox else command

        try:
            result = subprocess.run(
                actual_command, shell=True, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=timeout,
            )
            parts = []
            if result.stdout:
                parts.append(result.stdout.rstrip())
            if result.stderr:
                parts.append(f"[stderr]\n{result.stderr.rstrip()}")
            if result.returncode != 0:
                parts.append(f"[exit code: {result.returncode}]")
            return ToolResult(content="\n".join(parts) if parts else "(no output)")
        except subprocess.TimeoutExpired:
            return ToolResult(content=f"Error: Command timed out after {timeout}s", is_error=True)
        except Exception as e:
            return ToolResult(content=f"Error: {e}", is_error=True)
