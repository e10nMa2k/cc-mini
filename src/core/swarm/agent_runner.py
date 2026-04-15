"""
agent_runner.py — Run a child Engine instance in a background thread.

Mirrors claude-code's runAsyncAgentLifecycle() + LocalAgentTask execution path.
cc-mini uses a synchronous Engine, so threading.Thread replaces the void async closure.
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from core.engine import Engine


class AgentRunner:
    """
    Manages the full lifecycle of a single background agent:
    - Runs Engine.submit() in an isolated thread
    - Supports mid-execution message injection (SendMessage)
    - Supports abort (TaskStop → engine.abort())
    - Supports execution timeout via an optional timer thread
    - Fires on_done callback exactly once when the agent finishes, fails, or is stopped

    Mirrors claude-code's runAsyncAgentLifecycle + AgentRunner pattern.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._thread: threading.Thread | None = None
        self._timer: threading.Timer | None = None
        self._pending_messages: queue.Queue[str] = queue.Queue()
        self._on_done: Callable[[str, str | None, str | None], None] | None = None

        self._stopped = False
        self._tool_use_count = 0
        self._result_text: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(
        self,
        task_id: str,
        prompt: str,
        on_done: Callable[[str, str | None, str | None], None],
        timeout_s: float | None = None,
    ) -> None:
        """
        Launch the agent in a background thread.
        on_done(task_id, result, error) is called exactly once when the thread exits.

        Args:
            task_id:    Registry task ID for this agent.
            prompt:     Initial prompt submitted to the engine.
            on_done:    Completion callback — fires regardless of outcome.
            timeout_s:  If set, the agent is aborted after this many seconds and
                        the stop is treated as a clean kill (error=None).

        Mirrors the fire-and-forget pattern in runWithAgentContext().
        """
        self._on_done = on_done

        # Build the timer object *before* starting the thread so that
        # the thread's finally-block can always see and cancel it.
        # If the agent finishes before start() sets _timer, the finally-block
        # would miss the reference and the timer would leak.
        if timeout_s is not None and timeout_s > 0:
            self._timer = threading.Timer(timeout_s, self._on_timeout)
            self._timer.daemon = True

        self._thread = threading.Thread(
            target=self._run,
            args=(task_id, prompt),
            daemon=True,
            name=f"agent-{task_id}",
        )
        self._thread.start()

        # Start the timer only if the thread's finally-block hasn't cleared it
        # already (possible when the agent finishes before we reach this line).
        if self._timer is not None:
            self._timer.start()

    def send_message(self, message: str) -> None:
        """
        Inject a message into the running agent's queue.
        The agent processes it after completing its current tool round.
        Mirrors queuePendingMessage() + drainPendingMessages().
        """
        self._pending_messages.put(message)

    def stop(self) -> None:
        """
        Abort the agent execution.
        Mirrors killAsyncAgent() → abortController.abort().
        """
        self._stopped = True
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._engine.abort()

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def wait(self, timeout: float | None = None) -> None:
        """Wait for the thread to finish (used in synchronous contexts)."""
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    @property
    def tool_use_count(self) -> int:
        return self._tool_use_count

    @property
    def result_text(self) -> str | None:
        return self._result_text

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_timeout(self) -> None:
        """Timer callback: abort the agent when the deadline is exceeded."""
        if not self._stopped and self._thread is not None and self._thread.is_alive():
            self.stop()

    def _run(self, task_id: str, prompt: str) -> None:
        """
        Thread entry point: submit the initial prompt, then process any
        follow-up messages injected via send_message().

        All text blocks are collected as the final result.
        Each batch of pending messages triggers an additional Engine.submit() round.

        AbortedError (raised when engine.abort() is called) is treated as a
        clean stop — error is left as None rather than propagated as a failure.
        """
        result_parts: list[str] = []
        error: str | None = None

        try:
            current_prompt: str | list = prompt

            while True:
                for event in self._engine.submit(current_prompt):
                    if self._stopped:
                        break

                    kind = event[0]
                    if kind == "text":
                        result_parts.append(event[1])
                    elif kind == "tool_result":
                        self._tool_use_count += 1

                if self._stopped:
                    break

                # Drain any messages queued by SendMessage
                pending: list[str] = []
                while not self._pending_messages.empty():
                    try:
                        pending.append(self._pending_messages.get_nowait())
                    except queue.Empty:
                        break

                if not pending:
                    break  # No follow-up messages — agent is done

                # Combine multiple queued messages into a single user turn
                current_prompt = "\n\n".join(pending)

        except Exception as exc:
            from core.engine import AbortedError
            if isinstance(exc, AbortedError) or self._stopped:
                error = None  # Intentional stop — not a failure
            else:
                error = str(exc)
        finally:
            # Cancel the timeout timer if it hasn't fired yet
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

        self._result_text = "".join(result_parts) if result_parts else None

        if self._on_done is not None:
            self._on_done(task_id, self._result_text, error)
