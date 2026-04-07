from __future__ import annotations

import glob as glob_module
import subprocess
from pathlib import Path
from core.tool import Tool, ToolResult


_MAX_RESULTS = 100


class GlobTool(Tool):
    name = "Glob"
    description = (
        "- Fast file pattern matching tool that works with any codebase size\n"
        "- Supports glob patterns like \"**/*.js\" or \"src/**/*.ts\"\n"
        "- Returns matching file paths sorted by modification time\n"
        "- Use this tool when you need to find files by name patterns\n"
        "- When you are doing an open ended search that may require multiple "
        "rounds of globbing and grepping, use the Agent tool instead"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "The glob pattern to match files against"},
            "path": {
                "type": "string",
                "description": (
                    "The directory to search in. If not specified, the current working "
                    "directory will be used. IMPORTANT: Omit this field to use the default "
                    "directory. DO NOT enter \"undefined\" or \"null\" - simply omit it for "
                    "the default behavior. Must be a valid directory path if provided."
                ),
            },
        },
        "required": ["pattern"],
    }

    def is_read_only(self) -> bool:
        return True

    def get_activity_description(self, **kwargs) -> str | None:
        pattern = kwargs.get("pattern", "")
        return f"Finding {pattern}" if pattern else None

    def execute(self, pattern: str, path: str = ".") -> ToolResult:
        base = Path(path).resolve()
        if not base.exists():
            return ToolResult(content=f"Error: Directory not found: {path}", is_error=True)
        if not base.is_dir():
            return ToolResult(content=f"Error: Path is not a directory: {path}", is_error=True)

        # Try ripgrep first (fast, handles large codebases well)
        try:
            matches = self._rg_glob(pattern, str(base))
        except FileNotFoundError:
            matches = self._python_glob(pattern, base)

        if not matches:
            return ToolResult(content="No files found matching the pattern.")

        truncated = len(matches) > _MAX_RESULTS
        matches = matches[:_MAX_RESULTS]

        # Convert to relative paths to save tokens
        rel_matches = []
        for m in matches:
            try:
                rel_matches.append(str(Path(m).relative_to(base)))
            except ValueError:
                rel_matches.append(m)

        result = "\n".join(rel_matches)
        if truncated:
            result += "\n(Results are truncated. Consider using a more specific path or pattern.)"
        return ToolResult(content=result)

    def _rg_glob(self, pattern: str, search_dir: str) -> list[str]:
        """Use ripgrep --files --glob for fast file matching."""
        cmd = [
            "rg", "--files",
            "--glob", pattern,
            "--sort=modified",
            "--no-ignore",
            "--hidden",
            search_dir,
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        if result.returncode not in (0, 1):  # 1 = no matches
            raise FileNotFoundError("rg failed")
        output = result.stdout.strip()
        return output.split("\n") if output else []

    def _python_glob(self, pattern: str, base: Path) -> list[str]:
        """Fallback using Python's glob module."""
        matches = glob_module.glob(pattern, root_dir=str(base), recursive=True)
        # Sort by modification time (oldest first, matching rg --sort=modified)
        matches = sorted(
            matches,
            key=lambda p: (base / p).stat().st_mtime,
            reverse=True,
        )
        return [str(base / m) for m in matches]
