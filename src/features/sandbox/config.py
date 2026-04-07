"""Sandbox configuration dataclasses and TOML persistence.

Corresponds to sandboxTypes.ts SandboxSettingsSchema (lines 91-144).
"""

from __future__ import annotations

import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SandboxFilesystemConfig:
    """Filesystem restriction configuration.

    Corresponds to SandboxFilesystemConfigSchema (sandboxTypes.ts:50-80).
    """

    allow_write: list[str] = field(default_factory=lambda: ["."])
    deny_write: list[str] = field(default_factory=list)
    deny_read: list[str] = field(default_factory=list)
    allow_read: list[str] = field(default_factory=list)


@dataclass
class SandboxConfig:
    """Top-level sandbox configuration.

    Corresponds to SandboxSettings (sandboxTypes.ts:91-144).
    Fields:
    - enabled: whether sandbox is active
    - auto_allow_bash: auto-approve bash in sandbox (autoAllowBashIfSandboxed)
    - allow_unsandboxed: allow fallback when sandbox fails (allowUnsandboxedCommands)
    - excluded_commands: command patterns to skip sandboxing
    - filesystem: filesystem restrictions
    - unshare_net: isolate network namespace
    """

    enabled: bool = False
    auto_allow_bash: bool = False
    allow_unsandboxed: bool = False
    excluded_commands: list[str] = field(default_factory=list)
    filesystem: SandboxFilesystemConfig = field(default_factory=SandboxFilesystemConfig)
    unshare_net: bool = True


def load_sandbox_config(
    config_paths: tuple[Path, ...] = (),
) -> SandboxConfig:
    """Load sandbox config from TOML [sandbox] section.

    Priority matches core/config.py: project-local > user-global.
    Default search: ~/.config/cc-mini/config.toml, .cc-mini.toml
    """
    if not config_paths:
        config_paths = (
            Path.home() / ".config" / "cc-mini" / "config.toml",
            Path.cwd() / ".cc-mini.toml",
        )
    merged: dict[str, Any] = {}

    for p in config_paths:
        if not p.exists():
            continue
        try:
            with p.open("rb") as fh:
                data = tomllib.load(fh)
        except (tomllib.TOMLDecodeError, OSError):
            continue
        sandbox_section = data.get("sandbox")
        if isinstance(sandbox_section, dict):
            merged.update(sandbox_section)

    return _dict_to_config(merged)


def save_sandbox_config(config: SandboxConfig, path: Path) -> None:
    """Save sandbox config to TOML [sandbox] section.

    Corresponds to setSandboxSettings (sandbox-adapter.ts:669-691).
    Only updates [sandbox], preserves other content via line-level surgery.
    """
    sandbox_dict = _config_to_dict(config)
    sandbox_lines = _render_sandbox_section(sandbox_dict)

    if path.exists():
        try:
            original = path.read_text(encoding="utf-8")
        except OSError:
            original = ""
    else:
        original = ""

    new_content = _replace_sandbox_section(original, sandbox_lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_content, encoding="utf-8")


def _dict_to_config(d: dict[str, Any]) -> SandboxConfig:
    """Convert a flat/nested dict to SandboxConfig."""
    fs_raw = d.get("filesystem", {})
    fs = SandboxFilesystemConfig(
        allow_write=fs_raw.get("allow_write", ["."]),
        deny_write=fs_raw.get("deny_write", []),
        deny_read=fs_raw.get("deny_read", []),
        allow_read=fs_raw.get("allow_read", []),
    )
    return SandboxConfig(
        enabled=bool(d.get("enabled", False)),
        auto_allow_bash=bool(d.get("auto_allow_bash", False)),
        allow_unsandboxed=bool(d.get("allow_unsandboxed", False)),
        excluded_commands=list(d.get("excluded_commands", [])),
        filesystem=fs,
        unshare_net=bool(d.get("unshare_net", True)),
    )


def _config_to_dict(config: SandboxConfig) -> dict[str, Any]:
    """Convert SandboxConfig to a serializable dict."""
    return {
        "enabled": config.enabled,
        "auto_allow_bash": config.auto_allow_bash,
        "allow_unsandboxed": config.allow_unsandboxed,
        "excluded_commands": config.excluded_commands,
        "unshare_net": config.unshare_net,
        "filesystem": {
            "allow_write": config.filesystem.allow_write,
            "deny_write": config.filesystem.deny_write,
            "deny_read": config.filesystem.deny_read,
            "allow_read": config.filesystem.allow_read,
        },
    }


import re


def _render_sandbox_section(sandbox_dict: dict[str, Any]) -> str:
    """Render [sandbox] and [sandbox.filesystem] as TOML text."""
    lines: list[str] = ["[sandbox]"]
    for key, val in sandbox_dict.items():
        if isinstance(val, dict):
            continue
        lines.append(_format_kv(key, val))
    fs = sandbox_dict.get("filesystem")
    if isinstance(fs, dict):
        lines.append("")
        lines.append("[sandbox.filesystem]")
        for key, val in fs.items():
            lines.append(_format_kv(key, val))
    return "\n".join(lines) + "\n"


def _replace_sandbox_section(original: str, new_section: str) -> str:
    """Replace or append [sandbox] block in TOML text, preserving everything else.

    Uses line-based parsing: removes all lines belonging to [sandbox] or
    [sandbox.*] sections, then inserts new_section in their place.
    """
    if not original.strip():
        return new_section

    _HEADER_RE = re.compile(r"^\[(.+)\]\s*$")

    lines = original.splitlines(keepends=True)
    kept: list[str] = []
    insert_pos: int | None = None
    in_sandbox = False

    for line in lines:
        m = _HEADER_RE.match(line)
        if m:
            header_name = m.group(1).strip()
            if header_name == "sandbox" or header_name.startswith("sandbox."):
                in_sandbox = True
                if insert_pos is None:
                    insert_pos = len(kept)
                continue
            else:
                in_sandbox = False

        if in_sandbox:
            continue

        kept.append(line)

    # Build result: kept lines before insert point + new section + kept lines after
    if insert_pos is not None:
        before = "".join(kept[:insert_pos]).rstrip("\n")
        after = "".join(kept[insert_pos:]).lstrip("\n")
        parts = [p for p in (before, new_section.strip(), after) if p]
        return "\n\n".join(parts) + "\n"
    else:
        # No existing [sandbox] — append
        return original.rstrip("\n") + "\n\n" + new_section


def _write_toml(data: dict[str, Any], fh: Any) -> None:
    """Minimal TOML writer sufficient for our config structure.

    Handles: scalars, lists of strings, and one level of nested tables.
    """
    # Write top-level scalars and arrays first
    for key, val in data.items():
        if isinstance(val, dict):
            continue
        fh.write(f"{_format_kv(key, val)}\n")

    # Write tables
    for key, val in data.items():
        if not isinstance(val, dict):
            continue
        fh.write(f"\n[{key}]\n")
        for sub_key, sub_val in val.items():
            if isinstance(sub_val, dict):
                continue
            fh.write(f"{_format_kv(sub_key, sub_val)}\n")
        # Nested sub-tables
        for sub_key, sub_val in val.items():
            if not isinstance(sub_val, dict):
                continue
            fh.write(f"\n[{key}.{sub_key}]\n")
            for k, v in sub_val.items():
                fh.write(f"{_format_kv(k, v)}\n")


def _format_kv(key: str, val: Any) -> str:
    """Format a single TOML key = value pair."""
    if isinstance(val, bool):
        return f"{key} = {'true' if val else 'false'}"
    if isinstance(val, int):
        return f"{key} = {val}"
    if isinstance(val, float):
        return f"{key} = {val}"
    if isinstance(val, str):
        return f'{key} = "{val}"'
    if isinstance(val, list):
        items = ", ".join(f'"{v}"' if isinstance(v, str) else str(v) for v in val)
        return f"{key} = [{items}]"
    return f'{key} = "{val}"'
