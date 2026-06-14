"""Isolated execution for model-invocable fork-context skills."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Callable, Literal

from core.context import build_skill_system_prompt
from core.engine import AbortedError, Engine
from core.permissions import PermissionChecker
from core.tool import Tool
from features.skills import Skill

if TYPE_CHECKING:
    from features.cost_tracker import CostTracker


SkillStatus = Literal["completed", "failed", "timed_out", "aborted"]
EngineFactory = Callable[..., Engine]
TimerFactory = Callable[[float, Callable[[], None]], threading.Timer]

DEFAULT_SKILL_TIMEOUT_S = 600.0
SUMMARY_MAX_CHARS = 4000
SUMMARY_HEAD_CHARS = 3000
SUMMARY_TRUNCATION_MARKER = "\n...[truncated]...\n"
# 4000 - 3000 - len(marker) = 981. Keep this derived so marker changes remain safe.
SUMMARY_TAIL_CHARS = (
    SUMMARY_MAX_CHARS - SUMMARY_HEAD_CHARS - len(SUMMARY_TRUNCATION_MARKER)
)

BASE_READ_ONLY_TOOLS = frozenset({"Read", "Glob", "Grep"})
EXCLUDED_META_TOOLS = frozenset({
    "SkillTool",
    "Agent",
    "SendMessage",
    "TaskStop",
    "EnterPlanMode",
    "ExitPlanMode",
    "TodoWrite",
    "TodoUpdate",
})


@dataclass(frozen=True)
class SkillUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class SkillResult:
    skill_name: str
    status: SkillStatus
    summary: str
    duration_ms: int
    tool_uses: int
    usage: SkillUsage
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


def truncate_summary(summary: str) -> str:
    """Bound a summary while retaining useful context from both ends."""
    if len(summary) <= SUMMARY_MAX_CHARS:
        return summary
    return (
        summary[:SUMMARY_HEAD_CHARS]
        + SUMMARY_TRUNCATION_MARKER
        + summary[-SUMMARY_TAIL_CHARS:]
    )


def select_skill_tools(caller_tools: list[Tool], skill: Skill) -> list[Tool]:
    """Return the ordered caller/skill tool intersection."""
    allowed = BASE_READ_ONLY_TOOLS | set(skill.allowed_tools)
    return [
        tool for tool in caller_tools
        if tool.name in allowed and tool.name not in EXCLUDED_META_TOOLS
    ]


class SkillRunner:
    """Run one fork-context skill inside a fresh, restricted child Engine."""

    def __init__(
        self,
        *,
        engine_factory: EngineFactory,
        caller_tools: list[Tool],
        permission_checker: PermissionChecker,
        cwd: str,
        default_model: str,
        effort: str | None = None,
        cost_tracker: CostTracker | None = None,
        timeout_s: float = DEFAULT_SKILL_TIMEOUT_S,
        timer_factory: TimerFactory = threading.Timer,
    ) -> None:
        self._engine_factory = engine_factory
        self._caller_tools = list(caller_tools)
        self._permissions = permission_checker
        self._cwd = cwd
        self._default_model = default_model
        self._effort = effort
        self._cost_tracker = cost_tracker
        self._timeout_s = timeout_s
        self._timer_factory = timer_factory
        self._state_lock = threading.Lock()
        self._active_engine: Engine | None = None
        self._cancel_reason: Literal["aborted", "timed_out"] | None = None

    def abort(self) -> None:
        """Request cooperative cancellation of the active child Engine."""
        self._cancel("aborted")

    def run(self, skill: Skill, arguments: str = "") -> SkillResult:
        started = time.monotonic()
        tool_uses = 0
        input_tokens = 0
        output_tokens = 0
        latest_error: str | None = None
        summary = ""
        child: Engine | None = None
        timer: threading.Timer | None = None

        def result(status: SkillStatus, error: str | None = None) -> SkillResult:
            return SkillResult(
                skill_name=skill.name,
                status=status,
                summary=truncate_summary(summary),
                duration_ms=int((time.monotonic() - started) * 1000),
                tool_uses=tool_uses,
                usage=SkillUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                ),
                error=error,
            )

        if skill.context != "fork":
            return result("failed", f"Unsupported skill context: {skill.context}")

        prompt = skill.get_prompt(arguments)
        if not prompt.strip():
            return result("failed", "Expanded skill prompt is empty.")

        with self._state_lock:
            if self._active_engine is not None:
                return result("failed", "SkillRunner is already running a skill.")
            self._cancel_reason = None

        try:
            child_tools = select_skill_tools(self._caller_tools, skill)
            child_permissions = self._permissions.fork()
            model = skill.model or self._default_model
            system_prompt = build_skill_system_prompt(
                cwd=self._cwd,
                model=model,
                tool_names=[tool.name for tool in child_tools],
            )
            child = self._engine_factory(
                tools=child_tools,
                system_prompt=system_prompt,
                permission_checker=child_permissions,
                model=model,
                effort=self._effort,
                session_store=None,
                cost_tracker=self._cost_tracker,
            )

            with self._state_lock:
                self._active_engine = child

            timer = self._timer_factory(self._timeout_s, self._timeout)
            timer.daemon = True
            timer.start()

            for event in child.submit(prompt):
                kind = event[0]
                if kind == "tool_call":
                    tool_uses += 1
                elif kind == "usage":
                    usage = event[1]
                    input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
                    output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
                elif kind == "error":
                    latest_error = str(event[1])

            summary = child.last_assistant_text()
            cancel_reason = self._read_cancel_reason()
            if cancel_reason is not None:
                return result(cancel_reason, self._cancellation_error(cancel_reason))
            if not summary.strip():
                return result(
                    "failed",
                    latest_error or "Skill ended without a final assistant response.",
                )
            return result("completed")
        except AbortedError:
            cancel_reason = self._read_cancel_reason()
            if cancel_reason is not None:
                return result(cancel_reason, self._cancellation_error(cancel_reason))
            return result("failed", "Skill execution was aborted unexpectedly.")
        except Exception as exc:
            return result("failed", str(exc))
        finally:
            if timer is not None:
                timer.cancel()
            with self._state_lock:
                if self._active_engine is child:
                    self._active_engine = None

    def _timeout(self) -> None:
        self._cancel("timed_out")

    def _cancel(self, reason: Literal["aborted", "timed_out"]) -> None:
        child: Engine | None = None
        with self._state_lock:
            if self._cancel_reason is None:
                self._cancel_reason = reason
                child = self._active_engine
        if child is not None:
            child.abort()

    def _read_cancel_reason(self) -> Literal["aborted", "timed_out"] | None:
        with self._state_lock:
            return self._cancel_reason

    @staticmethod
    def _cancellation_error(reason: str) -> str:
        if reason == "timed_out":
            return "Skill execution timed out."
        return "Skill execution was aborted."
