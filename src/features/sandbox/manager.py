"""Sandbox manager: unified external interface.

Corresponds to sandbox-adapter.ts ISandboxManager interface (lines 880-922).
Coordinates config/checker/wrapper/command_matcher sub-modules.
"""

from __future__ import annotations

from pathlib import Path

from .config import SandboxConfig, load_sandbox_config, save_sandbox_config
from .checker import DependencyCheck, check_dependencies
from .command_matcher import contains_excluded_command
from .wrapper import wrap_command, build_bwrap_args


class SandboxManager:
    """Sandbox manager.

    Lifecycle: created once in main.py, lives for the entire REPL session.
    """

    def __init__(self, config: SandboxConfig | None = None):
        self._config = config or SandboxConfig()
        self._dep_check: DependencyCheck | None = None

    @property
    def config(self) -> SandboxConfig:
        return self._config

    # === State queries ===

    def is_enabled(self) -> bool:
        """Whether sandbox is actually usable.

        Corresponds to isSandboxingEnabled (sandbox-adapter.ts:532-547).
        """
        if not self._config.enabled:
            return False
        return self.check_dependencies().ok

    def is_auto_allow(self) -> bool:
        """Whether in auto-allow mode.

        Corresponds to isAutoAllowBashIfSandboxedEnabled().
        """
        return self._config.enabled and self._config.auto_allow_bash

    def check_dependencies(self) -> DependencyCheck:
        """Check dependencies (cached per session).

        Corresponds to memoized checkDependencies in original.
        """
        if self._dep_check is None:
            self._dep_check = check_dependencies()
        return self._dep_check

    # === Command decisions ===

    def should_sandbox(
        self, command: str, dangerously_disable: bool = False
    ) -> bool:
        """Determine whether a command should run in sandbox.

        Corresponds to shouldUseSandbox (shouldUseSandbox.ts:130-153).
        """
        if not self.is_enabled():
            return False
        if dangerously_disable and self._config.allow_unsandboxed:
            return False
        if not command:
            return False
        if contains_excluded_command(command, self._config.excluded_commands):
            return False
        return True

    # === Command wrapping ===

    def wrap(self, command: str, cwd: str | None = None) -> str:
        """Wrap command for sandbox execution.

        Corresponds to wrapWithSandbox (sandbox-adapter.ts:704-725).
        """
        return wrap_command(command, self._config, cwd)

    def build_args(self, command: str, cwd: str | None = None) -> list[str]:
        """Build bwrap argument list (for shell=False execution)."""
        return build_bwrap_args(command, self._config, cwd)

    # === Settings modification ===

    def set_mode(self, mode: str) -> str:
        """Set sandbox mode.

        Corresponds to SandboxSettings.tsx handleSelect.
        mode: "auto-allow" | "regular" | "disabled"
        """
        if mode == "auto-allow":
            self._config.enabled = True
            self._config.auto_allow_bash = True
            return "Sandbox enabled with auto-allow for bash commands"
        elif mode == "regular":
            self._config.enabled = True
            self._config.auto_allow_bash = False
            return "Sandbox enabled with regular bash permissions"
        elif mode == "disabled":
            self._config.enabled = False
            self._config.auto_allow_bash = False
            return "Sandbox disabled"
        else:
            return f"Unknown mode: {mode}"

    def add_excluded_command(self, pattern: str) -> str:
        """Add an excluded command pattern.

        Corresponds to /sandbox exclude sub-command.
        """
        if pattern not in self._config.excluded_commands:
            self._config.excluded_commands.append(pattern)
        return f"Added excluded pattern: {pattern}"

    def save(self, path: Path | None = None) -> None:
        """Persist current config to TOML file."""
        target = path or (Path.cwd() / ".cc-mini.toml")
        save_sandbox_config(self._config, target)
