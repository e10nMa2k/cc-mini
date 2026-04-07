"""Detect system dependencies required for sandbox operation.

Corresponds to sandbox-adapter.ts checkDependencies() (lines 451-457)
and SandboxDependenciesTab.tsx display logic.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DependencyCheck:
    """Dependency check result.

    Corresponds to SandboxDependencyCheck type.
    errors: fatal issues (sandbox cannot run)
    warnings: non-fatal issues (can degrade gracefully)
    """

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


def check_dependencies() -> DependencyCheck:
    """Check whether sandbox dependencies are satisfied.

    Checks:
    1. Platform — Linux only
    2. bwrap (bubblewrap) — required
    3. User namespace support — required (some kernels/containers disable it)
    4. bwrap runtime test
    """
    result = DependencyCheck()

    # 0. Platform check
    if platform.system() != "Linux":
        result.errors.append(
            f"Sandbox requires Linux (current: {platform.system()})"
        )
        return result

    # 1. bwrap binary
    if not shutil.which("bwrap"):
        result.errors.append(
            "bubblewrap (bwrap) not found. Install: apt install bubblewrap"
        )
        return result

    # 2. User namespace support
    userns_path = Path("/proc/sys/kernel/unprivileged_userns_clone")
    try:
        val = userns_path.read_text().strip()
        if val == "0":
            result.errors.append(
                "User namespaces disabled (unprivileged_userns_clone=0). "
                "Sandbox requires user namespace support."
            )
            return result
    except OSError:
        pass  # File not found means kernel allows by default

    # 3. Actual bwrap runtime test
    try:
        proc = subprocess.run(
            ["bwrap", "--ro-bind", "/", "/", "--", "/bin/true"],
            capture_output=True,
            timeout=5,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode(errors="replace").strip()
            result.errors.append(f"bwrap test failed: {stderr}")
    except subprocess.TimeoutExpired:
        result.errors.append("bwrap test timed out")
    except (FileNotFoundError, OSError) as e:
        result.errors.append(f"bwrap test failed: {e}")

    return result
