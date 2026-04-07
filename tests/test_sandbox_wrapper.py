"""Tests for sandbox/wrapper.py"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from features.sandbox.config import SandboxConfig, SandboxFilesystemConfig
from features.sandbox.wrapper import (
    build_bwrap_args,
    wrap_command,
    _resolve_paths,
    _get_protected_paths,
)


class TestBuildBwrapArgs:
    def test_basic_args(self):
        cfg = SandboxConfig(enabled=True)
        args = build_bwrap_args("echo hello", cfg, cwd="/tmp/test")
        assert args[0] == "bwrap"
        assert "--ro-bind" in args
        assert "--die-with-parent" in args
        assert "--unshare-pid" in args

    def test_command_in_args(self):
        cfg = SandboxConfig(enabled=True)
        args = build_bwrap_args("echo hello", cfg, cwd="/tmp/test")
        # Command should appear after -- /bin/sh -c
        idx = args.index("--")
        assert args[idx + 1] == "/bin/sh"
        assert args[idx + 2] == "-c"
        assert args[idx + 3] == "echo hello"

    def test_writable_cwd(self):
        cfg = SandboxConfig(enabled=True)
        args = build_bwrap_args("ls", cfg, cwd="/tmp/test")
        # cwd should be --bind mounted
        pairs = list(zip(args, args[1:], args[2:]))
        bind_pairs = [(a, b, c) for a, b, c in pairs if a == "--bind"]
        cwd_bound = any(b == "/tmp/test" and c == "/tmp/test" for a, b, c in bind_pairs)
        assert cwd_bound

    def test_unshare_net_enabled(self):
        cfg = SandboxConfig(enabled=True, unshare_net=True)
        args = build_bwrap_args("ls", cfg, cwd="/tmp/test")
        assert "--unshare-net" in args

    def test_unshare_net_disabled(self):
        cfg = SandboxConfig(enabled=True, unshare_net=False)
        args = build_bwrap_args("ls", cfg, cwd="/tmp/test")
        assert "--unshare-net" not in args

    def test_deny_write_paths(self, tmp_path: Path):
        deny_dir = tmp_path / "protected"
        deny_dir.mkdir()
        cfg = SandboxConfig(
            enabled=True,
            filesystem=SandboxFilesystemConfig(deny_write=[str(deny_dir)]),
        )
        args = build_bwrap_args("ls", cfg, cwd=str(tmp_path))
        # deny_write path should appear as --ro-bind <path> <path>
        deny_str = str(deny_dir)
        found = False
        for i in range(len(args) - 2):
            if args[i] == "--ro-bind" and args[i + 1] == deny_str and args[i + 2] == deny_str:
                found = True
                break
        assert found, f"Expected --ro-bind {deny_str} {deny_str} in args"

    def test_deny_read_paths(self, tmp_path: Path):
        secret_dir = tmp_path / "secret"
        secret_dir.mkdir()
        cfg = SandboxConfig(
            enabled=True,
            filesystem=SandboxFilesystemConfig(deny_read=[str(secret_dir)]),
        )
        args = build_bwrap_args("ls", cfg, cwd=str(tmp_path))
        # deny_read should appear as --tmpfs
        idx = args.index(str(secret_dir))
        assert args[idx - 1] == "--tmpfs"

    def test_chdir_set(self):
        cfg = SandboxConfig(enabled=True)
        args = build_bwrap_args("ls", cfg, cwd="/tmp/test")
        idx = args.index("--chdir")
        assert args[idx + 1] == "/tmp/test"


class TestWrapCommand:
    def test_returns_string(self):
        cfg = SandboxConfig(enabled=True)
        result = wrap_command("echo hello", cfg, cwd="/tmp/test")
        assert isinstance(result, str)
        assert "bwrap" in result
        assert "echo hello" in result

    def test_shell_quoting(self):
        cfg = SandboxConfig(enabled=True)
        result = wrap_command("echo 'hello world'", cfg, cwd="/tmp/test")
        assert "bwrap" in result


class TestResolvePaths:
    def test_dot(self):
        assert _resolve_paths(["."], "/my/cwd") == ["/my/cwd"]

    def test_tilde(self):
        home = str(Path.home())
        result = _resolve_paths(["~/foo"], "/cwd")
        assert result == [f"{home}/foo"]

    def test_absolute(self):
        assert _resolve_paths(["/etc"], "/cwd") == ["/etc"]

    def test_relative(self):
        assert _resolve_paths(["build"], "/my/cwd") == ["/my/cwd/build"]


class TestProtectedPaths:
    def test_with_config_file(self, tmp_path: Path):
        (tmp_path / ".cc-mini.toml").touch()
        paths = _get_protected_paths(str(tmp_path))
        assert str(tmp_path / ".cc-mini.toml") in paths

    def test_with_claude_md(self, tmp_path: Path):
        (tmp_path / "CLAUDE.md").touch()
        paths = _get_protected_paths(str(tmp_path))
        assert str(tmp_path / "CLAUDE.md") in paths

    def test_no_files(self, tmp_path: Path):
        paths = _get_protected_paths(str(tmp_path))
        assert len(paths) == 0
