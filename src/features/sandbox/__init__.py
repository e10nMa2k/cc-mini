"""Sandbox subsystem for cc-mini.

Provides bubblewrap (bwrap) based command isolation for BashTool.
"""

from .config import SandboxConfig, SandboxFilesystemConfig, load_sandbox_config, save_sandbox_config
from .checker import DependencyCheck, check_dependencies
from .command_matcher import contains_excluded_command
from .wrapper import build_bwrap_args, wrap_command
from .manager import SandboxManager

__all__ = [
    "SandboxConfig",
    "SandboxFilesystemConfig",
    "load_sandbox_config",
    "save_sandbox_config",
    "DependencyCheck",
    "check_dependencies",
    "contains_excluded_command",
    "build_bwrap_args",
    "wrap_command",
    "SandboxManager",
]
