"""
tests/test_swarm.py — cc-mini swarm feature tests

Coverage:
  - TaskRegistry thread-safe operations
  - AgentRunner background execution, callbacks, timeout, and message injection
  - SwarmCoordinator notification assembly, queue, and running count
  - AgentTool sync/background modes, depth guard, disallowed subtypes, timeout_s
  - SendMessageTool / TaskStopTool
  - SwarmSession notification injection, empty-submit fix, wait_for_completion
  - build_swarm_tools factory
  - Prompt content assertions
  - create_swarm() factory
"""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from core.swarm.task import TaskRegistry, TaskState, _generate_task_id
from core.swarm.agent_runner import AgentRunner
from core.swarm.coordinator import SwarmCoordinator, SwarmSession
from core.swarm.tools import AgentTool, SendMessageTool, TaskStopTool, build_swarm_tools
from core.swarm.prompts import get_coordinator_system_prompt, get_worker_system_prompt
from core.swarm import create_swarm, Swarm
from core.tool import ToolResult
from core.permissions import PermissionChecker


# ===========================================================================
# Fixtures & helpers
# ===========================================================================

def _make_registry() -> TaskRegistry:
    return TaskRegistry()


def _make_coordinator(registry: TaskRegistry | None = None) -> SwarmCoordinator:
    return SwarmCoordinator(registry or _make_registry())


def _make_mock_engine(text_response: str = "done", raise_exc: Exception | None = None):
    """Create a mock Engine whose submit() yields a single text event."""
    engine = MagicMock()
    engine.abort = MagicMock()
    engine._messages = []

    def fake_submit(prompt):
        if raise_exc:
            raise raise_exc
        yield ("text", text_response)

    engine.submit = MagicMock(side_effect=fake_submit)
    return engine


def _make_agent_tool(
    registry: TaskRegistry | None = None,
    coordinator: SwarmCoordinator | None = None,
    allow_subagents: bool = True,
    max_depth: int = 3,
    current_depth: int = 0,
) -> AgentTool:
    reg = registry or _make_registry()
    coord = coordinator or _make_coordinator(reg)
    return AgentTool(
        registry=reg,
        coordinator=coord,
        allow_subagents=allow_subagents,
        max_depth=max_depth,
        _current_depth=current_depth,
    )


# ===========================================================================
# TaskRegistry tests
# ===========================================================================

class TestTaskRegistry:

    def test_generate_task_id_format(self):
        tid = _generate_task_id()
        assert tid.startswith("a-")
        assert len(tid) == 10  # "a-" + 8 chars

    def test_generate_task_id_unique(self):
        ids = {_generate_task_id() for _ in range(100)}
        assert len(ids) == 100  # collision probability is negligible

    def test_register_returns_running_state(self):
        reg = _make_registry()
        runner = MagicMock()
        state = reg.register(runner=runner, description="test task", tool_use_id="tu_1")

        assert state.status == "running"
        assert state.description == "test task"
        assert state.tool_use_id == "tu_1"
        assert state.result is None
        assert state.error is None
        assert not state.notified

    def test_complete_transitions_to_completed(self):
        reg = _make_registry()
        runner = MagicMock()
        state = reg.register(runner, "task")

        ok = reg.complete(state.task_id, "result text")

        assert ok is True
        updated = reg.get(state.task_id)
        assert updated.status == "completed"
        assert updated.result == "result text"
        assert updated.end_time is not None

    def test_complete_noop_on_non_running(self):
        reg = _make_registry()
        runner = MagicMock()
        state = reg.register(runner, "task")
        reg.complete(state.task_id, "first")

        ok = reg.complete(state.task_id, "second")
        assert ok is False
        assert reg.get(state.task_id).result == "first"

    def test_fail_transitions_to_failed(self):
        reg = _make_registry()
        runner = MagicMock()
        state = reg.register(runner, "task")

        ok = reg.fail(state.task_id, "some error")

        assert ok is True
        assert reg.get(state.task_id).status == "failed"
        assert reg.get(state.task_id).error == "some error"

    def test_kill_calls_runner_stop(self):
        reg = _make_registry()
        runner = MagicMock()
        runner.stop = MagicMock()
        state = reg.register(runner, "task")

        ok = reg.kill(state.task_id)

        assert ok is True
        runner.stop.assert_called_once()
        assert reg.get(state.task_id).status == "killed"

    def test_kill_noop_on_completed(self):
        reg = _make_registry()
        runner = MagicMock()
        state = reg.register(runner, "task")
        reg.complete(state.task_id, "done")

        ok = reg.kill(state.task_id)
        assert ok is False

    def test_mark_notified_idempotent(self):
        reg = _make_registry()
        runner = MagicMock()
        state = reg.register(runner, "task")

        first = reg.mark_notified(state.task_id)
        second = reg.mark_notified(state.task_id)

        assert first is True
        assert second is False

    def test_get_running_filters_correctly(self):
        reg = _make_registry()
        r1, r2, r3 = MagicMock(), MagicMock(), MagicMock()
        for r in (r1, r2, r3):
            r.stop = MagicMock()

        s1 = reg.register(r1, "task1")
        s2 = reg.register(r2, "task2")
        s3 = reg.register(r3, "task3")

        reg.complete(s1.task_id, "done")
        reg.kill(s3.task_id)

        running = reg.get_running()
        assert len(running) == 1
        assert running[0].task_id == s2.task_id

    def test_thread_safety(self):
        """Concurrent register + complete should not crash or corrupt state."""
        reg = _make_registry()
        errors: list[Exception] = []

        def worker():
            try:
                runner = MagicMock()
                runner.stop = MagicMock()
                state = reg.register(runner, "concurrent task")
                time.sleep(0.001)
                reg.complete(state.task_id, "ok")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(reg.get_all()) == 20

    def test_kill_all_running(self):
        reg = _make_registry()
        runners = [MagicMock() for _ in range(3)]
        for r in runners:
            r.stop = MagicMock()

        states = [reg.register(r, f"task{i}") for i, r in enumerate(runners)]
        reg.complete(states[0].task_id, "done")  # leaves 2 running

        killed = reg.kill_all_running()
        assert len(killed) == 2
        for r in runners[1:]:
            r.stop.assert_called_once()


# ===========================================================================
# AgentRunner tests
# ===========================================================================

class TestAgentRunner:

    def test_run_background_calls_on_done(self):
        engine = _make_mock_engine("task result")
        runner = AgentRunner(engine)

        done_calls: list[tuple] = []
        event = threading.Event()

        def on_done(task_id, result, error):
            done_calls.append((task_id, result, error))
            event.set()

        runner.start("a-test001", "do something", on_done)
        event.wait(timeout=5)

        assert len(done_calls) == 1
        tid, result, error = done_calls[0]
        assert tid == "a-test001"
        assert result == "task result"
        assert error is None

    def test_run_background_captures_error(self):
        engine = _make_mock_engine(raise_exc=RuntimeError("boom"))
        runner = AgentRunner(engine)

        done_calls: list[tuple] = []
        event = threading.Event()

        def on_done(task_id, result, error):
            done_calls.append((task_id, result, error))
            event.set()

        runner.start("a-test002", "fail task", on_done)
        event.wait(timeout=5)

        assert done_calls[0][2] == "boom"

    def test_stop_aborts_engine(self):
        engine = _make_mock_engine("result")
        runner = AgentRunner(engine)
        runner.stop()
        engine.abort.assert_called_once()

    def test_send_message_queues(self):
        engine = _make_mock_engine("done")
        runner = AgentRunner(engine)
        runner.send_message("hello")
        runner.send_message("world")
        assert runner._pending_messages.qsize() == 2

    def test_send_message_triggers_continuation(self):
        """Pending messages injected before start should trigger a second submit()."""
        call_count = 0

        def fake_submit(prompt):
            nonlocal call_count
            call_count += 1
            yield ("text", f"response{call_count}")

        engine = MagicMock()
        engine.abort = MagicMock()
        engine.submit = MagicMock(side_effect=fake_submit)

        runner = AgentRunner(engine)
        # Inject message before starting the thread
        runner.send_message("follow-up")

        done_event = threading.Event()

        def on_done(*args):
            done_event.set()

        runner.start("a-test003", "initial prompt", on_done)
        done_event.wait(timeout=5)

        # Should have submitted twice: initial prompt + follow-up
        assert call_count == 2

    def test_timeout_aborts_agent(self):
        """Agent should be stopped when timeout_s is exceeded."""
        abort_event = threading.Event()
        start_event = threading.Event()

        def slow_submit(prompt):
            start_event.set()
            abort_event.wait(timeout=5)
            yield ("text", "never reaches coordinator")

        engine = MagicMock()
        engine.abort = MagicMock(side_effect=lambda: abort_event.set())
        engine.submit = MagicMock(side_effect=slow_submit)

        runner = AgentRunner(engine)
        done_event = threading.Event()
        done_args: list = []

        def on_done(task_id, result, error):
            done_args.extend([task_id, result, error])
            done_event.set()

        runner.start("a-timeout1", "slow task", on_done, timeout_s=0.2)
        start_event.wait(timeout=5)   # wait for thread to actually begin
        done_event.wait(timeout=5)    # wait for abort + on_done

        assert runner._stopped is True
        # Intentional stop must not propagate as a failure error
        assert done_args[2] is None

    def test_timeout_timer_cancelled_on_normal_completion(self):
        """Timer should be cleaned up when the agent finishes before the deadline."""
        engine = _make_mock_engine("fast result")
        runner = AgentRunner(engine)

        done_event = threading.Event()
        runner.start("a-fast1", "quick task", lambda *_: done_event.set(), timeout_s=60)
        done_event.wait(timeout=5)

        # Timer should have been cancelled and cleared
        assert runner._timer is None


# ===========================================================================
# SwarmCoordinator tests
# ===========================================================================

class TestSwarmCoordinator:

    def test_on_task_done_completed(self):
        reg = _make_registry()
        coord = SwarmCoordinator(reg)
        runner = MagicMock()
        runner.stop = MagicMock()
        runner.tool_use_count = 7
        state = reg.register(runner, "my task", tool_use_id="tu_abc")

        coord.on_task_done(state.task_id, "finished!", None)

        notifications = coord.drain_notifications()
        assert len(notifications) == 1
        xml = notifications[0]
        assert "<task-notification>" in xml
        assert state.task_id in xml
        assert "completed" in xml
        assert "finished!" in xml
        assert "tu_abc" in xml

    def test_on_task_done_failed(self):
        reg = _make_registry()
        coord = SwarmCoordinator(reg)
        runner = MagicMock()
        runner.stop = MagicMock()
        runner.tool_use_count = 0
        state = reg.register(runner, "failing task")

        coord.on_task_done(state.task_id, None, "crash!")

        notifications = coord.drain_notifications()
        assert len(notifications) == 1
        xml = notifications[0]
        assert "failed" in xml
        assert "crash!" in xml

    def test_on_task_done_idempotent(self):
        """The same task must not produce two notifications."""
        reg = _make_registry()
        coord = SwarmCoordinator(reg)
        runner = MagicMock()
        runner.stop = MagicMock()
        runner.tool_use_count = 0
        state = reg.register(runner, "task")

        coord.on_task_done(state.task_id, "result", None)
        coord.on_task_done(state.task_id, "result", None)  # duplicate call

        notifications = coord.drain_notifications()
        assert len(notifications) == 1

    def test_drain_notifications_empties_queue(self):
        reg = _make_registry()
        coord = SwarmCoordinator(reg)

        runners = [MagicMock() for _ in range(3)]
        for r in runners:
            r.stop = MagicMock()
            r.tool_use_count = 0

        states = [reg.register(r, f"t{i}") for i, r in enumerate(runners)]
        for s in states:
            coord.on_task_done(s.task_id, "done", None)

        first_drain = coord.drain_notifications()
        second_drain = coord.drain_notifications()

        assert len(first_drain) == 3
        assert len(second_drain) == 0

    def test_build_notification_xml_structure(self):
        xml = SwarmCoordinator._build_notification(
            task_id="a-abc12345",
            description="my task",
            status="completed",
            result="all good",
            tool_use_count=5,
            duration_ms=1234,
            tool_use_id="tu_001",
        )
        assert "<task-notification>" in xml
        assert "</task-notification>" in xml
        assert "<task-id>a-abc12345</task-id>" in xml
        assert "<status>completed</status>" in xml
        assert "<result>all good</result>" in xml
        assert "<tool_uses>5</tool_uses>" in xml
        assert "<duration_ms>1234</duration_ms>" in xml
        assert "<tool-use-id>tu_001</tool-use-id>" in xml

    def test_has_pending(self):
        reg = _make_registry()
        coord = SwarmCoordinator(reg)
        runner = MagicMock()
        runner.stop = MagicMock()
        runner.tool_use_count = 0
        state = reg.register(runner, "task")

        assert not coord.has_pending()
        coord.on_task_done(state.task_id, "done", None)
        assert coord.has_pending()
        coord.drain_notifications()
        assert not coord.has_pending()

    def test_tool_use_count_comes_from_runner(self):
        """Notification should reflect the runner's live tool_use_count, not the stale TaskState field."""
        reg = _make_registry()
        coord = SwarmCoordinator(reg)
        runner = MagicMock()
        runner.stop = MagicMock()
        runner.tool_use_count = 42  # live count on the runner

        state = reg.register(runner, "counted task")
        # TaskState.tool_use_count is still 0 (never incremented via registry)
        assert state.tool_use_count == 0

        coord.on_task_done(state.task_id, "done", None)
        xml = coord.drain_notifications()[0]

        assert "<tool_uses>42</tool_uses>" in xml

    def test_get_running_count(self):
        reg = _make_registry()
        coord = SwarmCoordinator(reg)

        runners = [MagicMock() for _ in range(3)]
        for r in runners:
            r.stop = MagicMock()
            r.tool_use_count = 0

        states = [reg.register(r, f"task{i}") for i, r in enumerate(runners)]

        assert coord.get_running_count() == 3

        reg.complete(states[0].task_id, "done")
        assert coord.get_running_count() == 2

        reg.kill(states[1].task_id)
        assert coord.get_running_count() == 1


# ===========================================================================
# AgentTool tests
# ===========================================================================

class TestAgentTool:

    def test_name_and_schema(self):
        tool = _make_agent_tool()
        assert tool.name == "Agent"
        assert "description" in tool.input_schema["properties"]
        assert "prompt" in tool.input_schema["properties"]
        assert "run_in_background" in tool.input_schema["properties"]
        assert "timeout_s" in tool.input_schema["properties"]

    def test_sync_mode_returns_result(self):
        reg = _make_registry()
        coord = _make_coordinator(reg)
        tool = _make_agent_tool(registry=reg, coordinator=coord)

        mock_engine = _make_mock_engine("sync result text")

        with patch.object(tool, "_build_child_engine", return_value=mock_engine):
            result = tool.execute(
                description="test task",
                prompt="do something",
                run_in_background=False,
            )

        assert not result.is_error
        assert "sync result text" in result.content

    def test_background_mode_returns_async_launched(self):
        reg = _make_registry()
        coord = _make_coordinator(reg)
        tool = _make_agent_tool(registry=reg, coordinator=coord)

        # Use a blocking engine so the agent is still running during assertions
        launch_event = threading.Event()
        done_event = threading.Event()

        def slow_submit(prompt):
            launch_event.set()
            done_event.wait(timeout=5)
            yield ("text", "background result")

        mock_engine = MagicMock()
        mock_engine.abort = MagicMock()
        mock_engine._messages = []
        mock_engine.submit = MagicMock(side_effect=slow_submit)

        with patch.object(tool, "_build_child_engine", return_value=mock_engine):
            result = tool.execute(
                description="bg task",
                prompt="work in background",
                run_in_background=True,
            )

        assert not result.is_error
        assert "async agent launched" in result.content.lower()
        assert "task_id" in result.content

        # Verify the agent is registered and running
        launch_event.wait(timeout=5)
        running = reg.get_running()
        assert len(running) == 1
        assert running[0].description == "bg task"

        done_event.set()  # release the background thread

    def test_max_depth_blocks_spawn(self):
        tool = _make_agent_tool(max_depth=2, current_depth=2)
        result = tool.execute(description="deep task", prompt="go deeper")
        assert result.is_error
        assert "depth" in result.content.lower()

    def test_disallow_subagents_blocks_spawn(self):
        tool = _make_agent_tool(allow_subagents=False)
        result = tool.execute(description="task", prompt="spawn something")
        assert result.is_error
        assert "disabled" in result.content.lower()

    def test_disallowed_subtype_blocked(self):
        reg = _make_registry()
        coord = _make_coordinator(reg)
        tool = AgentTool(
            registry=reg,
            coordinator=coord,
            disallowed_subtypes={"verifier"},
        )
        result = tool.execute(
            description="verify",
            prompt="check it",
            subagent_type="verifier",
        )
        assert result.is_error
        assert "verifier" in result.content

    def test_background_triggers_coordinator_on_completion(self):
        """Background agent completion should produce a task-notification."""
        reg = _make_registry()
        coord = SwarmCoordinator(reg)
        tool = _make_agent_tool(registry=reg, coordinator=coord)

        mock_engine = _make_mock_engine("finished work")
        done_event = threading.Event()

        original_on_task_done = coord.on_task_done

        def patched_on_done(task_id, result, error):
            original_on_task_done(task_id, result, error)
            done_event.set()

        coord.on_task_done = patched_on_done

        with patch.object(tool, "_build_child_engine", return_value=mock_engine):
            tool.execute(
                description="bg work",
                prompt="do bg work",
                run_in_background=True,
            )

        done_event.wait(timeout=5)
        notifications = coord.drain_notifications()
        assert len(notifications) == 1
        assert "bg work" in notifications[0]

    def test_timeout_s_passed_to_runner(self):
        """timeout_s from execute() should be forwarded to AgentRunner.start()."""
        reg = _make_registry()
        coord = _make_coordinator(reg)
        tool = _make_agent_tool(registry=reg, coordinator=coord)

        mock_engine = _make_mock_engine("done")
        started_args: dict = {}

        original_start = AgentRunner.start

        def capture_start(self, task_id, prompt, on_done, timeout_s=None):
            started_args["timeout_s"] = timeout_s
            return original_start(self, task_id, prompt, on_done, timeout_s=timeout_s)

        with patch.object(tool, "_build_child_engine", return_value=mock_engine):
            with patch.object(AgentRunner, "start", capture_start):
                tool.execute(
                    description="timed task",
                    prompt="do work",
                    run_in_background=True,
                    timeout_s=120.0,
                )

        assert started_args.get("timeout_s") == 120.0


# ===========================================================================
# SendMessageTool tests
# ===========================================================================

class TestSendMessageTool:

    def test_name_and_schema(self):
        reg = _make_registry()
        tool = SendMessageTool(reg)
        assert tool.name == "SendMessage"
        assert "to" in tool.input_schema["properties"]
        assert "message" in tool.input_schema["properties"]

    def test_activity_description(self):
        reg = _make_registry()
        tool = SendMessageTool(reg)
        assert tool.get_activity_description(to="a-abc123") == "Sending message to agent a-abc123"
        assert tool.get_activity_description() == "Sending agent message"

    def test_send_to_running_agent(self):
        reg = _make_registry()
        runner = MagicMock()
        runner.stop = MagicMock()
        runner.send_message = MagicMock()
        state = reg.register(runner, "agent")

        tool = SendMessageTool(reg)
        result = tool.execute(to=state.task_id, message="hello agent")

        assert not result.is_error
        runner.send_message.assert_called_once_with("hello agent")

    def test_send_to_nonexistent(self):
        reg = _make_registry()
        tool = SendMessageTool(reg)
        result = tool.execute(to="a-nonexistent", message="hello")
        assert result.is_error
        assert "not found" in result.content.lower()

    def test_send_to_completed_agent(self):
        reg = _make_registry()
        runner = MagicMock()
        runner.stop = MagicMock()
        state = reg.register(runner, "agent")
        reg.complete(state.task_id, "done")

        tool = SendMessageTool(reg)
        result = tool.execute(to=state.task_id, message="too late")
        assert result.is_error
        assert "not running" in result.content.lower()


# ===========================================================================
# TaskStopTool tests
# ===========================================================================

class TestTaskStopTool:

    def test_name_and_schema(self):
        reg = _make_registry()
        tool = TaskStopTool(reg)
        assert tool.name == "TaskStop"
        assert "task_id" in tool.input_schema["properties"]

    def test_activity_description(self):
        reg = _make_registry()
        tool = TaskStopTool(reg)
        assert tool.get_activity_description(task_id="a-abc123") == "Stopping agent a-abc123"
        assert tool.get_activity_description() == "Stopping agent"

    def test_stop_running_agent(self):
        reg = _make_registry()
        runner = MagicMock()
        runner.stop = MagicMock()
        state = reg.register(runner, "agent")

        tool = TaskStopTool(reg)
        result = tool.execute(task_id=state.task_id)

        assert not result.is_error
        assert "stopped" in result.content.lower()
        runner.stop.assert_called_once()

    def test_stop_nonexistent(self):
        reg = _make_registry()
        tool = TaskStopTool(reg)
        result = tool.execute(task_id="a-ghost")
        assert result.is_error

    def test_stop_already_completed(self):
        reg = _make_registry()
        runner = MagicMock()
        runner.stop = MagicMock()
        state = reg.register(runner, "agent")
        reg.complete(state.task_id, "done")

        tool = TaskStopTool(reg)
        result = tool.execute(task_id=state.task_id)
        assert not result.is_error
        assert "terminal" in result.content.lower()


# ===========================================================================
# SwarmSession tests
# ===========================================================================

class TestSwarmSession:

    def _make_session(self, multi_submit: bool = False):
        reg = _make_registry()
        coord = SwarmCoordinator(reg)
        engine = MagicMock()
        engine._messages = []
        engine.abort = MagicMock()
        engine.get_messages = MagicMock(return_value=[])
        engine.last_assistant_text = MagicMock(return_value="response")

        if multi_submit:
            # Return a fresh iterator on each call
            engine.submit = MagicMock(
                side_effect=lambda _: iter([("text", "response")])
            )
        else:
            engine.submit = MagicMock(return_value=iter([("text", "response")]))

        return SwarmSession(engine, coord), engine, coord, reg

    def test_submit_passthrough(self):
        session, engine, _, _ = self._make_session()
        events = list(session.submit("hello"))
        engine.submit.assert_called_once_with("hello")
        assert events == [("text", "response")]

    def test_notifications_injected_before_submit(self):
        """task-notification XML must be injected into engine._messages before the API call."""
        session, engine, coord, reg = self._make_session()

        runner = MagicMock()
        runner.stop = MagicMock()
        runner.tool_use_count = 0
        state = reg.register(runner, "task")
        coord.on_task_done(state.task_id, "worker done", None)

        assert coord.has_pending()

        list(session.submit("next user message"))

        messages_content = [m.get("content", "") for m in engine._messages]
        assert any("<task-notification>" in str(c) for c in messages_content)

    def test_empty_submit_with_notifications_uses_review_prompt(self):
        """submit('') when notifications are pending must NOT send an empty user message."""
        session, engine, coord, reg = self._make_session()

        runner = MagicMock()
        runner.stop = MagicMock()
        runner.tool_use_count = 0
        state = reg.register(runner, "task")
        coord.on_task_done(state.task_id, "done", None)

        list(session.submit(""))

        # engine.submit must have been called with a non-empty string
        call_arg = engine.submit.call_args[0][0]
        assert isinstance(call_arg, str) and call_arg.strip() != ""

    def test_empty_submit_without_notifications_is_noop(self):
        """submit('') with no pending notifications must not call the engine at all."""
        session, engine, _, _ = self._make_session()

        events = list(session.submit(""))

        engine.submit.assert_not_called()
        assert events == []

    def test_has_pending_delegates_to_coordinator(self):
        session, engine, coord, reg = self._make_session()
        assert not session.has_pending_notifications()

        runner = MagicMock()
        runner.stop = MagicMock()
        runner.tool_use_count = 0
        state = reg.register(runner, "task")
        coord.on_task_done(state.task_id, "done", None)

        assert session.has_pending_notifications()

    def test_wait_for_completion_drains_all_notifications(self):
        """wait_for_completion should process notifications until no agents are running."""
        session, engine, coord, reg = self._make_session(multi_submit=True)

        # Pre-complete two tasks so their notifications are immediately available
        for i in range(2):
            runner = MagicMock()
            runner.stop = MagicMock()
            runner.tool_use_count = 3
            state = reg.register(runner, f"task{i}")
            # Complete in registry so get_running_count() drops to 0 immediately
            reg.complete(state.task_id, f"result{i}")
            coord.on_task_done.__func__  # ensure it's the real method
            # Manually enqueue a notification via the coordinator's queue
            coord._notification_queue.put(
                SwarmCoordinator._build_notification(
                    task_id=state.task_id,
                    description=f"task{i}",
                    status="completed",
                    result=f"result{i}",
                )
            )

        events = list(session.wait_for_completion(timeout_s=10))
        assert any(e == ("text", "response") for e in events)
        assert not coord.has_pending()

    def test_wait_for_completion_raises_on_timeout(self):
        """wait_for_completion should raise TimeoutError when agents stall."""
        reg = _make_registry()
        coord = SwarmCoordinator(reg)
        engine = MagicMock()
        engine._messages = []
        engine.submit = MagicMock(side_effect=lambda _: iter([]))
        session = SwarmSession(engine, coord)

        # Register a task that never completes
        runner = MagicMock()
        runner.stop = MagicMock()
        runner.tool_use_count = 0
        reg.register(runner, "stalled task")  # stays "running"

        with pytest.raises(TimeoutError):
            list(session.wait_for_completion(timeout_s=0.1, poll_interval_s=0.05))


# ===========================================================================
# build_swarm_tools tests
# ===========================================================================

class TestBuildSwarmTools:

    def test_returns_three_tools(self):
        reg = _make_registry()
        coord = _make_coordinator(reg)
        tools = build_swarm_tools(reg, coord)

        names = {t.name for t in tools}
        assert names == {"Agent", "SendMessage", "TaskStop"}

    def test_agent_tool_configured_correctly(self):
        reg = _make_registry()
        coord = _make_coordinator(reg)
        tools = build_swarm_tools(reg, coord, max_depth=5)

        agent_tool = next(t for t in tools if t.name == "Agent")
        assert isinstance(agent_tool, AgentTool)
        assert agent_tool._max_depth == 5
        assert agent_tool._current_depth == 0


# ===========================================================================
# Prompts tests
# ===========================================================================

class TestPrompts:

    def test_coordinator_prompt_contains_key_sections(self):
        prompt = get_coordinator_system_prompt()
        assert "coordinator" in prompt.lower()
        assert "Agent" in prompt
        assert "SendMessage" in prompt
        assert "task-notification" in prompt
        assert "parallel" in prompt.lower()
        assert "timeout_s" in prompt  # new field documented in prompt

    def test_coordinator_prompt_with_worker_tools(self):
        prompt = get_coordinator_system_prompt(["Read", "Bash", "Edit"])
        assert "Read" in prompt
        assert "Bash" in prompt

    def test_worker_prompt_researcher(self):
        prompt = get_worker_system_prompt("researcher")
        assert "research" in prompt.lower()
        assert "not modify" in prompt.lower()

    def test_worker_prompt_verifier_no_spawn(self):
        prompt = get_worker_system_prompt("verifier")
        assert "VERDICT" in prompt
        assert "not spawn" in prompt.lower() or "do not spawn" in prompt.lower()

    def test_worker_prompt_general_with_spawn(self):
        prompt = get_worker_system_prompt("general-purpose", can_spawn_subagents=True)
        assert "sub-worker" in prompt.lower() or "sub-agent" in prompt.lower()

    def test_worker_prompt_general_without_spawn(self):
        prompt = get_worker_system_prompt("general-purpose", can_spawn_subagents=False)
        assert "worker agent" in prompt.lower()

    def test_worker_prompt_unknown_type_falls_back_to_base(self):
        prompt = get_worker_system_prompt("nonexistent-type")
        assert "worker agent" in prompt.lower()


# ===========================================================================
# create_swarm factory tests
# ===========================================================================

class TestCreateSwarm:

    def test_returns_swarm_instance(self):
        swarm = create_swarm()
        assert isinstance(swarm, Swarm)
        assert isinstance(swarm.registry, TaskRegistry)
        assert isinstance(swarm.coordinator, SwarmCoordinator)

    def test_swarm_with_custom_model(self):
        swarm = create_swarm(model="claude-sonnet-4-6", max_depth=2)
        assert swarm._model is not None
        assert swarm._max_depth == 2

    def test_create_session_returns_swarm_session(self):
        """create_session() should return a SwarmSession (factory wiring only — no API call)."""
        swarm = create_swarm()
        with patch("core.swarm.coordinator.SwarmSession") as MockSession:
            with patch("core.engine.Engine") as MockEngine:
                MockEngine.return_value = MagicMock()
                MockSession.return_value = MagicMock()
                assert hasattr(swarm, "create_session")
                assert callable(swarm.create_session)
