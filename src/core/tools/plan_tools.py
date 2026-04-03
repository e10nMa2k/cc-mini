"""EnterPlanMode and ExitPlanMode tools.

Corresponds to:
  TS: tools/EnterPlanModeTool/EnterPlanModeTool.ts + prompt.ts
  TS: tools/ExitPlanModeTool/ExitPlanModeV2Tool.ts + prompt.ts
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import Tool, ToolResult

if TYPE_CHECKING:
    from ..plan import PlanModeManager


class EnterPlanModeTool(Tool):
    name = "EnterPlanMode"
    description = (
        "Use this tool proactively when you're about to start a non-trivial implementation task. "
        "Getting user sign-off on your approach before writing code prevents wasted effort and ensures alignment. "
        "This tool transitions you into plan mode where you can explore the codebase and design an implementation "
        "approach for user approval.\n\n"
        "## When to Use This Tool\n\n"
        "**Prefer using EnterPlanMode** for implementation tasks unless they're simple. "
        "Use it when ANY of these conditions apply:\n\n"
        "1. **New Feature Implementation**: Adding meaningful new functionality\n"
        "   - Example: \"Add a logout button\" - where should it go? What should happen on click?\n"
        "   - Example: \"Add form validation\" - what rules? What error messages?\n\n"
        "2. **Multiple Valid Approaches**: The task can be solved in several different ways\n"
        "   - Example: \"Add caching to the API\" - could use Redis, in-memory, file-based, etc.\n"
        "   - Example: \"Improve performance\" - many optimization strategies possible\n\n"
        "3. **Code Modifications**: Changes that affect existing behavior or structure\n"
        "   - Example: \"Update the login flow\" - what exactly should change?\n"
        "   - Example: \"Refactor this component\" - what's the target architecture?\n\n"
        "4. **Architectural Decisions**: The task requires choosing between patterns or technologies\n"
        "   - Example: \"Add real-time updates\" - WebSockets vs SSE vs polling\n"
        "   - Example: \"Implement state management\" - Redux vs Context vs custom solution\n\n"
        "5. **Multi-File Changes**: The task will likely touch more than 2-3 files\n"
        "   - Example: \"Refactor the authentication system\"\n"
        "   - Example: \"Add a new API endpoint with tests\"\n\n"
        "6. **Unclear Requirements**: You need to explore before understanding the full scope\n"
        "   - Example: \"Make the app faster\" - need to profile and identify bottlenecks\n"
        "   - Example: \"Fix the bug in checkout\" - need to investigate root cause\n\n"
        "7. **User Preferences Matter**: The implementation could reasonably go multiple ways\n"
        "   - If you would use AskUserQuestion to clarify the approach, use EnterPlanMode instead\n"
        "   - Plan mode lets you explore first, then present options with context\n\n"
        "## When NOT to Use This Tool\n\n"
        "Only skip EnterPlanMode for simple tasks:\n"
        "- Single-line or few-line fixes (typos, obvious bugs, small tweaks)\n"
        "- Adding a single function with clear requirements\n"
        "- Tasks where the user has given very specific, detailed instructions\n"
        "- Pure research/exploration tasks (use the Agent tool with explore agent instead)\n\n"
        "## What Happens in Plan Mode\n\n"
        "In plan mode, you'll:\n"
        "1. Thoroughly explore the codebase using Glob, Grep, and Read tools\n"
        "2. Understand existing patterns and architecture\n"
        "3. Design an implementation approach\n"
        "4. Present your plan to the user for approval\n"
        "5. Use AskUserQuestion if you need to clarify approaches\n"
        "6. Exit plan mode with ExitPlanMode when ready to implement\n\n"
        "## Important Notes\n\n"
        "- This tool REQUIRES user approval - they must consent to entering plan mode\n"
        "- If unsure whether to use it, err on the side of planning - it's better to get "
        "alignment upfront than to redo work\n"
        "- Users appreciate being consulted before significant changes are made to their codebase"
    )
    input_schema = {
        "type": "object",
        "properties": {},
    }

    def __init__(self, plan_manager: PlanModeManager) -> None:
        self._plan_manager = plan_manager

    def is_read_only(self) -> bool:
        return True

    def get_activity_description(self, **kwargs) -> str | None:
        return "Entering plan mode\u2026"

    def execute(self, **kwargs) -> ToolResult:
        return ToolResult(content=self._plan_manager.enter())


class ExitPlanModeTool(Tool):
    name = "ExitPlanMode"
    description = (
        "Use this tool when you are in plan mode and have finished writing your plan to the plan file "
        "and are ready for user approval.\n\n"
        "## How This Tool Works\n"
        "- You should have already written your plan to the plan file specified in the plan mode system message\n"
        "- This tool does NOT take the plan content as a parameter - it will read the plan from the file you wrote\n"
        "- This tool simply signals that you're done planning and ready for the user to review and approve\n"
        "- The user will see the contents of your plan file when they review it\n\n"
        "## When to Use This Tool\n"
        "IMPORTANT: Only use this tool when the task requires planning the implementation steps of a task "
        "that requires writing code. For research tasks where you're gathering information, searching files, "
        "reading files or in general trying to understand the codebase - do NOT use this tool.\n\n"
        "## Before Using This Tool\n"
        "Ensure your plan is complete and unambiguous:\n"
        "- If you have unresolved questions about requirements or approach, use AskUserQuestion first "
        "(in earlier phases)\n"
        "- Once your plan is finalized, use THIS tool to request approval\n\n"
        "**Important:** Do NOT use AskUserQuestion to ask \"Is this plan okay?\" or \"Should I proceed?\" "
        "- that's exactly what THIS tool does. ExitPlanMode inherently requests user approval of your plan."
    )
    input_schema = {
        "type": "object",
        "properties": {},
    }

    def __init__(self, plan_manager: PlanModeManager) -> None:
        self._plan_manager = plan_manager

    def get_activity_description(self, **kwargs) -> str | None:
        return "Exiting plan mode\u2026"

    def execute(self, **kwargs) -> ToolResult:
        msg, _ = self._plan_manager.exit()
        return ToolResult(content=msg)
