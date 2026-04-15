"""
coordinator.py — SwarmCoordinator and SwarmSession

Responsibilities:
1. Receive completion/failure callbacks from background agents
2. Assemble <task-notification> XML (mirrors enqueueAgentNotification())
3. Maintain a pending-notification queue for the main thread to drain
4. Provide SwarmSession — a thin Engine wrapper that injects notifications
   transparently before every submit() call

Mirrors in claude-code:
  enqueueAgentNotification()   → _build_notification()
  enqueuePendingNotification() → notification_queue
  SwarmSession                 → print.ts drainCommandQueue logic
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Iterator, TYPE_CHECKING

if TYPE_CHECKING:
    from core.engine import Engine
from .task import TaskRegistry, TaskStatus


class SwarmCoordinator:
    """
    Manages the task-notification lifecycle.

    Thread-safe: background agent threads call on_task_done() to write to the
    queue; the main thread reads via drain_notifications().
    """

    def __init__(self, registry: TaskRegistry) -> None:
        self._registry = registry
        self._notification_queue: queue.Queue[str] = queue.Queue()

    # ------------------------------------------------------------------
    # Callback — called from agent threads
    # ------------------------------------------------------------------

    def on_task_done(
        self,
        task_id: str,
        result: str | None,
        error: str | None,
    ) -> None:
        """
        Called when a background agent completes or fails.
        Updates the registry, assembles the notification XML, and enqueues it.
        Mirrors completeAgentTask / failAgentTask + enqueueAgentNotification.
        """
        state = self._registry.get(task_id)
        if state is None:
            return

        # Idempotency guard — notify exactly once per task
        if not self._registry.mark_notified(task_id):
            return

        if error is not None:
            self._registry.fail(task_id, error)
            status: TaskStatus = "failed"
        elif state.status == "killed":
            status = "killed"
        else:
            self._registry.complete(task_id, result or "")
            status = "completed"

        # Refresh state after registry update
        state = self._registry.get(task_id)
        duration_ms = int((time.time() - state.start_time) * 1000) if state else 0

        xml = self._build_notification(
            task_id=task_id,
            description=state.description if state else task_id,
            status=status,
            result=result,
            error=error,
            # Read the live count from the runner, not the stale TaskState field
            tool_use_count=state.runner.tool_use_count if state else 0,
            duration_ms=duration_ms,
            tool_use_id=state.tool_use_id if state else None,
        )
        self._notification_queue.put(xml)

    # ------------------------------------------------------------------
    # Main-thread reads
    # ------------------------------------------------------------------

    def drain_notifications(self) -> list[str]:
        """
        Non-blocking drain of all pending notifications.
        Mirrors removeFromQueue() + getCommandsByMaxPriority() in query.ts.
        """
        notifications: list[str] = []
        while True:
            try:
                notifications.append(self._notification_queue.get_nowait())
            except queue.Empty:
                break
        return notifications

    def has_pending(self) -> bool:
        return not self._notification_queue.empty()

    def get_running_count(self) -> int:
        """Return the number of currently running background agents."""
        return len(self._registry.get_running())

    # ------------------------------------------------------------------
    # XML assembly
    # ------------------------------------------------------------------

    @staticmethod
    def _build_notification(
        task_id: str,
        description: str,
        status: TaskStatus,
        result: str | None = None,
        error: str | None = None,
        tool_use_count: int = 0,
        duration_ms: int = 0,
        tool_use_id: str | None = None,
    ) -> str:
        """
        Assemble a <task-notification> XML string.
        Mirrors the message template in enqueueAgentNotification().

        Format matches the coordinatorMode.ts system-prompt documentation:
          <task-notification>
            <task-id>...</task-id>
            <status>completed|failed|killed</status>
            <summary>...</summary>
            <result>...</result>   (present on completion)
            <usage>...</usage>     (always present)
          </task-notification>
        """
        if status == "completed":
            summary = f'Agent "{description}" completed'
        elif status == "failed":
            summary = f'Agent "{description}" failed: {error or "Unknown error"}'
        else:
            summary = f'Agent "{description}" was stopped'

        tool_use_id_line = (
            f"\n<tool-use-id>{tool_use_id}</tool-use-id>" if tool_use_id else ""
        )
        result_section = f"\n<result>{result}</result>" if result else ""
        usage_section = (
            f"\n<usage>"
            f"<tool_uses>{tool_use_count}</tool_uses>"
            f"<duration_ms>{duration_ms}</duration_ms>"
            f"</usage>"
        )

        return (
            f"<task-notification>"
            f"\n<task-id>{task_id}</task-id>{tool_use_id_line}"
            f"\n<status>{status}</status>"
            f"\n<summary>{summary}</summary>"
            f"{result_section}{usage_section}"
            f"\n</task-notification>"
        )


class SwarmSession:
    """
    Thin wrapper around Engine that automatically injects pending
    task-notifications into message history before every submit() call.

    Usage:
        session = SwarmSession(engine, coordinator)
        for event in session.submit("user message"):
            ...

    Mirrors the drainCommandQueue logic in claude-code's print.ts:
    notifications are injected as user/assistant pairs so the coordinator
    model sees them in the same API request as the real user turn.
    """

    def __init__(self, engine: Engine, coordinator: SwarmCoordinator) -> None:
        self._engine = engine
        self._coordinator = coordinator

    def submit(self, user_input: str | list) -> Iterator[tuple]:
        """
        Drain any pending task-notifications into the engine message history,
        then submit user_input.

        Notifications are appended as user/assistant pairs directly to
        engine._messages without triggering an extra API call. The model
        sees them all in the next API request.

        Empty user_input handling:
          - With pending notifications: replaced with a review prompt so the
            model synthesises the results (avoids sending an empty user turn,
            which the Anthropic API rejects).
          - Without notifications: returns immediately (no-op — no API call).
        """
        notifications = self._coordinator.drain_notifications()
        for xml in notifications:
            # Append directly to message history — no API call here
            self._engine._messages.append({
                "role": "user",
                "content": xml,
            })
            # Placeholder acknowledgement to maintain user/assistant alternation
            self._engine._messages.append({
                "role": "assistant",
                "content": "(notification received)",
            })

        # Normalise empty user input
        is_empty = user_input == "" or (isinstance(user_input, list) and not user_input)
        if is_empty:
            if notifications:
                # Give the coordinator a concrete turn to synthesise the injected results
                effective_input: str | list = (
                    "Please review the background task results above and continue."
                )
            else:
                return  # Nothing injected and nothing submitted — no-op
        else:
            effective_input = user_input

        yield from self._engine.submit(effective_input)

    def wait_for_completion(
        self,
        timeout_s: float = 300.0,
        poll_interval_s: float = 0.5,
    ) -> Iterator[tuple]:
        """
        Poll until all background agents finish, yielding events from each
        notification-processing turn.

        Args:
            timeout_s:        Maximum seconds to wait before raising TimeoutError.
            poll_interval_s:  Sleep interval between polls when no notifications
                              are ready yet (seconds).

        Yields:
            Same event tuples as submit(): ("text", str), ("tool_result", ...), etc.

        Raises:
            TimeoutError if background agents do not complete within timeout_s.
        """
        deadline = time.time() + timeout_s

        while (
            self._coordinator.get_running_count() > 0
            or self._coordinator.has_pending()
        ):
            if time.time() > deadline:
                raise TimeoutError(
                    f"Background agents did not complete within {timeout_s:.0f}s. "
                    f"{self._coordinator.get_running_count()} agent(s) still running."
                )
            if self._coordinator.has_pending():
                yield from self.submit("")  # drain and let the coordinator react
            else:
                time.sleep(poll_interval_s)

        # Final drain in case the last notification arrived just as the loop exited
        if self._coordinator.has_pending():
            yield from self.submit("")

    def has_pending_notifications(self) -> bool:
        return self._coordinator.has_pending()

    # Passthrough to the underlying Engine
    def abort(self) -> None:
        self._engine.abort()

    def get_messages(self) -> list[dict]:
        return self._engine.get_messages()

    def last_assistant_text(self) -> str:
        return self._engine.last_assistant_text()
