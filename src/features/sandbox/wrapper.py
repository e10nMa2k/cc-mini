"""Generate bwrap command lines to wrap user commands in a sandbox.

bwrap key arguments:
- --ro-bind src dest : read-only mount
- --bind src dest    : read-write mount
- --dev /dev         : minimal /dev
- --proc /proc       : mount /proc
- --tmpfs /tmp       : temporary filesystem
- --unshare-net      : isolate network namespace
- --die-with-parent  : kill child when parent exits
- -- /bin/sh -c CMD  : execute command in sandbox

Corresponds to:
- sandbox-adapter.ts convertToSandboxRuntimeConfig (lines 172-381)
- sandbox-adapter.ts wrapWithSandbox (lines 704-725)
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path

from .config import SandboxConfig


def build_bwrap_args(
    command: str,
    config: SandboxConfig,
    cwd: str | None = None,
) -> list[str]:
    """Build complete bwrap argument list from config.

    Returned list can be passed directly to subprocess.run().

    Mount order matters: bwrap processes args in order, later overrides earlier.
    Strategy: --ro-bind / / global read-only -> --bind for write access -> --ro-bind to protect specific files
    """
    cwd = cwd or os.getcwd()
    args = ["bwrap"]

    # === Base mounts ===
    args.extend(["--ro-bind", "/", "/"])  # Global read-only
    args.extend(["--dev", "/dev"])  # Minimal /dev
    args.extend(["--proc", "/proc"])  # /proc
    args.extend(["--tmpfs", "/tmp"])  # Temporary filesystem

    # === Writable directories ===
    fs = config.filesystem
    for write_path in _resolve_paths(fs.allow_write, cwd):
        if os.path.exists(write_path):
            args.extend(["--bind", write_path, write_path])

    # === Deny write (force read-only even within allow_write) ===
    # Corresponds to sandbox-adapter.ts:230-255
    for deny_path in _resolve_paths(fs.deny_write, cwd):
        if os.path.exists(deny_path):
            args.extend(["--ro-bind", deny_path, deny_path])

    # === Deny read (mask with empty tmpfs) ===
    for deny_path in _resolve_paths(fs.deny_read, cwd):
        if os.path.exists(deny_path):
            args.extend(["--tmpfs", deny_path])

    # === Working directory ===
    args.extend(["--bind", cwd, cwd])
    args.extend(["--chdir", cwd])

    # === Network isolation ===
    if config.unshare_net:
        args.append("--unshare-net")

    # === Security options ===
    args.append("--die-with-parent")
    args.append("--unshare-pid")

    # === Settings file protection ===
    # Corresponds to sandbox-adapter.ts:230-236
    for protected in _get_protected_paths(cwd):
        if os.path.exists(protected):
            args.extend(["--ro-bind", protected, protected])

    # === Execute command ===
    args.extend(["--", "/bin/sh", "-c", command])

    return args


def wrap_command(
    command: str,
    config: SandboxConfig,
    cwd: str | None = None,
) -> str:
    """Wrap a command as a bwrap sandbox command string.

    Corresponds to wrapWithSandbox (sandbox-adapter.ts:704-725).
    Returns a string suitable for shell=True execution.
    """
    bwrap_args = build_bwrap_args(command, config, cwd)
    return " ".join(shlex.quote(a) for a in bwrap_args)


def _resolve_paths(patterns: list[str], cwd: str) -> list[str]:
    """Resolve path patterns to absolute paths.

    Rules (corresponds to resolveSandboxFilesystemPath):
    - "."  -> cwd
    - "~/" -> user home directory
    - "/" prefix -> absolute path
    - other -> relative to cwd
    """
    resolved = []
    for p in patterns:
        if p == ".":
            resolved.append(cwd)
        elif p.startswith("~/"):
            resolved.append(str(Path.home() / p[2:]))
        elif p.startswith("/"):
            resolved.append(p)
        else:
            resolved.append(str(Path(cwd) / p))
    return resolved


def _get_protected_paths(cwd: str) -> list[str]:
    """Return paths that must be read-only protected inside sandbox.

    Corresponds to sandbox-adapter.ts:230-255:
    - .cc-mini.toml (project config)
    - ~/.config/cc-mini/config.toml (global config)
    - CLAUDE.md (should not be modified by sandbox)
    """
    paths = []
    local_config = Path(cwd) / ".cc-mini.toml"
    if local_config.exists():
        paths.append(str(local_config))
    global_config = Path.home() / ".config" / "cc-mini" / "config.toml"
    if global_config.exists():
        paths.append(str(global_config))
    claude_md = Path(cwd) / "CLAUDE.md"
    if claude_md.exists():
        paths.append(str(claude_md))
    return paths
