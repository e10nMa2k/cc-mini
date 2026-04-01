"""Cross-terminal single-instance lock for Idle Adventure.

Only one IA session can run at a time across all terminals.
Uses PID + heartbeat to detect stale locks.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

_CONFIG_DIR = Path.home() / ".config" / "mini-claude"
_LOCK_FILE = _CONFIG_DIR / "ia_game.lock"
_HEARTBEAT_INTERVAL = 30  # seconds
_HEARTBEAT_TIMEOUT = 60   # seconds — stale if older


def _pid_alive(pid: int) -> bool:
    """Check if a process with given PID is still running."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def acquire_lock() -> bool:
    """Try to acquire the game lock.

    Returns True if lock acquired, False if another session is active.
    """
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if _LOCK_FILE.exists():
        try:
            data = json.loads(_LOCK_FILE.read_text(encoding="utf-8"))
            pid = data.get("pid", -1)
            heartbeat = data.get("heartbeat", 0)
            # Check if the owning process is still alive AND heartbeat is fresh
            if _pid_alive(pid) and (time.time() - heartbeat) < _HEARTBEAT_TIMEOUT:
                return False  # Another active session
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            pass  # Corrupted lock file — safe to overwrite

    # Write new lock
    lock_data = {
        "pid": os.getpid(),
        "started_at": time.time(),
        "heartbeat": time.time(),
    }
    try:
        _LOCK_FILE.write_text(json.dumps(lock_data), encoding="utf-8")
    except OSError:
        return False
    return True


def release_lock() -> None:
    """Release the game lock."""
    try:
        if _LOCK_FILE.exists():
            # Only delete if we own it
            data = json.loads(_LOCK_FILE.read_text(encoding="utf-8"))
            if data.get("pid") == os.getpid():
                _LOCK_FILE.unlink(missing_ok=True)
    except (json.JSONDecodeError, OSError):
        # Best effort
        try:
            _LOCK_FILE.unlink(missing_ok=True)
        except OSError:
            pass


def update_heartbeat() -> None:
    """Update the heartbeat timestamp in the lock file."""
    try:
        if not _LOCK_FILE.exists():
            return
        data = json.loads(_LOCK_FILE.read_text(encoding="utf-8"))
        if data.get("pid") == os.getpid():
            data["heartbeat"] = time.time()
            _LOCK_FILE.write_text(json.dumps(data), encoding="utf-8")
    except (json.JSONDecodeError, OSError):
        pass
