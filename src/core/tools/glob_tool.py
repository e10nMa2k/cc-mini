from __future__ import annotations

import glob as glob_module
from pathlib import Path
from .base import Tool, ToolResult


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
            "pattern": {"type": "string", "description": "Glob pattern e.g. '**/*.py'"},
            "path": {"type": "string", "description": "Base directory to search (default: cwd)"},
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

        matches = glob_module.glob(pattern, root_dir=str(base), recursive=True)
        matches = sorted(matches, key=lambda p: (base / p).stat().st_mtime, reverse=True)

        if not matches:
            return ToolResult(content="No files found matching the pattern.")
        return ToolResult(content="\n".join(matches))
