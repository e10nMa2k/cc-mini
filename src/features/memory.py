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
MAX_ENTRYPOINT_LINES = 200
ENTRYPOINT_NAME = "MEMORY.md"
LOCK_FILE_NAME = ".consolidate-lock"
HOLDER_STALE_S = 3600  # 1 hour — reclaim lock after this
SESSION_SCAN_INTERVAL_S = 600  # 10 minutes — scan throttle

# Module-level scan throttle state (mirrors TS closure in initAutoDream)
_last_session_scan_at: float = 0.0


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
    """Check all gates: time ≥ min_hours AND sessions ≥ min_sessions.

    Includes a 10-minute scan throttle (mirrors TS SESSION_SCAN_INTERVAL_MS)
    to avoid re-scanning sessions every turn when time-gate passes but
    session-gate doesn't.
    """
    global _last_session_scan_at

    last = read_last_consolidated_at(memory_dir)
    now = datetime.now().timestamp()
    hours_since = (now - last) / 3600 if last > 0 else float("inf")

    if hours_since < min_hours:
        return False

    # Scan throttle: skip session counting if last scan was < 10 min ago
    if now - _last_session_scan_at < SESSION_SCAN_INTERVAL_S:
        return False
    _last_session_scan_at = now

    # Count sessions newer than last consolidation, exclude current
    scan_dir = sessions_dir or SESSIONS_DIR
    count = 0
    if scan_dir.exists():
        for f in scan_dir.iterdir():
            if f.suffix == ".jsonl" and current_session_id not in f.name and f.stat().st_mtime > last:
                count += 1

    return count >= min_sessions


def list_sessions_since(since_ts: float, sessions_dir: Path | None = None,
                        current_session_id: str = "") -> list[str]:
    """Return session IDs (filenames without .jsonl) touched since since_ts."""
    scan_dir = sessions_dir or SESSIONS_DIR
    result: list[str] = []
    if not scan_dir.exists():
        return result
    for f in scan_dir.iterdir():
        if (f.suffix == ".jsonl"
                and current_session_id not in f.name
                and f.stat().st_mtime > since_ts):
            result.append(f.stem)
    return result


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

def build_dream_prompt(memory_dir: Path, transcript_dir: str = "",
                       session_ids: list[str] | None = None) -> str:
    """Build the 4-phase consolidation prompt for the dream agent.

    Closely mirrors Claude Code's consolidationPrompt.ts.
    """
    extra_parts: list[str] = []

    # Tool constraints note (matches TS: added as `extra` only for auto-dream)
    extra_parts.append(
        "**Tool constraints for this run:** Bash is not available. "
        "Edit and Write are restricted to files within the memory directory. "
        "Read, Grep, and Glob are unrestricted. Plan your exploration with "
        "this in mind."
    )

    if session_ids:
        extra_parts.append(
            f"Sessions since last consolidation ({len(session_ids)}):\n"
            + "\n".join(f"- {sid}" for sid in session_ids)
        )

    extra = "\n\n".join(extra_parts)
    extra_section = f"\n\n## Additional context\n\n{extra}" if extra else ""

    transcript_line = ""
    if transcript_dir:
        transcript_line = (
            f"\nSession transcripts: `{transcript_dir}` "
            "(large JSONL files — grep narrowly, don't read whole files)\n"
        )

    return f"""\
# Dream: Memory Consolidation

You are performing a dream — a reflective pass over your memory files. \
Synthesize what you've learned recently into durable, well-organized memories \
so that future sessions can orient quickly.

Memory directory: `{memory_dir}`
This directory already exists — write to it directly with the Write tool \
(do not run mkdir or check for its existence).
{transcript_line}
---

## Phase 1 — Orient

- Use Glob to list all files in `{memory_dir}/` to see what already exists
- Read `{ENTRYPOINT_NAME}` to understand the current index
- Skim existing topic files so you improve them rather than creating duplicates
- If `logs/` or `sessions/` subdirectories exist, review recent entries there

## Phase 2 — Gather recent signal

Look for new information worth persisting. Sources in rough priority order:

1. **Daily logs** (`logs/YYYY/MM/YYYY-MM-DD.md`) if present — these are the \
append-only stream
2. **Existing memories that drifted** — facts that contradict something you \
see in the codebase now
3. **Transcript search** — if you need specific context (e.g., "what was the \
error message from yesterday's build failure?"), grep the JSONL transcripts \
for narrow terms:
   `grep -rn "<narrow term>" {transcript_dir}/ --include="*.jsonl" | tail -50`

Don't exhaustively read transcripts. Look only for things you already suspect \
matter.

## Phase 3 — Consolidate

For each thing worth remembering, write or update a memory file at the top \
level of the memory directory. Use the memory file format and type conventions \
from your system prompt's auto-memory section — it's the source of truth for \
what to save, how to structure it, and what NOT to save.

Focus on:
- Merging new signal into existing topic files rather than creating \
near-duplicates
- Converting relative dates ("yesterday", "last week") to absolute dates so \
they remain interpretable after time passes
- Deleting contradicted facts — if today's investigation disproves an old \
memory, fix it at the source

## Phase 4 — Prune and index

Update `{ENTRYPOINT_NAME}` so it stays under {MAX_ENTRYPOINT_LINES} lines \
AND under ~25KB. It's an **index**, not a dump — each entry should be one \
line under ~150 characters: `- [Title](file.md) — one-line hook`. Never \
write memory content directly into it.

- Remove pointers to memories that are now stale, wrong, or superseded
- Demote verbose entries: if an index line is over ~200 chars, it's carrying \
content that belongs in the topic file — shorten the line, move the detail
- Add pointers to newly important memories
- Resolve contradictions — if two files disagree, fix the wrong one

---

Return a brief summary of what you consolidated, updated, or pruned. \
If nothing changed (memories are already tight), say so.{extra_section}"""


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
