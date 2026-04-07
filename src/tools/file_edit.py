from __future__ import annotations

from pathlib import Path
from core.tool import Tool, ToolResult


class FileEditTool(Tool):
    name = "Edit"
    description = (
        "Performs exact string replacements in files.\n\n"
        "Usage:\n"
        "- You must use your `Read` tool at least once in the conversation before editing. "
        "This tool will error if you attempt an edit without reading the file.\n"
        "- When editing text from Read tool output, ensure you preserve the exact indentation "
        "(tabs/spaces) as it appears AFTER the line number prefix. The line number prefix format is: "
        "line number + tab. Everything after that is the actual file content to match. "
        "Never include any part of the line number prefix in the old_string or new_string.\n"
        "- ALWAYS prefer editing existing files in the codebase. NEVER write new files unless explicitly required.\n"
        "- Only use emojis if the user explicitly requests it. Avoid adding emojis to files unless asked.\n"
        "- The edit will FAIL if `old_string` is not unique in the file. Either provide a larger string "
        "with more surrounding context to make it unique or use `replace_all` to change every instance of `old_string`.\n"
        "- Use `replace_all` for replacing and renaming strings across the file. "
        "This parameter is useful if you want to rename a variable for instance."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute path to file"},
            "old_string": {"type": "string", "description": "Exact string to replace"},
            "new_string": {"type": "string", "description": "Replacement string"},
            "replace_all": {"type": "boolean", "description": "Replace all occurrences", "default": False},
        },
        "required": ["file_path", "old_string", "new_string"],
    }

    # Shared set of files that have been read — populated by FileReadTool
    _read_files: set[str] = set()

    _MAX_FILE_SIZE = 1 * 1024 * 1024 * 1024  # 1 GiB

    @classmethod
    def mark_file_read(cls, file_path: str) -> None:
        cls._read_files.add(file_path)

    def get_activity_description(self, **kwargs) -> str | None:
        file_path = kwargs.get("file_path", "")
        return f"Editing {file_path}" if file_path else None

    def execute(self, file_path: str, old_string: str, new_string: str,
                replace_all: bool = False) -> ToolResult:
        path = Path(file_path)
        if not path.exists():
            return ToolResult(content=f"Error: File not found: {file_path}", is_error=True)

        # Enforce read-before-write
        if file_path not in self._read_files and str(path.resolve()) not in self._read_files:
            return ToolResult(
                content=f"Error: You must read {file_path} before editing it. Use the Read tool first.",
                is_error=True,
            )

        # File size check
        try:
            if path.stat().st_size > self._MAX_FILE_SIZE:
                return ToolResult(content=f"Error: File too large to edit", is_error=True)
        except OSError:
            pass
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as e:
            return ToolResult(content=f"Error reading file: {e}", is_error=True)

        count = content.count(old_string)
        if count == 0:
            return ToolResult(content=f"Error: old_string not found in {file_path}", is_error=True)
        if count > 1 and not replace_all:
            return ToolResult(
                content=f"Error: old_string found {count} times. Use replace_all=true or add more context.",
                is_error=True,
            )

        new_content = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)
        try:
            path.write_text(new_content, encoding="utf-8")
        except OSError as e:
            return ToolResult(content=f"Error writing file: {e}", is_error=True)

        replaced = count if replace_all else 1
        return ToolResult(content=f"Successfully replaced {replaced} occurrence(s) in {file_path}")
