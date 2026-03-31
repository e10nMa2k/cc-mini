"""Slash command system — parsing and dispatch.

Modelled after claude-code's ``src/commands.ts``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from .compact import CompactService
    from .config import AppConfig
    from .engine import Engine
    from .permissions import PermissionChecker
    from .session import SessionStore


# ---------------------------------------------------------------------------
# Context bundle passed to every command handler
# ---------------------------------------------------------------------------

@dataclass
class CommandContext:
    engine: Engine
    session_store: SessionStore | None
    compact_service: CompactService
    console: Console
    app_config: AppConfig
    memory_dir: Path | None = None
    permissions: PermissionChecker | None = None
    run_dream: object = None  # Callable[[], None]
    # Callable that creates a fresh SessionStore (for /clear, /resume)
    new_session_store: object = None  # Callable[[], SessionStore]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_command(text: str) -> tuple[str, str] | None:
    """If *text* starts with ``/``, return ``(command_name, args)``."""
    text = text.strip()
    if not text.startswith("/"):
        return None
    parts = text.split(None, 1)
    name = parts[0][1:].lower()  # strip leading /
    args = parts[1] if len(parts) > 1 else ""
    return name, args


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _cmd_help(ctx: CommandContext, args: str) -> None:
    table = Table(title="Available Commands", show_header=True, header_style="bold cyan")
    table.add_column("Command", style="green")
    table.add_column("Description")
    for name, desc, _ in _COMMAND_TABLE:
        table.add_row(f"/{name}", desc)
    ctx.console.print(table)


def _cmd_compact(ctx: CommandContext, args: str) -> None:
    from .compact import estimate_tokens

    messages = ctx.engine.get_messages()
    if len(messages) < 4:
        ctx.console.print("[dim]Too few messages to compact.[/dim]")
        return

    pre_tokens = estimate_tokens(messages)
    ctx.console.print(f"[dim]Compacting {len(messages)} messages (~{pre_tokens:,} tokens)…[/dim]")

    new_msgs, summary = ctx.compact_service.compact(
        messages, ctx.engine.get_system_prompt(), custom_instructions=args,
    )
    ctx.engine.set_messages(new_msgs)

    # Persist compacted state to a fresh session store if available
    if ctx.session_store is not None:
        _persist_compacted(ctx, new_msgs)

    post_tokens = estimate_tokens(new_msgs)
    ctx.console.print(
        f"[green]✓[/green] Compacted: {pre_tokens:,} → {post_tokens:,} tokens "
        f"({len(messages)} → {len(new_msgs)} messages)"
    )


def _persist_compacted(ctx: CommandContext, new_msgs: list[dict]) -> None:
    """Re-write the current session with compacted messages."""
    if ctx.session_store is None:
        return
    # Create a new session store pointing to the same session id,
    # overwrite the JSONL with the compacted messages.
    import json
    from .session import _serialize_message, _now_iso
    path = ctx.session_store._jsonl_path
    with open(path, "w", encoding="utf-8") as fh:
        for msg in new_msgs:
            safe = _serialize_message(msg)
            safe["_ts"] = _now_iso()
            fh.write(json.dumps(safe, ensure_ascii=False) + "\n")
    ctx.session_store._message_count = len(new_msgs)
    ctx.session_store._save_meta()


def _cmd_history(ctx: CommandContext, args: str) -> None:
    from .session import SessionStore

    cwd = str(os.getcwd())
    sessions = SessionStore.list_sessions(cwd)
    if not sessions:
        ctx.console.print("[dim]No saved sessions for this directory.[/dim]")
        return

    table = Table(title="Session History", show_header=True, header_style="bold cyan")
    table.add_column("#", style="dim", width=4)
    table.add_column("ID", style="dim", width=10)
    table.add_column("Title")
    table.add_column("Messages", justify="right", width=8)
    table.add_column("Updated", width=20)

    for i, meta in enumerate(sessions, 1):
        table.add_row(
            str(i),
            meta.session_id[:8],
            meta.title[:50],
            str(meta.message_count),
            meta.updated_at[:19].replace("T", " "),
        )
    ctx.console.print(table)


def _cmd_resume(ctx: CommandContext, args: str) -> None:
    from .session import SessionStore

    cwd = str(os.getcwd())
    sessions = SessionStore.list_sessions(cwd)

    if not sessions:
        ctx.console.print("[dim]No saved sessions to resume.[/dim]")
        return

    if not args:
        # Show list and ask user to pick
        _cmd_history(ctx, "")
        ctx.console.print("\n[dim]Usage: /resume <number> or /resume <session-id>[/dim]")
        return

    # Try as numeric index
    target_meta = None
    try:
        idx = int(args.strip()) - 1
        if 0 <= idx < len(sessions):
            target_meta = sessions[idx]
    except ValueError:
        pass

    # Try as session-id prefix
    if target_meta is None:
        needle = args.strip().lower()
        for meta in sessions:
            if meta.session_id.lower().startswith(needle):
                target_meta = meta
                break

    if target_meta is None:
        ctx.console.print(f"[red]Session not found: {args}[/red]")
        return

    # Skip if resuming the current session
    if ctx.session_store and target_meta.session_id == ctx.session_store.session_id:
        ctx.console.print("[dim]Already in this session.[/dim]")
        return

    # Load messages
    messages = SessionStore.load_messages(target_meta.session_id, cwd)
    if not messages:
        ctx.console.print("[red]Session has no messages.[/red]")
        return

    # Create new session store pointing to the resumed session
    new_store = ctx.new_session_store  # type: ignore[call-arg]
    resumed_store = type(ctx.session_store)(  # type: ignore[arg-type]
        cwd=cwd,
        model=ctx.app_config.model,
        session_id=target_meta.session_id,
    ) if ctx.session_store else None

    ctx.engine.set_messages(messages)
    if resumed_store is not None:
        ctx.engine.set_session_store(resumed_store)
        ctx.session_store = resumed_store  # type: ignore[assignment]

    ctx.console.print(
        f"[green]✓[/green] Resumed session [bold]{target_meta.session_id[:8]}[/bold]: "
        f"{target_meta.title[:50]}  ({len(messages)} messages)"
    )


def _cmd_clear(ctx: CommandContext, args: str) -> None:
    ctx.engine.set_messages([])
    if callable(ctx.new_session_store):
        new_store = ctx.new_session_store()
        ctx.engine.set_session_store(new_store)
        ctx.session_store = new_store  # type: ignore[assignment]
    ctx.console.print("[green]✓[/green] Conversation cleared. New session started.")


def _cmd_memory(ctx: CommandContext, args: str) -> None:
    from .memory import load_memory_index

    if ctx.memory_dir is None:
        ctx.console.print("[dim]Memory system not configured.[/dim]")
        return
    index = load_memory_index(ctx.memory_dir)
    if index:
        ctx.console.print(index)
    else:
        ctx.console.print("[dim]No memories yet. Use /dream to consolidate daily logs.[/dim]")


def _cmd_remember(ctx: CommandContext, args: str) -> None:
    from .memory import append_to_daily_log

    if ctx.memory_dir is None:
        ctx.console.print("[dim]Memory system not configured.[/dim]")
        return
    if not args.strip():
        ctx.console.print("[dim]Usage: /remember <text>[/dim]")
        return
    append_to_daily_log(ctx.memory_dir, args.strip())
    ctx.console.print("[dim]Saved to daily log.[/dim]")


def _cmd_dream(ctx: CommandContext, args: str) -> None:
    if ctx.run_dream is None or not callable(ctx.run_dream):
        ctx.console.print("[dim]Dream not available.[/dim]")
        return
    ctx.run_dream()


# ---------------------------------------------------------------------------
# Command registry
# ---------------------------------------------------------------------------

# (name, description, handler)
_COMMAND_TABLE: list[tuple[str, str, object]] = [
    ("help",     "Show available commands",                         _cmd_help),
    ("compact",  "Compress conversation context [instructions]",    _cmd_compact),
    ("resume",   "Resume a past session [number|session-id]",       _cmd_resume),
    ("history",  "List saved sessions for this directory",          _cmd_history),
    ("clear",    "Clear conversation, start new session",           _cmd_clear),
    ("memory",   "Show current memory index",                       _cmd_memory),
    ("remember", "Save a note to the daily log [text]",             _cmd_remember),
    ("dream",    "Consolidate daily logs into topic files",          _cmd_dream),
]

_HANDLERS: dict[str, object] = {name: handler for name, _, handler in _COMMAND_TABLE}


def handle_command(name: str, args: str, ctx: CommandContext) -> bool:
    """Dispatch slash command. Returns True if handled, False otherwise."""
    handler = _HANDLERS.get(name)
    if handler is None:
        ctx.console.print(f"[red]Unknown command: /{name}[/red]  (try /help)")
        return False
    handler(ctx, args)  # type: ignore[operator]
    return True
