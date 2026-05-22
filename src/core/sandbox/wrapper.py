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

"""
Bubblewrap 是一个轻量级的 Linux 沙盒工具，由 Flatpak 项目开发，专注于为非特权用户提供安全的容器隔离能力。它利用 Linux 内核的命名空间（namespaces）技术创建隔离的执行环境，但相比 Docker 等完整容器方案，Bubblewrap 的代码库极小、设计简洁、启动速度极快


这个代码构建了一个安全的命令执行沙箱，使用 bwrap (Bubblewrap) 来隔离和控制命令的访问权限。

真实文件系统                    沙箱视图
/ (全局)          →    / (只读)
/home/user/docs   →    /home/user/docs (可写，如果在allow_write中)
/etc/passwd       →    /etc/passwd (只读，即使在allow_write中)
/secret/data      →    /secret/data (空目录，不可见)


config = SandboxConfig(
    filesystem=FilesystemConfig(
        allow_write=["./output", "./temp"],      # 只允许写入这些目录
        deny_write=["./config", "./.env"],       # 强制只读敏感文件
        deny_read=["./secret", "./keys"],        # 完全隐藏机密文件
    ),
    unshare_net=True,  # 禁用网络
)

command = "python script.py --process-data"
bwrap_cmd = wrap_command(command, config, cwd="/home/user/project")

# 实际执行的命令：
# bwrap --ro-bind / / --dev /dev --proc /proc --tmpfs /tmp \
#   --bind /home/user/project/output /home/user/project/output \
#   --ro-bind /home/user/project/.env /home/user/project/.env \
#   --tmpfs /home/user/project/secret \
#   --bind /home/user/project /home/user/project --chdir /home/user/project \
#   --unshare-net --die-with-parent --unshare-pid \
#   --ro-bind /home/user/project/.cc-mini.toml /home/user/project/.cc-mini.toml \
#   -- /bin/sh -c "python script.py --process-data"
"""
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
