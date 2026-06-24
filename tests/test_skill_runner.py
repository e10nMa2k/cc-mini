"""Tests for isolated fork-context skill execution."""

from __future__ import annotations

import json
import threading
from unittest.mock import MagicMock

import pytest

from core.engine import AbortedError
from core.llm import LLMUsage
from core.permissions import PermissionChecker
from core.tool import Tool, ToolResult
from features.skill_runner import (
    SUMMARY_HEAD_CHARS,
    SUMMARY_MAX_CHARS,
    SUMMARY_TAIL_CHARS,
    SUMMARY_TRUNCATION_MARKER,
    SkillRunner,
    SkillUsage,
    select_skill_tools,
    truncate_summary,
)
from features.skills import Skill


class NamedTool(Tool):
    description = "test tool"
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, name: str, read_only: bool = False):
        self._name = name
        self._read_only = read_only

    @property
    def name(self):
        return self._name

    def is_read_only(self):
        return self._read_only

    def execute(self):
        return ToolResult(content=self.name)


class FakeTimer:
    def __init__(self, interval, callback):
        self.interval = interval
        self.callback = callback
        self.daemon = False
        self.started = False
        self.cancelled = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        self.callback()


class TimerFactory:
    def __init__(self):
        self.timers = []

    def __call__(self, interval, callback):
        timer = FakeTimer(interval, callback)
        self.timers.append(timer)
        return timer


class FakeEngine:
    def __init__(self, events=(), final_text="done", submit_error=None):
        self.events = list(events)
        self.final_text = final_text
        self.submit_error = submit_error
        self.submitted_prompt = None
        self.abort_calls = 0
        self.messages = []

    def set_messages(self, messages):
        self.messages = messages

    def submit(self, prompt):
        self.submitted_prompt = prompt
        if self.submit_error is not None:
            raise self.submit_error
        yield from self.events

    def last_assistant_text(self):
        return self.final_text

    def abort(self):
        self.abort_calls += 1


class BlockingEngine(FakeEngine):
    def __init__(self, release_on_abort=True):
        super().__init__(final_text="")
        self.started = threading.Event()
        self.release = threading.Event()
        self.release_on_abort = release_on_abort
        self.aborted = False

    def submit(self, prompt):
        self.submitted_prompt = prompt
        self.started.set()
        assert self.release.wait(timeout=2)
        if self.aborted:
            raise AbortedError()
        yield ("text", "unexpected")

    def abort(self):
        self.abort_calls += 1
        self.aborted = True
        if self.release_on_abort:
            self.release.set()


class CapturingFactory:
    def __init__(self, engine):
        self.engine = engine
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.engine


def make_runner(
    engine=None,
    *,
    tools=None,
    checker=None,
    timer_factory=None,
    cost_tracker=None,
    effort="high",
    compact_service_factory=None,
):
    engine = engine or FakeEngine()
    factory = CapturingFactory(engine)
    timer_factory = timer_factory or TimerFactory()
    runner = SkillRunner(
        engine_factory=factory,
        caller_tools=tools or [],
        permission_checker=checker or PermissionChecker(auto_approve=True),
        cwd="/tmp/project",
        default_model="parent-model",
        effort=effort,
        cost_tracker=cost_tracker,
        timeout_s=123,
        timer_factory=timer_factory,
        compact_service_factory=compact_service_factory,
    )
    return runner, factory, timer_factory


def fork_skill(**kwargs):
    defaults = {"name": "review", "context": "fork", "_prompt_text": "Run $ARGUMENTS"}
    defaults.update(kwargs)
    return Skill(**defaults)


def inline_skill(**kwargs):
    defaults = {"name": "review", "context": "inline", "_prompt_text": "Run $ARGUMENTS"}
    defaults.update(kwargs)
    return Skill(**defaults)


def test_select_skill_tools_preserves_caller_order_and_intersection():
    tools = [
        NamedTool("Bash"), NamedTool("Read", True), NamedTool("Write"),
        NamedTool("Glob", True), NamedTool("Grep", True),
    ]
    skill = fork_skill(allowed_tools=["Bash", "Missing"])

    assert [tool.name for tool in select_skill_tools(tools, skill)] == [
        "Bash", "Read", "Glob", "Grep",
    ]


@pytest.mark.parametrize(
    "name",
    [
        "SkillTool", "Agent", "SendMessage", "TaskStop", "EnterPlanMode",
        "ExitPlanMode", "TodoWrite", "TodoUpdate",
    ],
)
def test_select_skill_tools_always_excludes_meta_tools(name):
    skill = fork_skill(allowed_tools=[name])
    assert select_skill_tools([NamedTool(name)], skill) == []


def test_select_skill_tools_can_be_empty():
    assert select_skill_tools([NamedTool("Write")], fork_skill()) == []


def test_runner_expands_arguments_and_constructs_isolated_child():
    tools = [NamedTool("Read", True), NamedTool("Bash")]
    checker = PermissionChecker(auto_approve=True)
    checker._always_allow.add("Bash")
    cost_tracker = MagicMock()
    runner, factory, timers = make_runner(
        tools=tools, checker=checker, cost_tracker=cost_tracker,
    )
    skill = fork_skill(allowed_tools=["Bash"], model="skill-model")

    result = runner.run(skill, "tests")

    assert result.status == "completed"
    assert factory.engine.submitted_prompt == "Run tests"
    call = factory.calls[0]
    assert [tool.name for tool in call["tools"]] == ["Read", "Bash"]
    assert call["model"] == "skill-model"
    assert call["effort"] == "high"
    assert call["session_store"] is None
    assert call["cost_tracker"] is cost_tracker
    assert call["permission_checker"] is not checker
    assert call["permission_checker"]._always_allow == {"Bash"}
    assert call["system_prompt"].find("Available tools: Read, Bash") >= 0
    assert timers.timers[0].interval == 123
    assert timers.timers[0].cancelled is True
    cost_tracker.add_usage.assert_not_called()


def test_runner_uses_parent_model_without_override():
    runner, factory, _ = make_runner()
    result = runner.run(fork_skill())
    assert result.status == "completed"
    assert factory.calls[0]["model"] == "parent-model"
    assert factory.calls[0]["effort"] == "high"


def test_empty_prompt_fails_without_creating_child():
    runner, factory, timers = make_runner()
    result = runner.run(fork_skill(_prompt_text="   "))
    assert result.status == "failed"
    assert "empty" in result.error.lower()
    assert factory.calls == []
    assert timers.timers == []


@pytest.mark.parametrize("context", ["unknown", "detached"])
def test_unsupported_context_fails_without_creating_child(context):
    runner, factory, _ = make_runner()
    result = runner.run(fork_skill(context=context))
    assert result.status == "failed"
    assert context in result.error
    assert factory.calls == []


def test_inline_context_requires_snapshot():
    runner, factory, _ = make_runner()
    result = runner.run(inline_skill())
    assert result.status == "failed"
    assert "snapshot" in result.error.lower()
    assert factory.calls == []


@pytest.mark.parametrize("content", [None, 42, {"type": "text", "text": "request"}])
def test_inline_context_rejects_invalid_message_content(content):
    runner, factory, _ = make_runner()
    result = runner.run(
        inline_skill(),
        parent_snapshot=[{"role": "user", "content": content}],
    )

    assert result.status == "failed"
    assert "snapshot" in result.error.lower()
    assert factory.calls == []


def test_inline_context_loads_defensive_copy_before_prompt():
    engine = FakeEngine()
    runner, _, _ = make_runner(engine)
    snapshot = [{"role": "user", "content": [{"type": "text", "text": "request"}]}]

    result = runner.run(inline_skill(), "checks", parent_snapshot=snapshot)

    assert result.status == "completed"
    assert engine.messages == snapshot
    assert engine.messages is not snapshot
    assert engine.messages[0]["content"] is not snapshot[0]["content"]
    assert engine.submitted_prompt == "Run checks"

    engine.messages[0]["content"][0]["text"] = "mutated"
    assert snapshot[0]["content"][0]["text"] == "request"


class FakeCompactService:
    def __init__(self, output=None, error=None):
        self.output = output
        self.error = error
        self.calls = []

    def compact(self, messages, system_prompt):
        self.calls.append((messages, system_prompt))
        if self.error:
            raise self.error
        return self.output, "summary"


def test_inline_context_below_trigger_skips_compaction():
    compact_factory = MagicMock()
    runner, _, _ = make_runner(compact_service_factory=compact_factory)
    result = runner.run(
        inline_skill(model="unknown-model"),
        parent_snapshot=[{"role": "user", "content": "small"}],
    )
    assert result.status == "completed"
    compact_factory.assert_not_called()


def test_inline_context_compacts_at_trigger(monkeypatch):
    service = FakeCompactService([{"role": "user", "content": "compacted"}])
    runner, _, _ = make_runner(compact_service_factory=lambda model: service)
    estimates = iter([80, 20])
    monkeypatch.setattr("features.skill_runner.auto_compact_threshold", lambda model: 100)
    monkeypatch.setattr(
        "features.skill_runner.estimate_tokens_conservative",
        lambda messages: next(estimates),
    )

    result = runner.run(
        inline_skill(model="child-model"),
        parent_snapshot=[{"role": "user", "content": "large"}],
    )

    assert result.status == "completed"
    assert len(service.calls) == 1
    assert service.calls[0][0] == [{"role": "user", "content": "large"}]


@pytest.mark.parametrize(
    ("service", "message"),
    [
        (FakeCompactService(error=RuntimeError("boom")), "compaction failed"),
        (FakeCompactService([]), "invalid"),
    ],
)
def test_inline_context_compaction_failure(monkeypatch, service, message):
    runner, factory, _ = make_runner(compact_service_factory=lambda model: service)
    monkeypatch.setattr("features.skill_runner.auto_compact_threshold", lambda model: 100)
    monkeypatch.setattr("features.skill_runner.estimate_tokens_conservative", lambda messages: 80)

    result = runner.run(
        inline_skill(),
        parent_snapshot=[{"role": "user", "content": "large"}],
    )

    assert result.status == "failed"
    assert message in result.error.lower()
    assert factory.calls == []


def test_inline_context_rejects_still_oversized_compaction(monkeypatch):
    service = FakeCompactService([{"role": "user", "content": "still large"}])
    runner, factory, _ = make_runner(compact_service_factory=lambda model: service)
    estimates = iter([80, 101])
    monkeypatch.setattr("features.skill_runner.auto_compact_threshold", lambda model: 100)
    monkeypatch.setattr(
        "features.skill_runner.estimate_tokens_conservative",
        lambda messages: next(estimates),
    )

    result = runner.run(
        inline_skill(),
        parent_snapshot=[{"role": "user", "content": "large"}],
    )

    assert result.status == "failed"
    assert "above the model limit" in result.error
    assert factory.calls == []


def test_runner_collects_tool_and_token_usage_without_double_counting():
    tracker = MagicMock()
    engine = FakeEngine(events=[
        ("tool_call", "Read", {}, None),
        ("usage", LLMUsage(input_tokens=10, output_tokens=4)),
        ("usage", LLMUsage(input_tokens=3, output_tokens=2)),
    ], final_text="summary")
    runner, _, _ = make_runner(engine, cost_tracker=tracker)

    result = runner.run(fork_skill())

    assert result.status == "completed"
    assert result.summary == "summary"
    assert result.tool_uses == 1
    assert result.usage == SkillUsage(input_tokens=13, output_tokens=6)
    tracker.add_usage.assert_not_called()


@pytest.mark.parametrize(
    ("engine", "message"),
    [
        (FakeEngine(final_text="", events=[("error", "provider failed")]), "provider failed"),
        (FakeEngine(submit_error=RuntimeError("construction failed")), "construction failed"),
    ],
)
def test_runner_maps_failures_to_result(engine, message):
    runner, _, _ = make_runner(engine)
    result = runner.run(fork_skill())
    assert result.status == "failed"
    assert message in result.error


def test_engine_factory_failure_is_returned():
    def factory(**kwargs):
        raise ValueError("bad factory")

    runner = SkillRunner(
        engine_factory=factory,
        caller_tools=[],
        permission_checker=PermissionChecker(),
        cwd="/tmp",
        default_model="model",
        timer_factory=TimerFactory(),
    )
    result = runner.run(fork_skill())
    assert result.status == "failed"
    assert result.error == "bad factory"


def _run_in_thread(runner, skill):
    results = []
    thread = threading.Thread(target=lambda: results.append(runner.run(skill)))
    thread.start()
    return thread, results


def test_caller_abort_cancels_active_child():
    engine = BlockingEngine()
    runner, _, _ = make_runner(engine)
    thread, results = _run_in_thread(runner, fork_skill())
    assert engine.started.wait(timeout=2)

    runner.abort()
    thread.join(timeout=2)

    assert results[0].status == "aborted"
    assert engine.abort_calls == 1
    assert runner._active_engine is None


def test_timeout_cancels_active_child():
    engine = BlockingEngine()
    timers = TimerFactory()
    runner, _, _ = make_runner(engine, timer_factory=timers)
    thread, results = _run_in_thread(runner, fork_skill())
    assert engine.started.wait(timeout=2)

    timers.timers[0].fire()
    thread.join(timeout=2)

    assert results[0].status == "timed_out"
    assert engine.abort_calls == 1
    assert timers.timers[0].cancelled is True


def test_first_cancellation_reason_wins():
    engine = BlockingEngine(release_on_abort=False)
    timers = TimerFactory()
    runner, _, _ = make_runner(engine, timer_factory=timers)
    thread, results = _run_in_thread(runner, fork_skill())
    assert engine.started.wait(timeout=2)

    runner.abort()
    timers.timers[0].fire()
    engine.release.set()
    thread.join(timeout=2)

    assert results[0].status == "aborted"
    assert engine.abort_calls == 1


def test_timeout_waits_for_blocked_execution_to_unwind():
    engine = BlockingEngine(release_on_abort=False)
    timers = TimerFactory()
    runner, _, _ = make_runner(engine, timer_factory=timers)
    thread, results = _run_in_thread(runner, fork_skill())
    assert engine.started.wait(timeout=2)

    timers.timers[0].fire()
    assert engine.abort_calls == 1
    assert thread.is_alive()
    engine.release.set()
    thread.join(timeout=2)

    assert results[0].status == "timed_out"


def test_summary_boundary_and_truncation():
    exact = "x" * SUMMARY_MAX_CHARS
    assert truncate_summary(exact) == exact

    long = "a" * SUMMARY_HEAD_CHARS + "middle" + "z" * 2000
    truncated = truncate_summary(long)
    assert len(truncated) == SUMMARY_MAX_CHARS
    assert truncated.startswith("a" * SUMMARY_HEAD_CHARS)
    assert SUMMARY_TRUNCATION_MARKER in truncated
    assert truncated.endswith("z" * SUMMARY_TAIL_CHARS)


def test_skill_result_serialization_is_stable():
    runner, _, _ = make_runner(FakeEngine(final_text="完成"))
    result = runner.run(fork_skill())

    payload = json.loads(result.to_json())
    assert payload == result.to_dict()
    assert payload["skill_name"] == "review"
    assert payload["usage"] == {"input_tokens": 0, "output_tokens": 0}
