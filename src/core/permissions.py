from __future__ import annotations
import os
import sys
import select
from typing import Literal, TYPE_CHECKING
from .tools.base import Tool

if TYPE_CHECKING:
    from ._keylistener import EscListener
    from .sandbox.manager import SandboxManager
    from .plan import PlanModeManager

PermissionBehavior = Literal["allow", "deny"]

# Tools allowed in plan mode (read-only + plan file writes)
_PLAN_MODE_ALLOWED_TOOLS = {"Read", "Glob", "Grep", "AskUserQuestion", "EnterPlanMode", "ExitPlanMode"}
_PLAN_MODE_WRITE_TOOLS = {"Edit", "Write"}  # allowed only for plan file


class PermissionChecker:
    """Read-only tools are auto-allowed. Bash/writes prompt the user (y/n/always)."""

    def __init__(
        self,
        auto_approve: bool = False,
        sandbox_manager: SandboxManager | None = None,
    ):
        self._auto_approve = auto_approve
        self._always_allow: set[str] = set()
        self._esc_listener: EscListener | None = None
        self._sandbox = sandbox_manager
        self._plan_manager: PlanModeManager | None = None

    def set_plan_manager(self, plan_manager: PlanModeManager) -> None:
        self._plan_manager = plan_manager

    def set_esc_listener(self, listener: EscListener | None):
        self._esc_listener = listener

    def check(self, tool: Tool, inputs: dict) -> PermissionBehavior:
        # Plan mode restrictions: only allow read-only tools + plan file writes
        if self._plan_manager is not None and self._plan_manager.is_active:
            if tool.name in _PLAN_MODE_ALLOWED_TOOLS:
                return "allow"
            if tool.name in _PLAN_MODE_WRITE_TOOLS:
                # Only allow writing to the plan file
                file_path = inputs.get("file_path", "")
                plan_path = self._plan_manager.plan_file_path
                if plan_path and file_path == plan_path:
                    return "allow"
                from rich.console import Console
                Console().print(
                    f"[yellow]Plan mode: can only edit the plan file "
                    f"({plan_path})[/yellow]"
                )
                return "deny"
            # Block everything else (Bash, etc.)
            from rich.console import Console
            Console().print(
                f"[yellow]Plan mode: {tool.name} is not allowed. "
                f"Only read-only tools and plan file editing are permitted.[/yellow]"
            )
            return "deny"

        if tool.is_read_only():
            return "allow"
        if self._auto_approve:
            return "allow"
        if tool.name in self._always_allow:
            return "allow"

        # Sandbox auto-allow: sandboxed Bash commands need no confirmation
        if (
            tool.name == "Bash"
            and self._sandbox is not None
            and self._sandbox.is_auto_allow()
            and self._sandbox.should_sandbox(inputs.get("command", ""))
        ):
            return "allow"

        return self._prompt_user(tool, inputs)

    def _prompt_user(self, tool: Tool, inputs: dict) -> PermissionBehavior:
        from rich.console import Console
        console = Console()
        console.print(f"\n[bold yellow]Permission required:[/bold yellow] [bold]{tool.name}[/bold]")
        for k, v in inputs.items():
            val = str(v)[:200] + ("..." if len(str(v)) > 200 else "")
            console.print(f"  [dim]{k}:[/dim] {val}")

        console.print("\n  Allow? \\[y]es / \\[n]o / \\[a]lways: ", end="")

        # Pause the ESC listener so it doesn't steal our keystrokes
        if self._esc_listener:
            self._esc_listener.pause()

        fd = sys.stdin.fileno()
        try:
            while True:
                # In cbreak mode: read single byte unbuffered, no Enter needed
                b = os.read(fd, 1)

                # Check for ESC — distinguish bare ESC from escape
                # sequences (arrow keys etc.) that start with \x1b
                if b == b'\x1b':
                    if select.select([fd], [], [], 0.05)[0]:
                        # Escape sequence — drain and ignore
                        while select.select([fd], [], [], 0.01)[0]:
                            os.read(fd, 64)
                        continue
                    # Genuine ESC press
                    console.print()
                    if self._esc_listener:
                        self._esc_listener.pressed = True
                    return "deny"

                choice = b.decode("utf-8", errors="ignore").lower()
                console.print(choice)  # echo the char

                if choice == 'y':
                    return "allow"
                if choice == 'n':
                    return "deny"
                if choice == 'a':
                    self._always_allow.add(tool.name)
                    return "allow"
                console.print("  Please enter y, n, or a: ", end="")
        finally:
            # Resume the ESC listener
            if self._esc_listener:
                self._esc_listener.resume()
