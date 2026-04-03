from __future__ import annotations

from pathlib import Path
from .base import Tool, ToolResult


class FileWriteTool(Tool):
    name = "Write"
    description = (
        "Writes a file to the local filesystem.\n\n"
        "Usage:\n"
        "- This tool will overwrite the existing file if there is one at the provided path.\n"
        "- If this is an existing file, you MUST use the Read tool first to read the file's contents. "
        "This tool will fail if you did not read the file first.\n"
        "- Prefer the Edit tool for modifying existing files \u2014 it only sends the diff. "
        "Only use this tool to create new files or for complete rewrites.\n"
        "- NEVER create documentation files (*.md) or README files unless explicitly requested by the User.\n"
        "- Only use emojis if the user explicitly requests it. Avoid writing emojis to files unless asked."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute path to the file to write"},
            "content": {"type": "string", "description": "The full content to write to the file"},
        },
        "required": ["file_path", "content"],
    }

    def get_activity_description(self, **kwargs) -> str | None:
        file_path = kwargs.get("file_path", "")
        return f"Writing {file_path}" if file_path else None

    def execute(self, file_path: str, content: str) -> ToolResult:
        path = Path(file_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except OSError as e:
            return ToolResult(content=f"Error writing file: {e}", is_error=True)

        lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        return ToolResult(content=f"Successfully wrote {lines} lines to {file_path}")
