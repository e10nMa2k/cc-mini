"""Tests for sandbox/checker.py"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from features.sandbox.checker import DependencyCheck, check_dependencies


class TestDependencyCheck:
    def test_ok_when_no_errors(self):
        dc = DependencyCheck()
        assert dc.ok is True

    def test_not_ok_when_errors(self):
        dc = DependencyCheck(errors=["something wrong"])
        assert dc.ok is False

    def test_ok_with_warnings_only(self):
        dc = DependencyCheck(warnings=["minor issue"])
        assert dc.ok is True


class TestCheckDependencies:
    def test_non_linux_platform(self, monkeypatch):
        monkeypatch.setattr("features.sandbox.checker.platform.system", lambda: "Darwin")
        result = check_dependencies()
        assert not result.ok
        assert any("Linux" in e for e in result.errors)

    def test_bwrap_missing(self, monkeypatch):
        monkeypatch.setattr("features.sandbox.checker.platform.system", lambda: "Linux")
        monkeypatch.setattr("features.sandbox.checker.shutil.which", lambda x: None)
        result = check_dependencies()
        assert not result.ok
        assert any("bwrap" in e for e in result.errors)

    def test_userns_disabled(self, monkeypatch, tmp_path):
        monkeypatch.setattr("features.sandbox.checker.platform.system", lambda: "Linux")
        monkeypatch.setattr("features.sandbox.checker.shutil.which", lambda x: "/usr/bin/bwrap")
        userns_file = tmp_path / "unprivileged_userns_clone"
        userns_file.write_text("0")
        monkeypatch.setattr(
            "features.sandbox.checker.Path",
            lambda p: userns_file if "unprivileged_userns_clone" in str(p) else Path(p),
        )
        result = check_dependencies()
        assert not result.ok
        assert any("namespace" in e.lower() for e in result.errors)

    def test_bwrap_test_failure(self, monkeypatch):
        monkeypatch.setattr("features.sandbox.checker.platform.system", lambda: "Linux")
        monkeypatch.setattr("features.sandbox.checker.shutil.which", lambda x: "/usr/bin/bwrap")

        # Make Path read_text raise so userns check passes
        original_path = Path
        class FakePath(type(Path())):
            def read_text(self, *a, **kw):
                raise OSError("no such file")
        monkeypatch.setattr("features.sandbox.checker.Path", lambda p: FakePath(p))

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = b"some error"
        monkeypatch.setattr("features.sandbox.checker.subprocess.run", lambda *a, **kw: mock_proc)

        result = check_dependencies()
        assert not result.ok
        assert any("bwrap test failed" in e for e in result.errors)

    def test_bwrap_test_timeout(self, monkeypatch):
        monkeypatch.setattr("features.sandbox.checker.platform.system", lambda: "Linux")
        monkeypatch.setattr("features.sandbox.checker.shutil.which", lambda x: "/usr/bin/bwrap")

        original_path = Path
        class FakePath(type(Path())):
            def read_text(self, *a, **kw):
                raise OSError("no such file")
        monkeypatch.setattr("features.sandbox.checker.Path", lambda p: FakePath(p))

        def raise_timeout(*a, **kw):
            raise subprocess.TimeoutExpired("bwrap", 5)
        monkeypatch.setattr("features.sandbox.checker.subprocess.run", raise_timeout)

        result = check_dependencies()
        assert not result.ok
        assert any("timed out" in e for e in result.errors)
