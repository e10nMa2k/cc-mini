"""Tests for sandbox/config.py"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from features.sandbox.config import (
    SandboxConfig,
    SandboxFilesystemConfig,
    load_sandbox_config,
    save_sandbox_config,
    _dict_to_config,
)


class TestDefaults:
    def test_default_config(self):
        cfg = SandboxConfig()
        assert cfg.enabled is False
        assert cfg.auto_allow_bash is False
        assert cfg.allow_unsandboxed is False
        assert cfg.excluded_commands == []
        assert cfg.unshare_net is True

    def test_filesystem_config_defaults(self):
        fs = SandboxFilesystemConfig()
        assert fs.allow_write == ["."]
        assert fs.deny_write == []
        assert fs.deny_read == []
        assert fs.allow_read == []


class TestLoadFromToml:
    def test_load_from_toml(self, tmp_path: Path):
        toml_file = tmp_path / "config.toml"
        toml_file.write_text(
            '[sandbox]\n'
            'enabled = true\n'
            'auto_allow_bash = true\n'
            'excluded_commands = ["docker *", "npm run *"]\n'
            'unshare_net = false\n'
            '\n'
            '[sandbox.filesystem]\n'
            'allow_write = [".", "build/"]\n'
            'deny_write = ["/etc"]\n'
        )
        cfg = load_sandbox_config((toml_file,))
        assert cfg.enabled is True
        assert cfg.auto_allow_bash is True
        assert cfg.excluded_commands == ["docker *", "npm run *"]
        assert cfg.unshare_net is False
        assert cfg.filesystem.allow_write == [".", "build/"]
        assert cfg.filesystem.deny_write == ["/etc"]

    def test_load_missing_section(self, tmp_path: Path):
        toml_file = tmp_path / "config.toml"
        toml_file.write_text('[anthropic]\napi_key = "sk-test"\n')
        cfg = load_sandbox_config((toml_file,))
        assert cfg.enabled is False
        assert cfg.excluded_commands == []

    def test_load_nonexistent_file(self, tmp_path: Path):
        cfg = load_sandbox_config((tmp_path / "nope.toml",))
        assert cfg.enabled is False

    def test_load_malformed_toml(self, tmp_path: Path):
        toml_file = tmp_path / "bad.toml"
        toml_file.write_text("not valid toml {{{{")
        cfg = load_sandbox_config((toml_file,))
        assert cfg.enabled is False


class TestSave:
    def test_save_creates_file(self, tmp_path: Path):
        cfg = SandboxConfig(enabled=True, auto_allow_bash=True)
        target = tmp_path / "sub" / "config.toml"
        save_sandbox_config(cfg, target)
        assert target.exists()
        content = target.read_text()
        assert "enabled = true" in content
        assert "auto_allow_bash = true" in content

    def test_save_preserves_other_sections(self, tmp_path: Path):
        toml_file = tmp_path / "config.toml"
        toml_file.write_text(
            '[anthropic]\napi_key = "sk-test"\n'
        )
        cfg = SandboxConfig(enabled=True)
        save_sandbox_config(cfg, toml_file)
        content = toml_file.read_text()
        assert 'api_key = "sk-test"' in content
        assert "enabled = true" in content

    def test_round_trip(self, tmp_path: Path):
        cfg = SandboxConfig(
            enabled=True,
            auto_allow_bash=True,
            excluded_commands=["docker *"],
            filesystem=SandboxFilesystemConfig(
                allow_write=[".", "build/"],
                deny_write=["/etc"],
            ),
        )
        target = tmp_path / "rt.toml"
        save_sandbox_config(cfg, target)
        loaded = load_sandbox_config((target,))
        assert loaded.enabled == cfg.enabled
        assert loaded.auto_allow_bash == cfg.auto_allow_bash
        assert loaded.excluded_commands == cfg.excluded_commands
        assert loaded.filesystem.allow_write == cfg.filesystem.allow_write
        assert loaded.filesystem.deny_write == cfg.filesystem.deny_write
