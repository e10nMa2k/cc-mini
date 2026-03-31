"""KAIROS memory system — append-only daily logs, dream consolidation, session persistence."""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

MEMORY_DIR = Path.home() / ".mini-claude" / "memory"
SESSIONS_DIR = Path.home() / ".mini-claude" / "sessions"
MAX_MEMORY_INDEX_CHARS = 10_000
LOCK_FILE_NAME = ".consolidate-lock"
HOLDER_STALE_S = 3600  # 1 hour — reclaim lock after this


# ---------------------------------------------------------------------------
# Directory helpers
# ---------------------------------------------------------------------------

def ensure_memory_dir(memory_dir: Path) -> None:
    """Create memory_dir and memory_dir/logs if they don't exist."""
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "logs").mkdir(parents=True, exist_ok=True)


def daily_log_path(memory_dir: Path, today: date | None = None) -> Path:
    """Return memory_dir/logs/YYYY/MM/YYYY-MM-DD.md, creating parents."""
    today = today or date.today()
    path = memory_dir / "logs" / str(today.year) / f"{today.month:02d}" / f"{today.isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def append_to_daily_log(memory_dir: Path, entry: str) -> None:
    """Append a timestamped entry to today's daily log."""
    path = daily_log_path(memory_dir)
    timestamp = datetime.now().strftime("%H:%M")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"- [{timestamp}] {entry}\n")


# ---------------------------------------------------------------------------
# Memory index
# ---------------------------------------------------------------------------

def load_memory_index(memory_dir: Path) -> str:
    """Read MEMORY.md, truncate to MAX_MEMORY_INDEX_CHARS. Returns '' if missing."""
    path = memory_dir / "MEMORY.md"
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[:MAX_MEMORY_INDEX_CHARS]
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Consolidation lock  (mirrors autoDream/consolidationLock.ts)
# Lock file mtime = lastConsolidatedAt.  Body = holder PID.
# ---------------------------------------------------------------------------

def _lock_path(memory_dir: Path) -> Path:
    return memory_dir / LOCK_FILE_NAME


def read_last_consolidated_at(memory_dir: Path) -> float:
    """Return epoch seconds of last consolidation (0 if never)."""
    lp = _lock_path(memory_dir)
    try:
        return lp.stat().st_mtime
    except OSError:
        return 0.0


def try_acquire_lock(memory_dir: Path) -> bool:
    """Try to acquire consolidation lock. Returns True on success."""
    lp = _lock_path(memory_dir)
    my_pid = os.getpid()

    # Check existing holder
    try:
        stat = lp.stat()
        age = datetime.now().timestamp() - stat.st_mtime
        holder_pid = int(lp.read_text().strip())
        # If holder is alive and lock is fresh, back off
        if age < HOLDER_STALE_S:
            try:
                os.kill(holder_pid, 0)  # probe only
                return False
            except OSError:
                pass  # holder dead, reclaim
    except (OSError, ValueError):
        pass  # no lock or corrupt — take it

    # Write our PID
    lp.write_text(str(my_pid))
    return True


def release_lock(memory_dir: Path) -> None:
    """Stamp lock mtime to now (marks consolidation time) but keep the file."""
    lp = _lock_path(memory_dir)
    try:
        now = datetime.now().timestamp()
        os.utime(lp, (now, now))
    except OSError:
        pass


def record_consolidation(memory_dir: Path) -> None:
    """Record that a consolidation just finished (for manual /dream too)."""
    lp = _lock_path(memory_dir)
    lp.write_text(str(os.getpid()))
    now = datetime.now().timestamp()
    os.utime(lp, (now, now))


def count_sessions_since(since_ts: float) -> int:
    """Count session files with mtime > since_ts."""
    if not SESSIONS_DIR.exists():
        return 0
    count = 0
    for f in SESSIONS_DIR.iterdir():
        if f.suffix == ".jsonl" and f.stat().st_mtime > since_ts:
            count += 1
    return count


def should_auto_dream(memory_dir: Path, min_hours: float, min_sessions: int,
                      current_session_id: str,
                      sessions_dir: Path | None = None) -> bool:
    """Check all gates: time ≥ min_hours AND sessions ≥ min_sessions."""
    last = read_last_consolidated_at(memory_dir)
    now = datetime.now().timestamp()
    hours_since = (now - last) / 3600 if last > 0 else float("inf")

    if hours_since < min_hours:
        return False

    # Count sessions newer than last consolidation, exclude current
    scan_dir = sessions_dir or SESSIONS_DIR
    count = 0
    if scan_dir.exists():
        for f in scan_dir.iterdir():
            if f.suffix == ".jsonl" and current_session_id not in f.name and f.stat().st_mtime > last:
                count += 1

    return count >= min_sessions


# ---------------------------------------------------------------------------
# <memory> tag extraction
# ---------------------------------------------------------------------------

def extract_memory_tags(text: str) -> list[str]:
    """Extract all <memory>...</memory> tag contents from text."""
    return [m.strip() for m in re.findall(r"<memory>(.*?)</memory>", text, re.DOTALL)]


# ---------------------------------------------------------------------------
# System prompt section
# ---------------------------------------------------------------------------

def build_memory_system_section(memory_dir: Path) -> str:
    """Return the memory instructions + MEMORY.md content for the system prompt.

    Mirrors Claude Code's memdir.ts buildMemoryLines() — 4-type taxonomy,
    frontmatter format, what-not-to-save gate, and drift caveat.
    """
    index = load_memory_index(memory_dir)

    section = f"""\

# Auto Memory

You have a persistent, file-based memory system at `{memory_dir}/`.
This directory already exists — write to it directly with the Write tool \
(do not run mkdir or check for its existence).

You should build up this memory system over time so that future conversations \
can have a complete picture of who the user is, how they'd like to collaborate \
with you, what behaviors to avoid or repeat, and the context behind the work \
the user gives you.

If the user explicitly asks you to remember something, save it immediately as \
whichever type fits best. If they ask you to forget something, find and remove \
the relevant entry.

## Types of memory

There are four discrete types of memory:

### user
Information about the user's role, goals, responsibilities, and knowledge. \
Great user memories help you tailor future behavior to the user's preferences. \
**When to save:** When you learn details about the user's role, preferences, \
responsibilities, or knowledge.

### feedback
Guidance or correction the user has given you. These are very important — they \
allow you to remain coherent and responsive across sessions. Without these, you \
will repeat the same mistakes. \
**When to save:** Any time the user corrects your approach in a way applicable \
to future conversations (e.g. "don't mock the database", "stop summarizing"). \
**Body structure:** Lead with the rule, then a **Why:** line and a \
**How to apply:** line.

### project
Information about ongoing work, goals, initiatives, bugs, or incidents not \
derivable from code or git history. \
**When to save:** When you learn who is doing what, why, or by when. Always \
convert relative dates to absolute dates. \
**Body structure:** Lead with the fact/decision, then **Why:** and \
**How to apply:** lines.

### reference
Pointers to where information lives in external systems. \
**When to save:** When you learn about resources and their purpose \
(e.g. "bugs tracked in Linear project INGEST").

## What NOT to save
- Code patterns, architecture, file paths — derivable from reading the project
- Git history, recent changes — `git log` / `git blame` are authoritative
- Debugging solutions — the fix is in the code; the commit message has context
- Anything already documented in CLAUDE.md files
- Ephemeral task details or current conversation context

## How to save memories

**Option A — <memory> tags (quick notes):**
Wrap text in `<memory>...</memory>` tags in your response. These are \
automatically extracted and appended to the daily log.

**Option B — Write files directly (structured memories):**
Write a `.md` file to `{memory_dir}/` with this frontmatter:

```markdown
---
name: {{{{memory name}}}}
description: {{{{one-line description — used to decide relevance later}}}}
type: {{{{user | feedback | project | reference}}}}
---

{{{{memory content}}}}
```

Then add a pointer to that file in `{memory_dir}/MEMORY.md`. \
MEMORY.md is an index, not a memory — it should contain only links with \
brief descriptions. Keep it under 200 lines.

## When to access memories
- When specific known memories seem relevant to the task at hand
- When the user seems to be referring to work from a prior conversation
- You MUST access memory when the user explicitly asks you to recall or remember

## Slash commands
- `/dream` — consolidate daily logs into topic files and update MEMORY.md
- `/remember <text>` — manually append a note to the daily log
- `/memory` — print current MEMORY.md contents
"""

    if index:
        section += f"\n## Current Memory Index (MEMORY.md)\n{index}\n"
    else:
        section += "\nNo memories consolidated yet.\n"

    return section


# ---------------------------------------------------------------------------
# Dream consolidation prompt
# ---------------------------------------------------------------------------

def build_dream_prompt(memory_dir: Path) -> str:
    """Build the 4-phase consolidation prompt for the dream agent.

    Mirrors Claude Code's consolidationPrompt.ts structure.
    """
    return f"""\
You are running a KAIROS dream consolidation. Your job is to read daily logs \
and existing memories, then produce consolidated topic files and an updated \
MEMORY.md index.

Memory directory: {memory_dir}
Logs directory: {memory_dir / 'logs'}

## Phase 1: Orient
- Use Glob to list all files in {memory_dir}/ (topic files + MEMORY.md).
- Read MEMORY.md if it exists to understand the current index.
- Skim existing topic files to know what's already captured.

## Phase 2: Gather recent signal
- Use Glob to find all daily log files under {memory_dir / 'logs'}/
- Read each log file. Priority: daily logs first, then existing memories that \
may have drifted.

## Phase 3: Consolidate
Group related entries into topic files. Each topic file must have YAML frontmatter:

```markdown
---
name: descriptive name
description: one-line description for relevance decisions
type: user | feedback | project | reference
---

Content here. For feedback/project types, structure as:
Rule or fact
**Why:** the reason
**How to apply:** when/where this kicks in
```

Rules:
- Merge into existing files rather than creating duplicates
- Convert relative dates to absolute dates
- One file per topic (e.g., user_preferences.md, project_goals.md)
- Write or update files in {memory_dir}/ using the Write or Edit tools

## Phase 4: Prune and index
- Update {memory_dir}/MEMORY.md as a concise index
- One line per topic file with a brief description
- Remove stale pointers to files that no longer exist
- Keep MEMORY.md under 200 lines

Do NOT delete daily log files — they are append-only.
Work through all four phases now."""


# ---------------------------------------------------------------------------
# Session persistence (JSONL)
# ---------------------------------------------------------------------------

def save_session(messages: list[dict], session_id: str) -> None:
    """Serialize messages to JSONL and update the last-session symlink."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = SESSIONS_DIR / f"{session_id}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(serialize_message(msg), default=str) + "\n")

    # Update symlink
    link = SESSIONS_DIR / "last-session"
    link.unlink(missing_ok=True)
    link.symlink_to(path.name)


def load_session(session_id: str | None = None) -> list[dict] | None:
    """Load messages from JSONL. If no ID, follow the last-session symlink."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    if session_id:
        path = SESSIONS_DIR / f"{session_id}.jsonl"
    else:
        link = SESSIONS_DIR / "last-session"
        if not link.exists():
            return None
        path = SESSIONS_DIR / link.resolve().name

    if not path.exists():
        return None

    messages = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                messages.append(json.loads(line))
    return messages or None


def serialize_message(msg: dict) -> dict:
    """Handle both Anthropic SDK objects (.model_dump()) and plain dicts."""
    content = msg.get("content")
    if content is None:
        return dict(msg)

    if isinstance(content, list):
        serialized = []
        for item in content:
            if hasattr(item, "model_dump"):
                serialized.append(item.model_dump())
            elif isinstance(item, dict):
                serialized.append(item)
            else:
                # ContentBlock or similar — try to convert
                serialized.append({"type": "text", "text": str(item)})
        return {"role": msg["role"], "content": serialized}

    return dict(msg)
