import subprocess
from datetime import date
from pathlib import Path

_BASE_PROMPT = """\
You are Claude Code, an AI assistant for software engineering tasks in the terminal.
You help with coding tasks by reading files, editing code, running commands, and searching codebases.

Guidelines:
- Always read a file before editing it
- Prefer small, targeted edits over rewriting large sections
- Run tests after making changes when test commands are available
- Use Glob/Grep to find relevant files before reading them all"""


def build_system_prompt(cwd: str | None = None, memory_dir: Path | None = None) -> str:
    parts = [_BASE_PROMPT]
    parts.append(f"\n# Environment\nToday's date: {date.today().isoformat()}")

    cwd = cwd or str(Path.cwd())
    parts.append(f"Working directory: {cwd}")

    git_status = _get_git_status(cwd)
    if git_status:
        parts.append(f"\n# Git Status\n{git_status}")

    claude_md = _find_claude_md(cwd)
    if claude_md:
        parts.append(f"\n# CLAUDE.md\n{claude_md}")

    if memory_dir is not None:
        from .memory import build_memory_system_section
        parts.append(build_memory_system_section(memory_dir))

    # Companion intro (if hatched and not muted)
    companion_text = _get_companion_intro()
    if companion_text:
        parts.append(f"\n{companion_text}")

    return "\n".join(parts)


def _get_git_status(cwd: str) -> str:
    try:
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, cwd=cwd, timeout=5,
        ).stdout.strip()

        status = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True, text=True, cwd=cwd, timeout=5,
        ).stdout.strip()[:2000]

        log = subprocess.run(
            ["git", "log", "--oneline", "-5"],
            capture_output=True, text=True, cwd=cwd, timeout=5,
        ).stdout.strip()

        if not branch and not status and not log:
            return ""

        parts = []
        if branch:
            parts.append(f"Branch: {branch}")
        if status:
            parts.append(f"Status:\n{status}")
        if log:
            parts.append(f"Recent commits:\n{log}")
        return "\n".join(parts)
    except Exception:
        return ""


def _get_companion_intro() -> str:
    try:
        from .buddy.companion import get_companion
        from .buddy.storage import load_companion_muted
        from .buddy.prompt import companion_intro_text

        if load_companion_muted():
            return ""
        companion = get_companion()
        if companion is None:
            return ""
        return companion_intro_text(companion.name, companion.species)
    except Exception:
        return ""


def _find_claude_md(cwd: str) -> str:
    path = Path(cwd) / "CLAUDE.md"
    if path.exists():
        try:
            return path.read_text(encoding="utf-8", errors="replace")[:10_000]
        except OSError:
            pass
    return ""
