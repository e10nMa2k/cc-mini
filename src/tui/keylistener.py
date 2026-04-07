"""Background thread that listens for the Escape key.

Opens /dev/tty directly (bypassing sys.stdin) so that prompt_toolkit's
terminal manipulation cannot interfere.  When ESC is detected the listener
calls an on_cancel callback that aborts the active HTTP stream immediately.
"""
from __future__ import annotations

import os
import signal
import sys
import select
import threading
from typing import Callable

try:
    import termios
    import tty
    _HAS_TERMIOS = True
except ImportError:
    _HAS_TERMIOS = False


class EscListener:
    """Context manager that listens for ESC in a daemon thread.

    While active the terminal is in cbreak mode.  Call ``pause()`` before
    reading interactive input (e.g. permission prompts) and ``resume()``
    after, so the listener thread does not steal keystrokes.
    """

    def __init__(self, on_cancel: Callable[[], None] | None = None):
        self.pressed = False
        self._on_cancel = on_cancel
        self._stop = threading.Event()
        self._paused = threading.Event()   # set = paused, clear = running
        self._thread: threading.Thread | None = None
        self._tty_fd: int | None = None       # dedicated /dev/tty fd
        self._old_settings = None

    # -- context manager --------------------------------------------------

    def __enter__(self):
        self.pressed = False
        self._stop.clear()
        self._paused.clear()

        # Open /dev/tty directly — independent of sys.stdin / prompt_toolkit
        try:
            self._tty_fd = os.open("/dev/tty", os.O_RDONLY | os.O_NOCTTY)
        except OSError:
            # Fallback to stdin fd if /dev/tty is not available
            self._tty_fd = sys.stdin.fileno()

        # Save terminal settings and switch to cbreak mode
        try:
            self._old_settings = termios.tcgetattr(self._tty_fd)
            tty.setcbreak(self._tty_fd)
        except termios.error:
            self._old_settings = None

        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        # Restore terminal
        if self._old_settings is not None and self._tty_fd is not None:
            try:
                termios.tcsetattr(self._tty_fd, termios.TCSADRAIN, self._old_settings)
            except termios.error:
                pass
            self._old_settings = None
        # Close our private fd (only if we opened /dev/tty ourselves)
        if self._tty_fd is not None and self._tty_fd > 2:
            try:
                os.close(self._tty_fd)
            except OSError:
                pass
        self._tty_fd = None

    # -- pause/resume for interactive input --------------------------------

    def pause(self):
        """Pause the listener so stdin can be read by permission prompts."""
        self._paused.set()

    def resume(self):
        """Resume listening after interactive input is done."""
        self._paused.clear()

    # -- non-blocking ESC check for main thread ----------------------------

    def check_esc_nonblocking(self) -> bool:
        """Return True if ESC was already detected by the background thread."""
        return self.pressed

    # -- internal ---------------------------------------------------------

    def _has_data(self, timeout: float) -> bool:
        if self._tty_fd is None:
            return False
        try:
            return bool(select.select([self._tty_fd], [], [], timeout)[0])
        except (OSError, ValueError):
            return False

    def _drain(self):
        while self._has_data(0.01):
            try:
                os.read(self._tty_fd, 64)
            except OSError:
                break

    def _listen(self):
        while not self._stop.is_set():
            if self._paused.is_set():
                self._stop.wait(0.05)
                continue

            if not self._has_data(0.1):
                continue
            if self._paused.is_set():
                continue

            try:
                b = os.read(self._tty_fd, 1)
            except OSError:
                break

            if not b:
                break

            if b == b'\x1b':
                if self._has_data(0.05):
                    self._drain()
                    continue
                # Genuine ESC — send SIGINT (same effect as Ctrl+C)
                self.pressed = True
                os.kill(os.getpid(), signal.SIGINT)
                return


# ---------------------------------------------------------------------------
# Windows fallback
# ---------------------------------------------------------------------------
if not _HAS_TERMIOS:
    import msvcrt

    class EscListener:  # type: ignore[no-redef]
        """Windows version using msvcrt."""

        def __init__(self, on_cancel: Callable[[], None] | None = None):
            self.pressed = False
            self._on_cancel = on_cancel
            self._stop = threading.Event()
            self._paused = threading.Event()
            self._thread: threading.Thread | None = None

        def __enter__(self):
            self.pressed = False
            self._stop.clear()
            self._paused.clear()
            self._thread = threading.Thread(target=self._listen, daemon=True)
            self._thread.start()
            return self

        def __exit__(self, *_exc):
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=0.5)

        def pause(self):
            self._paused.set()

        def resume(self):
            self._paused.clear()

        def check_esc_nonblocking(self) -> bool:
            if self.pressed:
                return True
            while msvcrt.kbhit():
                if msvcrt.getch() == b'\x1b':
                    self.pressed = True
                    if self._on_cancel:
                        self._on_cancel()
                    return True
            return False

        def _listen(self):
            while not self._stop.is_set():
                if self._paused.is_set():
                    self._stop.wait(0.05)
                    continue
                if not msvcrt.kbhit():
                    self._stop.wait(0.05)
                    continue
                if self._paused.is_set():
                    continue
                if msvcrt.getch() == b'\x1b':
                    self.pressed = True
                    if self._on_cancel:
                        self._on_cancel()
                    return
