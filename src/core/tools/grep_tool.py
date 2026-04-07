from __future__ import annotations

import re
import subprocess
from pathlib import Path
import glob as glob_module
from .base import Tool, ToolResult


class GrepTool(Tool):
    name = "Grep"
    description = (
        "A powerful search tool built on ripgrep\n\n"
        "  Usage:\n"
        "  - ALWAYS use Grep for search tasks. NEVER invoke `grep` or `rg` as a Bash command. "
        "The Grep tool has been optimized for correct permissions and access.\n"
        "  - Supports full regex syntax (e.g., \"log.*Error\", \"function\\s+\\w+\")\n"
        "  - Filter files with glob parameter (e.g., \"*.js\", \"**/*.tsx\") or type parameter "
        "(e.g., \"js\", \"py\", \"rust\")\n"
        "  - Output modes: \"content\" shows matching lines, \"files_with_matches\" shows only "
        "file paths (default), \"count\" shows match counts\n"
        "  - Use Agent tool for open-ended searches requiring multiple rounds\n"
        "  - Pattern syntax: Uses ripgrep (not grep) - literal braces need escaping "
        "(use `interface\\{\\}` to find `interface{}` in Go code)\n"
        "  - Multiline matching: By default patterns match within single lines only. "
        "For cross-line patterns like `struct \\{[\\s\\S]*?field`, use `multiline: true`"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern"},
            "path": {"type": "string", "description": "Directory or file to search"},
            "glob": {"type": "string", "description": "File glob filter e.g. '*.py'"},
            "output_mode": {
                "type": "string",
                "enum": ["files_with_matches", "content"],
                "default": "files_with_matches",
            },
            "-i": {"type": "boolean", "description": "Case insensitive", "default": False},
            "-C": {"type": "integer", "description": "Context lines around each match", "default": 0},
        },
        "required": ["pattern"],
    }

    def get_activity_description(self, **kwargs) -> str | None:
        pattern = kwargs.get("pattern", "")
        return f"Searching for {pattern}" if pattern else None

    def is_read_only(self) -> bool:
        return True

    def execute(self, pattern: str, path: str = ".", glob: str | None = None,
                output_mode: str = "files_with_matches", **kwargs) -> ToolResult:
        cmd = ["rg", "--no-heading"]
        if kwargs.get("-i"):
            cmd.append("-i")
        context = kwargs.get("-C", 0)
        if context:
            cmd.extend(["-C", str(context)])
        cmd.append("-l" if output_mode == "files_with_matches" else "-n")
        if glob:
            cmd.extend(["-g", glob])
        cmd.extend([pattern, path])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            output = result.stdout.strip()
            return ToolResult(content=output if output else "No matches found.")
        except FileNotFoundError:
            return self._python_grep(pattern, path, glob, kwargs.get("-i", False), output_mode)
        except subprocess.TimeoutExpired:
            return ToolResult(content="Error: Search timed out.", is_error=True)

    def _python_grep(self, pattern: str, path: str, glob_filter: str | None,
                     case_insensitive: bool, output_mode: str = "files_with_matches") -> ToolResult:
        base = Path(path)
        flags = re.IGNORECASE if case_insensitive else 0
        regex = re.compile(pattern, flags)

        if base.is_file():
            files = [base]
        else:
            pat = glob_filter or "**/*"
            files = [base / p for p in glob_module.glob(pat, root_dir=str(base), recursive=True)]

        matched = []
        for f in files:
            if not f.is_file():
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
                if output_mode == "content":
                    for lineno, line in enumerate(text.splitlines(), 1):
                        if regex.search(line):
                            matched.append(f"{f}:{lineno}:{line}")
                else:
                    if regex.search(text):
                        matched.append(str(f))
            except OSError:
                pass

        return ToolResult(content="\n".join(matched) if matched else "No matches found.")
