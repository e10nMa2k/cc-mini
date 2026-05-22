from pathlib import Path
from .base import Tool, ToolResult


class FileReadTool(Tool):
    name = "Read" # Tool抽象类的属性
    description = (
        "Reads a file from the local filesystem. "
        "Returns content with line numbers (1-indexed). "
        "Use offset and limit to read large files in chunks."
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
