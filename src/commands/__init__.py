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

from features.coordinator import current_session_mode, match_session_mode

if TYPE_CHECKING:
    from features.compact import CompactService
    from core.config import AppConfig
    from features.cost_tracker import CostTracker
    from core.engine import Engine
    from core.permissions import PermissionChecker
    from core.session import SessionStore


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
    run_dream: object = None
    cost_tracker: CostTracker | None = None
    new_session_store: object = None
    reconfigure_mode: object = None
    plan_manager: object = None
    pending_query: str | None = None  # set by commands that want a follow-up model query


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
    from features.compact import estimate_tokens

    messages = ctx.engine.get_messages()
    if len(messages) < 4:
        ctx.console.print("[dim]Too few messages to compact.[/dim]")
        return

    pre_tokens = estimate_tokens(messages)
    ctx.console.print(f"[dim]Compacting {len(messages)} messages (~{pre_tokens:,} tokens)…[/dim]")

    new_msgs, summary = ctx.compact_service.compact(
        messages, ctx.engine.system_prompt, custom_instructions=args,
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
    from core.session import _serialize_message, _now_iso
    path = ctx.session_store._jsonl_path
    with open(path, "w", encoding="utf-8") as fh:
        for msg in new_msgs:
            safe = _serialize_message(msg)
            safe["_ts"] = _now_iso()
            fh.write(json.dumps(safe, ensure_ascii=False) + "\n")
    ctx.session_store._message_count = len(new_msgs)
    ctx.session_store._save_meta()


def _cmd_history(ctx: CommandContext, args: str) -> None:
    from core.session import SessionStore

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
    from core.session import SessionStore

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
    meta, messages = SessionStore.load_session(target_meta.session_id, cwd)
    if not messages:
        ctx.console.print("[red]Session has no messages.[/red]")
        return

    warning = None
    session_mode = meta.mode if meta is not None else None
    if callable(ctx.reconfigure_mode):
        warning = ctx.reconfigure_mode(session_mode)
    else:
        warning = match_session_mode(session_mode)

    # Create new session store pointing to the resumed session
    new_store = ctx.new_session_store  # type: ignore[call-arg]
    resumed_store = type(ctx.session_store)(  # type: ignore[arg-type]
        cwd=cwd,
        model=ctx.app_config.model,
        session_id=target_meta.session_id,
        mode=current_session_mode(),
    ) if ctx.session_store else None

    ctx.engine.set_messages(messages)
    if resumed_store is not None:
        ctx.engine.set_session_store(resumed_store)
        ctx.session_store = resumed_store  # type: ignore[assignment]

    ctx.console.print(
        f"[green]✓[/green] Resumed session [bold]{target_meta.session_id[:8]}[/bold]: "
        f"{target_meta.title[:50]}  ({len(messages)} messages)"
    )
    if warning:
        ctx.console.print(f"[yellow]{warning}[/yellow]")


def _cmd_clear(ctx: CommandContext, args: str) -> None:
    ctx.engine.set_messages([])
    if callable(ctx.new_session_store):
        new_store = ctx.new_session_store()
        ctx.engine.set_session_store(new_store)
        ctx.session_store = new_store  # type: ignore[assignment]
    ctx.console.print("[green]✓[/green] Conversation cleared. New session started.")


def _cmd_memory(ctx: CommandContext, args: str) -> None:
    from features.memory import load_memory_index

    if ctx.memory_dir is None:
        ctx.console.print("[dim]Memory system not configured.[/dim]")
        return
    index = load_memory_index(ctx.memory_dir)
    if index:
        ctx.console.print(index)
    else:
        ctx.console.print("[dim]No memories yet. Use /dream to consolidate daily logs.[/dim]")


def _cmd_remember(ctx: CommandContext, args: str) -> None:
    from features.memory import append_to_daily_log

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


def _cmd_skills(ctx: CommandContext, args: str) -> None:
    """List all available skills."""
    from features.skills import list_skills

    skills = list_skills(user_invocable_only=True)
    if not skills:
        ctx.console.print("[dim]No skills available.[/dim]")
        return

    table = Table(title="Available Skills", show_header=True, header_style="bold cyan")
    table.add_column("Command", style="green")
    table.add_column("Source", style="dim", width=8)
    table.add_column("Description")
    for s in skills:
        hint = f" [{s.argument_hint}]" if s.argument_hint else ""
        table.add_row(f"/{s.name}{hint}", s.source, s.description)
    ctx.console.print(table)
def _cmd_cost(ctx: CommandContext, args: str) -> None:
    if ctx.cost_tracker is None:
        ctx.console.print("[dim]Cost tracking is not available.[/dim]")
        return
    ctx.console.print(ctx.cost_tracker.format_cost())


def _cmd_model(ctx: CommandContext, args: str) -> None:
    from core.config import resolve_model, default_max_tokens_for_model, DEFAULT_MODEL

    provider = ctx.app_config.provider

    if args:
        ctx.engine.set_model(args.strip())
        actual = ctx.engine.get_model()
        ctx.console.print(
            f"[green]✓[/green] Set model to [bold]{actual}[/bold]  "
            f"(max_tokens={default_max_tokens_for_model(actual, provider=provider)})")
        return

    if provider != "anthropic":
        current = ctx.engine.get_model()
        ctx.console.print(
            f"[dim]Current model: {current}[/dim]\n"
            f"[dim]Use /model <name> to switch models for the {provider} provider.[/dim]"
        )
        return

    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    current = ctx.engine.get_model()

    # Marketing name lookup
    _NAMES = {
        "claude-sonnet-4-6": "Sonnet 4.6", "claude-sonnet-4-5": "Sonnet 4.5",
        "claude-sonnet-4": "Sonnet 4", "claude-opus-4-6": "Opus 4.6",
        "claude-opus-4-5": "Opus 4.5", "claude-opus-4-1": "Opus 4.1",
        "claude-opus-4": "Opus 4", "claude-haiku-4-5": "Haiku 4.5",
        "claude-3-5-haiku": "Haiku 3.5",
    }
    display = next((n for p, n in _NAMES.items() if p in current), "Sonnet 4.6")

    # (alias, label, description) — from modelOptions.ts PAYG 1P path
    # 1M context variants omitted: require SDK betas not available in cc-mini
    options = [
        (DEFAULT_MODEL, "Default (recommended)", f"Use the default model (currently {display}) · $3/$15 per Mtok"),
        ("sonnet",      "Sonnet",                "Sonnet 4.6 · Best for everyday tasks · $3/$15 per Mtok"),
        ("opus",        "Opus",                  "Opus 4.6 · Most capable for complex work · $5/$25 per Mtok"),
        ("haiku",       "Haiku",                 "Haiku 4.5 · Fastest for quick answers · $1/$5 per Mtok"),
    ]

    effort_levels = ["low", "medium", "high"]
    effort_sym = {"low": "◑", "medium": "◕", "high": "●"}

    cursor = [0]
    for i, (alias, _, _) in enumerate(options):
        if resolve_model(alias) == current:
            cursor[0] = i
            break

    effort_idx = [2]
    result: list[str | None] = [None]
    max_label = max(len(l) for _, l, _ in options)

    kb = KeyBindings()

    @kb.add("up")
    def _(e): cursor.__setitem__(0, (cursor[0] - 1) % len(options))
    @kb.add("down")
    def _(e): cursor.__setitem__(0, (cursor[0] + 1) % len(options))
    @kb.add("left")
    def _(e): effort_idx.__setitem__(0, (effort_idx[0] - 1) % len(effort_levels))
    @kb.add("right")
    def _(e): effort_idx.__setitem__(0, (effort_idx[0] + 1) % len(effort_levels))

    @kb.add("enter")
    def _(e):
        result[0] = options[cursor[0]][0]
        e.app.exit()

    for i in range(min(len(options), 9)):
        @kb.add(str(i + 1))
        def _(e, idx=i):
            cursor[0] = idx
            result[0] = options[idx][0]
            e.app.exit()

    @kb.add("escape")
    @kb.add("c-c")
    def _(e): e.app.exit()

    def _tokens():
        t = [("bold ansibrightcyan", "  Select model\n"),
             ("ansigray", "  Switch between models. Applies to this session and future\n"
                          "  sessions. For other/previous model names, specify with --model.\n\n")]
        for i, (alias, label, desc) in enumerate(options):
            is_cur = i == cursor[0]
            is_active = resolve_model(alias) == current
            ptr = "❯" if is_cur else " "
            sty = "ansibrightcyan" if is_cur else ""
            chk = " ✔" if is_active else ""
            t.append((sty, f"  {ptr} {i+1}. {(label + chk).ljust(max_label + 3)}"))
            t.append(("ansigray", desc))
            t.append(("", "\n"))

        eff = effort_levels[effort_idx[0]]
        t.append(("", "\n"))
        t.append(("ansigray", "  Effort: "))
        for lvl in effort_levels:
            s = "bold ansibrightcyan" if lvl == eff else "ansigray"
            t.append((s, f" {effort_sym[lvl]} {lvl} "))
        t.append(("", "\n"))
        t.append(("ansigray", "  ↑↓ select · ←→ effort · ↵ confirm · esc cancel"))
        return t

    app: Application = Application(
        layout=Layout(Window(FormattedTextControl(_tokens))),
        key_bindings=kb, full_screen=False)

    try:
        app.run()
    except (EOFError, KeyboardInterrupt):
        pass

    if result[0] is None:
        ctx.console.print(f"[dim]Kept model as {current}[/dim]")
        return

    ctx.engine.set_model(result[0])
    actual = ctx.engine.get_model()
    eff = effort_levels[effort_idx[0]]
    ctx.console.print(
        f"[green]✓[/green] Set model to [bold]{actual}[/bold]  "
        f"(max_tokens={default_max_tokens_for_model(actual, provider=provider)}, effort={eff})"
    )


# ---------------------------------------------------------------------------
# Command registry
# ---------------------------------------------------------------------------

def _cmd_plan(ctx: CommandContext, args: str) -> None:
    """Enter plan mode or show current plan."""
    from features.plan import PlanModeManager
    pm: PlanModeManager | None = ctx.plan_manager  # type: ignore[assignment]
    if pm is None:
        ctx.console.print("[red]Plan mode not available.[/red]")
        return
    if pm.is_active:
        content = pm.get_plan_content()
        if content:
            ctx.console.print(f"[bold]Current plan[/bold] ({pm.plan_file_path}):\n")
            ctx.console.print(content)
        else:
            ctx.console.print(f"[dim]Plan mode active but no plan written yet. File: {pm.plan_file_path}[/dim]")
    else:
        pm.enter()
        ctx.console.print("[green]Enabled plan mode[/green]")
        # If user provided a description, queue it as a follow-up query
        # Matches TS: onDone('Enabled plan mode', { shouldQuery: true })
        description = args.strip()
        if description:
            ctx.pending_query = description


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
    ("skills",   "List all available skills",                       _cmd_skills),
    ("cost",    "Show token usage and cost summary",               _cmd_cost),
    ("model",   "Show or switch model [model-name]",               _cmd_model),
    ("plan",    "Enter plan mode or show current plan",             _cmd_plan),
]

_HANDLERS: dict[str, object] = {name: handler for name, _, handler in _COMMAND_TABLE}


def handle_command(name: str, args: str, ctx: CommandContext) -> bool:
    """Dispatch slash command. Returns True if handled, False otherwise.

    If *name* does not match a built-in command, checks the skill registry
    and executes the skill inline (prompt injection) or forked (isolated turn).
    """
    handler = _HANDLERS.get(name)
    if handler is not None:
        handler(ctx, args)  # type: ignore[operator]
        return True

    # Try as a skill invocation
    from features.skills import get_skill
    skill = get_skill(name)
    if skill is not None:
        return _execute_skill(skill, args, ctx)

    ctx.console.print(f"[red]Unknown command: /{name}[/red]  (try /help or /skills)")
    return False


def _execute_skill(skill, args: str, ctx: CommandContext) -> bool:
    """Execute a skill — inline or forked.

    Inline (default): inject the skill prompt as a user message into the
    current conversation and let the engine process it.

    Forked: run the skill in an isolated turn (save messages, clear, run,
    restore original messages).  Matches claude-code's ``context: 'fork'``.
    """
    from tui.query import run_query

    prompt = skill.get_prompt(args)
    if not prompt:
        ctx.console.print(f"[dim]Skill /{skill.name} produced no prompt.[/dim]")
        return True

    ctx.console.print(f"[dim]Running skill: /{skill.name}…[/dim]")

    if skill.context == "fork":
        # Forked execution: isolated turn
        saved = list(ctx.engine.get_messages())
        ctx.engine.set_messages([])
        try:
            permissions = ctx.permissions
            run_query(ctx.engine, prompt, print_mode=False, permissions=permissions)
        finally:
            # Restore original messages (forked result is ephemeral)
            ctx.engine.set_messages(saved)
    else:
        # Inline execution: inject prompt into ongoing conversation
        permissions = ctx.permissions
        run_query(ctx.engine, prompt, print_mode=False, permissions=permissions)

    return True
