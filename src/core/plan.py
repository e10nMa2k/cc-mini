"""Plan mode — explore-before-implement workflow.

Corresponds to:
  TS: utils/plans.ts          (plan file I/O, slug generation)
  TS: bootstrap/state.ts      (plan mode state)
  TS: utils/permissions/permissionSetup.ts  (permission stripping)
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import Engine
    from .tools.base import Tool

# ---------------------------------------------------------------------------
# Word slug generation (simplified from utils/words.ts)
# ---------------------------------------------------------------------------

_ADJECTIVES = [
    "amber", "azure", "bold", "bright", "calm", "clear", "cool", "crisp",
    "dark", "deep", "eager", "fair", "fast", "fierce", "gentle", "golden",
    "green", "happy", "keen", "kind", "light", "lucky", "merry", "noble",
    "pale", "proud", "quick", "quiet", "rapid", "sharp", "silent", "sleek",
    "snoopy", "soft", "steady", "still", "swift", "tall", "tidy", "vivid",
    "warm", "wild", "wise", "young", "brave", "clever", "daring", "fresh",
]

_NOUNS = [
    "arrow", "badge", "blade", "brook", "castle", "cloud", "comet", "coral",
    "crane", "creek", "crown", "dawn", "delta", "dove", "dream", "eagle",
    "ember", "falcon", "fern", "flame", "forge", "frost", "garden", "grove",
    "harbor", "hawk", "heron", "hill", "island", "jewel", "lake", "leaf",
    "lotus", "maple", "marsh", "meadow", "moon", "ocean", "orchid", "peak",
    "pine", "planet", "pond", "rain", "river", "sage", "shore", "spark",
    "stone", "storm", "summit", "tiger", "trail", "valley", "wave", "willow",
]


def _generate_slug() -> str:
    return f"{random.choice(_ADJECTIVES)}-{random.choice(_NOUNS)}-{random.choice(_NOUNS)}"


def _get_plans_dir() -> Path:
    plans_dir = Path.home() / ".mini-claude" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    return plans_dir


# ---------------------------------------------------------------------------
# PlanModeManager
# ---------------------------------------------------------------------------

class PlanModeManager:
    """Manages plan mode lifecycle: enter, exit, file management, prompt injection.

    Constructed once at startup, bound to engine after engine creation.
    Passed to EnterPlanModeTool / ExitPlanModeTool via constructor injection
    (same pattern as AgentTool holding WorkerManager).
    """

    def __init__(self) -> None:
        self._engine: Engine | None = None
        self._active: bool = False
        self._plan_file: Path | None = None
        self._saved_tools: list[Tool] | None = None
        self._saved_prompt: str | None = None

    def bind_engine(self, engine: Engine) -> None:
        self._engine = engine

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def plan_file_path(self) -> str | None:
        return str(self._plan_file) if self._plan_file else None

    def get_plan_content(self) -> str | None:
        if self._plan_file and self._plan_file.exists():
            try:
                return self._plan_file.read_text(encoding="utf-8")
            except OSError:
                return None
        return None

    # -- enter / exit -------------------------------------------------------

    def enter(self) -> str:
        """Enter plan mode: create plan file, switch to read-only tools, inject prompt."""
        assert self._engine is not None, "PlanModeManager not bound to engine"

        if self._active:
            return f"Already in plan mode. Plan file: {self._plan_file}"

        # Generate plan file
        plans_dir = _get_plans_dir()
        for _ in range(10):
            slug = _generate_slug()
            path = plans_dir / f"{slug}.md"
            if not path.exists():
                break
        self._plan_file = path

        # Save current state
        self._saved_tools = list(self._engine._tools.values())
        self._saved_prompt = self._engine.system_prompt

        # Switch to read-only tools + plan tools + AskUserQuestion
        from .tools.plan_tools import EnterPlanModeTool, ExitPlanModeTool
        from .tools.ask_user import AskUserQuestionTool
        from .tools.file_read import FileReadTool
        from .tools.glob_tool import GlobTool
        from .tools.grep_tool import GrepTool
        from .tools.file_edit import FileEditTool
        from .tools.file_write import FileWriteTool

        plan_tools: list[Tool] = [
            FileReadTool(),
            GlobTool(),
            GrepTool(),
            FileEditTool(),   # allowed only for plan file (checked by permissions)
            FileWriteTool(),  # allowed only for plan file (checked by permissions)
            AskUserQuestionTool(),
            EnterPlanModeTool(self),
            ExitPlanModeTool(self),
        ]
        self._engine.set_tools(plan_tools)

        # Inject plan mode instructions into system prompt
        from .context import get_plan_mode_section
        plan_section = get_plan_mode_section(str(self._plan_file))
        self._engine.system_prompt = self._saved_prompt + "\n\n" + plan_section

        self._active = True

        # Short message — detailed instructions are already in the system prompt.
        # Matches TS EnterPlanModeTool which returns a brief confirmation.
        return f"Entered plan mode. Plan file: {self._plan_file}"

    def exit(self) -> tuple[str, str | None]:
        """Exit plan mode: restore tools and prompt, return (message, plan_content)."""
        assert self._engine is not None, "PlanModeManager not bound to engine"

        if not self._active:
            return ("Not in plan mode.", None)

        plan_content = self.get_plan_content()

        # Restore original state
        if self._saved_tools is not None:
            self._engine.set_tools(self._saved_tools)
        if self._saved_prompt is not None:
            self._engine.system_prompt = self._saved_prompt

        self._active = False
        self._saved_tools = None
        self._saved_prompt = None

        plan_path = str(self._plan_file) if self._plan_file else "unknown"

        if plan_content:
            msg = (
                f"User has approved your plan. You can now start coding.\n\n"
                f"Your plan has been saved to: {plan_path}\n"
                f"You can refer back to it if needed during implementation.\n\n"
                f"## Approved Plan:\n{plan_content}"
            )
        else:
            msg = (
                "Exited plan mode. No plan file was written.\n"
                "You can now make edits, run tools, and take actions."
            )

        return (msg, plan_content)
