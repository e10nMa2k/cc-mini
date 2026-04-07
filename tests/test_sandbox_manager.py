"""Tests for sandbox/manager.py"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from features.sandbox.config import SandboxConfig
from features.sandbox.checker import DependencyCheck
from features.sandbox.manager import SandboxManager


class TestIsEnabled:
    def test_disabled_by_default(self):
        mgr = SandboxManager()
        assert mgr.is_enabled() is False

    def test_enabled_with_deps_ok(self, monkeypatch):
        cfg = SandboxConfig(enabled=True)
        mgr = SandboxManager(config=cfg)
        monkeypatch.setattr(
            "features.sandbox.manager.check_dependencies",
            lambda: DependencyCheck(),
        )
        assert mgr.is_enabled() is True

    def test_enabled_but_deps_fail(self, monkeypatch):
        cfg = SandboxConfig(enabled=True)
        mgr = SandboxManager(config=cfg)
        monkeypatch.setattr(
            "features.sandbox.manager.check_dependencies",
            lambda: DependencyCheck(errors=["bwrap missing"]),
        )
        assert mgr.is_enabled() is False


class TestIsAutoAllow:
    def test_auto_allow_on(self):
        cfg = SandboxConfig(enabled=True, auto_allow_bash=True)
        mgr = SandboxManager(config=cfg)
        assert mgr.is_auto_allow() is True

    def test_auto_allow_off_when_disabled(self):
        cfg = SandboxConfig(enabled=False, auto_allow_bash=True)
        mgr = SandboxManager(config=cfg)
        assert mgr.is_auto_allow() is False


class TestShouldSandbox:
    def _make_mgr(self, monkeypatch, enabled=True, excluded=None):
        cfg = SandboxConfig(
            enabled=enabled,
            excluded_commands=excluded or [],
        )
        mgr = SandboxManager(config=cfg)
        monkeypatch.setattr(
            "features.sandbox.manager.check_dependencies",
            lambda: DependencyCheck(),
        )
        return mgr

    def test_disabled(self, monkeypatch):
        mgr = self._make_mgr(monkeypatch, enabled=False)
        assert mgr.should_sandbox("ls") is False

    def test_normal_command(self, monkeypatch):
        mgr = self._make_mgr(monkeypatch)
        assert mgr.should_sandbox("ls -la") is True

    def test_excluded_command(self, monkeypatch):
        mgr = self._make_mgr(monkeypatch, excluded=["docker *"])
        assert mgr.should_sandbox("docker build .") is False

    def test_empty_command(self, monkeypatch):
        mgr = self._make_mgr(monkeypatch)
        assert mgr.should_sandbox("") is False

    def test_dangerously_disable(self, monkeypatch):
        cfg = SandboxConfig(enabled=True, allow_unsandboxed=True)
        mgr = SandboxManager(config=cfg)
        monkeypatch.setattr(
            "features.sandbox.manager.check_dependencies",
            lambda: DependencyCheck(),
        )
        assert mgr.should_sandbox("rm -rf /", dangerously_disable=True) is False

    def test_dangerously_disable_not_allowed(self, monkeypatch):
        cfg = SandboxConfig(enabled=True, allow_unsandboxed=False)
        mgr = SandboxManager(config=cfg)
        monkeypatch.setattr(
            "features.sandbox.manager.check_dependencies",
            lambda: DependencyCheck(),
        )
        assert mgr.should_sandbox("rm -rf /", dangerously_disable=True) is True


class TestSetMode:
    def test_auto_allow(self):
        mgr = SandboxManager()
        msg = mgr.set_mode("auto-allow")
        assert mgr.config.enabled is True
        assert mgr.config.auto_allow_bash is True
        assert "auto-allow" in msg

    def test_regular(self):
        mgr = SandboxManager()
        msg = mgr.set_mode("regular")
        assert mgr.config.enabled is True
        assert mgr.config.auto_allow_bash is False

    def test_disabled(self):
        mgr = SandboxManager(config=SandboxConfig(enabled=True))
        msg = mgr.set_mode("disabled")
        assert mgr.config.enabled is False
        assert mgr.config.auto_allow_bash is False

    def test_unknown(self):
        mgr = SandboxManager()
        msg = mgr.set_mode("foobar")
        assert "Unknown" in msg


class TestAddExcluded:
    def test_add(self):
        mgr = SandboxManager()
        mgr.add_excluded_command("docker *")
        assert "docker *" in mgr.config.excluded_commands

    def test_no_duplicate(self):
        mgr = SandboxManager()
        mgr.add_excluded_command("docker *")
        mgr.add_excluded_command("docker *")
        assert mgr.config.excluded_commands.count("docker *") == 1


class TestDepCheckCache:
    def test_cached(self, monkeypatch):
        call_count = 0

        def counting_check():
            nonlocal call_count
            call_count += 1
            return DependencyCheck()

        monkeypatch.setattr(
            "features.sandbox.manager.check_dependencies", counting_check
        )
        mgr = SandboxManager(config=SandboxConfig(enabled=True))
        mgr.check_dependencies()
        mgr.check_dependencies()
        assert call_count == 1
