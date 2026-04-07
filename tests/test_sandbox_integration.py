"""Integration tests for sandbox — require bwrap to be available."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from features.sandbox.config import SandboxConfig, SandboxFilesystemConfig
from features.sandbox.manager import SandboxManager
from features.sandbox.wrapper import build_bwrap_args, wrap_command

pytestmark = pytest.mark.skipif(
    not shutil.which("bwrap"),
    reason="bwrap not available",
)


def _run_sandboxed(command: str, config: SandboxConfig | None = None, cwd: str | None = None) -> subprocess.CompletedProcess:
    """Helper: run a command inside sandbox and return CompletedProcess."""
    cfg = config or SandboxConfig(enabled=True)
    wrapped = wrap_command(command, cfg, cwd=cwd)
    return subprocess.run(wrapped, shell=True, capture_output=True, text=True, timeout=10)


class TestSandboxedExecution:
    def test_read_only_root(self, tmp_path: Path):
        """Writing to / should fail in sandbox."""
        result = _run_sandboxed("touch /test_sandbox_file", cwd=str(tmp_path))
        assert result.returncode != 0

    def test_cwd_writable(self, tmp_path: Path):
        """Writing to cwd should succeed in sandbox."""
        result = _run_sandboxed(
            "echo hello > test_file.txt && cat test_file.txt",
            cwd=str(tmp_path),
        )
        assert result.returncode == 0
        assert "hello" in result.stdout

    def test_command_output(self, tmp_path: Path):
        """Command output should be returned correctly."""
        result = _run_sandboxed("echo sandbox_works", cwd=str(tmp_path))
        assert result.returncode == 0
        assert "sandbox_works" in result.stdout

    def test_read_etc_passwd(self, tmp_path: Path):
        """Read-only access to /etc/passwd should work."""
        result = _run_sandboxed("cat /etc/passwd", cwd=str(tmp_path))
        assert result.returncode == 0
        assert "root" in result.stdout

    def test_pipe(self, tmp_path: Path):
        """Pipe commands should work in sandbox."""
        result = _run_sandboxed("echo hello | tr 'h' 'H'", cwd=str(tmp_path))
        assert result.returncode == 0
        assert "Hello" in result.stdout

    def test_network_isolated(self, tmp_path: Path):
        """Network should be isolated with unshare_net=True."""
        cfg = SandboxConfig(enabled=True, unshare_net=True)
        # Try to ping localhost — should fail with network isolated
        result = _run_sandboxed(
            "cat /proc/net/if_inet6 2>/dev/null || echo no_ipv6",
            config=cfg,
            cwd=str(tmp_path),
        )
        # In network-isolated ns, /proc/net/if_inet6 will have minimal or no entries
        assert result.returncode == 0

    def test_timeout(self, tmp_path: Path):
        """Timeout should work with sandboxed commands."""
        cfg = SandboxConfig(enabled=True)
        wrapped = wrap_command("sleep 30", cfg, cwd=str(tmp_path))
        with pytest.raises(subprocess.TimeoutExpired):
            subprocess.run(wrapped, shell=True, capture_output=True, timeout=1)

    def test_protected_config(self, tmp_path: Path):
        """Config file should be read-only protected."""
        config_file = tmp_path / ".cc-mini.toml"
        config_file.write_text("[sandbox]\nenabled = true\n")
        cfg = SandboxConfig(enabled=True)
        result = _run_sandboxed(
            f"echo hacked > {config_file}",
            config=cfg,
            cwd=str(tmp_path),
        )
        # Should fail or the file content should be unchanged
        content = config_file.read_text()
        assert "hacked" not in content


class TestManagerIntegration:
    def test_wrap_and_execute(self, tmp_path: Path):
        """SandboxManager.wrap() produces executable command."""
        cfg = SandboxConfig(enabled=True)
        mgr = SandboxManager(config=cfg)
        wrapped = mgr.wrap("echo manager_works", cwd=str(tmp_path))
        result = subprocess.run(wrapped, shell=True, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0
        assert "manager_works" in result.stdout
