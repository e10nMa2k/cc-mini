from __future__ import annotations

from pathlib import Path
from .base import Tool, ToolResult


class FileReadTool(Tool):
    name = "Read"
    description = (
        "Reads a file from the local filesystem. You can access any file directly by using this tool.\n"
        "Assume this tool is able to read all files on the machine. If the User provides a path to a "
        "file assume that path is valid. It is okay to read a file that does not exist; an error will be returned.\n\n"
        "Usage:\n"
        "- The file_path parameter must be an absolute path, not a relative path\n"
        "- By default, it reads up to 2000 lines starting from the beginning of the file\n"
        "- When you already know which part of the file you need, only read that part. "
        "This can be important for larger files.\n"
        "- Results are returned using cat -n format, with line numbers starting at 1\n"
        "- This tool can only read files, not directories. To read a directory, use an ls command via the Bash tool.\n"
        "- If you read a file that exists but has empty contents you will receive a system reminder warning "
        "in place of file contents."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute path to the file"},
            "offset": {"type": "integer", "description": "Line to start from (0-indexed)", "default": 0},
            "limit": {"type": "integer", "description": "Max lines to return", "default": 2000},
        },
        "required": ["file_path"],
    }

    def is_read_only(self) -> bool:
        return True

    def get_activity_description(self, **kwargs) -> str | None:
        file_path = kwargs.get("file_path", "")
        return f"Reading {file_path}" if file_path else None

    def execute(self, file_path: str, offset: int = 0, limit: int = 2000) -> ToolResult:
        path = Path(file_path)
        if not path.exists():
            return ToolResult(content=f"Error: File not found: {file_path}", is_error=True)
        if not path.is_file():
            return ToolResult(content=f"Error: Not a file: {file_path}", is_error=True)
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return ToolResult(content=f"Error reading file: {e}", is_error=True)

        lines = content.splitlines(keepends=True)
        sliced = lines[offset: offset + limit]
        numbered = "".join(f"{offset + i + 1}\t{line}" for i, line in enumerate(sliced))

        if len(lines) > offset + limit:
            numbered += f"\n... ({len(lines) - offset - limit} more lines)"

        return ToolResult(content=numbered)
