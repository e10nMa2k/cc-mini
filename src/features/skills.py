"""Skill system — load, register, and execute SKILL.md-based skills.

Modelled after claude-code's ``src/skills/loadSkillsDir.ts`` and
``src/tools/SkillTool/SkillTool.ts``.

Skills are Markdown files with YAML frontmatter that define reusable prompts.
They can be:
  1. **Bundled** — registered in code via ``register_skill()``
  2. **Project** — discovered from ``.cc-mini/skills/<name>/SKILL.md``
  3. **User** — discovered from ``~/.cc-mini/skills/<name>/SKILL.md``

Execution modes:
  - **inline** (default): prompt injected into current conversation
  - **fork**: prompt runs in an isolated turn (messages saved/restored)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Skill definition
# ---------------------------------------------------------------------------

@dataclass
class Skill:
    """A single skill definition."""
    name: str
    description: str = ""
    when_to_use: str = ""
    user_invocable: bool = True
    disable_model_invocation: bool = False
    allowed_tools: list[str] = field(default_factory=list)
    model: str | None = None
    context: str = "inline"          # "inline" or "fork"
    argument_hint: str = ""
    paths: list[str] = field(default_factory=list)  # gitignore-style patterns
    source: str = "project"          # "bundled", "project", "user"
    skill_root: str | None = None    # base dir for $SKILL_DIR substitution

    # The prompt content (body of SKILL.md, after frontmatter)
    _prompt_text: str = ""
    # Or a dynamic prompt generator (for bundled skills)
    _prompt_fn: Callable[[str], str] | None = None

    def get_prompt(self, args: str = "") -> str:
        """Return the final prompt text, substituting variables."""
        if self._prompt_fn is not None:
            return self._prompt_fn(args)
        text = self._prompt_text
        # Variable substitution (matches claude-code's processPromptSlashCommand)
        text = text.replace("$ARGUMENTS", args)
        if self.skill_root:
            text = text.replace("${CLAUDE_SKILL_DIR}", self.skill_root)
        if args and self.argument_hint:
            text = text.replace(f"${{{self.argument_hint}}}", args)
        return text


# ---------------------------------------------------------------------------
# YAML frontmatter parser (minimal, no PyYAML dependency)
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split ``text`` into (frontmatter_dict, body).

    Uses a minimal key: value parser — supports strings, booleans, and
    comma-separated lists.  Does not handle nested YAML.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text

    raw = m.group(1)
    body = text[m.end():]
    meta: dict[str, Any] = {}

    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower().replace("-", "_")
        val = val.strip()
        # Boolean
        if val.lower() in ("true", "yes"):
            meta[key] = True
        elif val.lower() in ("false", "no"):
            meta[key] = False
        # List (comma-separated)
        elif "," in val:
            meta[key] = [v.strip() for v in val.split(",") if v.strip()]
        # Quoted string
        elif (val.startswith('"') and val.endswith('"')) or \
             (val.startswith("'") and val.endswith("'")):
            meta[key] = val[1:-1]
        else:
            meta[key] = val

    return meta, body


def _ensure_str(val: Any, default: str = "") -> str:
    """Coerce *val* to a string — rejoin lists produced by the frontmatter parser."""
    if val is None:
        return default
    if isinstance(val, list):
        return ", ".join(str(v) for v in val)
    return str(val)


def _skill_from_frontmatter(meta: dict[str, Any], body: str,
                             name: str, source: str,
                             skill_root: str | None = None) -> Skill:
    """Build a ``Skill`` from parsed frontmatter and body text."""
    allowed = meta.get("allowed_tools", [])
    if isinstance(allowed, str):
        allowed = [t.strip() for t in allowed.split(",") if t.strip()]

    paths = meta.get("paths", [])
    if isinstance(paths, str):
        paths = [p.strip() for p in paths.split(",") if p.strip()]

    return Skill(
        name=_ensure_str(meta.get("name"), name),
        description=_ensure_str(meta.get("description")),
        when_to_use=_ensure_str(meta.get("when_to_use")),
        user_invocable=meta.get("user_invocable", True),
        disable_model_invocation=meta.get("disable_model_invocation", False),
        allowed_tools=allowed,
        model=meta.get("model"),
        context=_ensure_str(meta.get("context"), "inline"),
        argument_hint=_ensure_str(meta.get("arguments")),
        paths=paths,
        source=source,
        skill_root=skill_root,
        _prompt_text=body.strip(),
    )


# ---------------------------------------------------------------------------
# Skill registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, Skill] = {}


def register_skill(skill: Skill) -> None:
    """Add a skill to the global registry."""
    _REGISTRY[skill.name] = skill


def get_skill(name: str) -> Skill | None:
    """Look up a skill by name."""
    return _REGISTRY.get(name)


def list_skills(user_invocable_only: bool = True) -> list[Skill]:
    """Return all registered skills, optionally filtered."""
    skills = list(_REGISTRY.values())
    if user_invocable_only:
        skills = [s for s in skills if s.user_invocable]
    return sorted(skills, key=lambda s: (s.source != "bundled", s.name))


def clear_skills(source: str | None = None) -> None:
    """Remove skills from the registry.  If *source* given, only that source."""
    if source is None:
        _REGISTRY.clear()
    else:
        to_remove = [k for k, v in _REGISTRY.items() if v.source == source]
        for k in to_remove:
            del _REGISTRY[k]


# ---------------------------------------------------------------------------
# Skill discovery from disk
# ---------------------------------------------------------------------------

def load_skills_from_dir(skills_dir: Path, source: str = "project") -> list[Skill]:
    """Scan *skills_dir* for ``<name>/SKILL.md`` and register each skill.

    Matches claude-code's ``loadSkillsDir.ts`` directory-format loading:
    only directories containing a ``SKILL.md`` file are recognised.

    Also supports single ``.md`` files directly in the directory (legacy
    ``/commands/`` format from claude-code).
    """
    loaded: list[Skill] = []
    if not skills_dir.is_dir():
        return loaded

    for entry in sorted(skills_dir.iterdir()):
        skill = None
        if entry.is_dir():
            skill_md = entry / "SKILL.md"
            if not skill_md.exists():
                # Fallback: look for any .md file in the directory
                md_files = list(entry.glob("*.md"))
                if md_files:
                    skill_md = md_files[0]
                else:
                    continue
            try:
                text = skill_md.read_text(encoding="utf-8")
            except Exception:
                continue
            meta, body = _parse_frontmatter(text)
            skill = _skill_from_frontmatter(
                meta, body,
                name=entry.name,
                source=source,
                skill_root=str(entry),
            )
        elif entry.suffix == ".md" and entry.is_file():
            # Legacy single-file format
            try:
                text = entry.read_text(encoding="utf-8")
            except Exception:
                continue
            meta, body = _parse_frontmatter(text)
            skill = _skill_from_frontmatter(
                meta, body,
                name=entry.stem,
                source=source,
                skill_root=str(entry.parent),
            )

        if skill and skill._prompt_text:
            register_skill(skill)
            loaded.append(skill)

    return loaded


def discover_skills(cwd: str | None = None) -> list[Skill]:
    """Discover and register skills from standard locations.

    Search order (matches claude-code's four-tier hierarchy):
      1. Bundled skills (already registered via ``register_bundled_skills()``)
      2. User skills:    ``~/.cc-mini/skills/``
      3. Project skills: ``{cwd}/.cc-mini/skills/``

    Returns newly loaded skills (excludes already-registered bundled ones).
    """
    loaded: list[Skill] = []

    # User-level skills
    user_dir = Path.home() / ".cc-mini" / "skills"
    loaded.extend(load_skills_from_dir(user_dir, source="user"))

    # Project-level skills
    if cwd:
        project_dir = Path(cwd) / ".cc-mini" / "skills"
        loaded.extend(load_skills_from_dir(project_dir, source="project"))

    return loaded


# ---------------------------------------------------------------------------
# System prompt section
# ---------------------------------------------------------------------------

def build_skills_prompt_section() -> str:
    """Build the skills listing for the system prompt.

    Matches claude-code's ``SkillTool/prompt.ts`` — lists available skills
    so the model knows what it can invoke via ``/skill-name``.
    """
    skills = list_skills(user_invocable_only=False)
    if not skills:
        return ""

    lines = ["# Available Skills", ""]
    for s in skills:
        desc = s.description or "(no description)"
        line = f"- /{s.name}: {desc}"
        if s.when_to_use:
            line += f" — {s.when_to_use}"
        lines.append(line)

    return "\n".join(lines)
