from __future__ import annotations

import argparse
import base64
import mimetypes
import re
import sys
import time
import threading
from datetime import datetime
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style as PTStyle
from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

from .config import load_app_config
from .context import build_system_prompt
from .engine import AbortedError, Engine
from .session import SessionStore
from .compact import CompactService, estimate_tokens, should_compact
from .commands import parse_command, handle_command, CommandContext
from ._keylistener import EscListener
from .permissions import PermissionChecker
from .tools.bash import BashTool
from .tools.file_edit import FileEditTool
from .tools.file_read import FileReadTool
from .tools.file_write import FileWriteTool
from .tools.glob_tool import GlobTool
from .tools.grep_tool import GrepTool
from .memory import (
    ensure_memory_dir,
    extract_memory_tags,
    append_to_daily_log,
    build_dream_prompt,
    should_auto_dream,
    try_acquire_lock,
    release_lock,
    record_consolidation,
)

console = Console()
_HISTORY_FILE = Path.home() / ".cc_mini_history"

# Match claude-code-main: useDoublePress DOUBLE_PRESS_TIMEOUT_MS = 800
_DOUBLE_PRESS_TIMEOUT_MS = 0.8


# ---------------------------------------------------------------------------
# Slash command autocomplete — shows suggestions when user types "/"
# Matches claude-code-main's commandSuggestions.ts behavior
# ---------------------------------------------------------------------------

class _SlashCommandCompleter(Completer):
    """Autocomplete for slash commands. Triggers when input starts with "/"."""

    # (name, description) — built-in + buddy
    COMMANDS: list[tuple[str, str]] = [
        ('help',    'Show available commands'),
        ('compact', 'Compress conversation context'),
        ('resume',  'Resume a past session'),
        ('history', 'List saved sessions'),
        ('clear',   'Clear conversation, start new session'),
        ('buddy',   'Companion pet — hatch, pet, stats, mute/unmute'),
        ('buddy pet',   'Pet your companion'),
        ('buddy stats', 'Show companion stats'),
        ('buddy mute',  'Mute companion reactions'),
        ('buddy unmute', 'Unmute companion reactions'),
        ('exit',    'Exit the REPL'),
    ]

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor.lstrip()
        if not text.startswith('/'):
            return

        # Strip the leading "/" for matching
        query = text[1:].lower()

        for name, desc in self.COMMANDS:
            if not query or name.startswith(query):
                # Calculate how many characters to replace
                yield Completion(
                    f'/{name}',
                    start_position=-len(text),
                    display=f'/{name}',
                    display_meta=desc,
                )


_slash_completer = _SlashCommandCompleter()


def _tool_preview(tool_name: str, tool_input: dict) -> str:
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        return cmd[:80] + ("…" if len(cmd) > 80 else "")
    if tool_name in ("Read", "Edit", "Write"):
        fp = tool_input.get("file_path", "")
        return fp[-60:] if len(fp) > 60 else fp
    if tool_name in ("Glob", "Grep"):
        return tool_input.get("pattern", "")
    return ""


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_IMG_PATH_RE = re.compile(r"@(\S+)")


def _parse_input(text: str) -> str | list:
    """Parse user input, extracting @path image references into content blocks.

    Returns plain string if no images, or a list of content blocks if images found.
    """
    matches = list(_IMG_PATH_RE.finditer(text))
    if not matches:
        return text

    image_blocks = []
    for m in matches:
        fpath = Path(m.group(1))
        if not fpath.suffix.lower() in _IMAGE_EXTS:
            continue
        if not fpath.exists():
            continue
        media_type = mimetypes.guess_type(str(fpath))[0] or "image/png"
        data = base64.standard_b64encode(fpath.read_bytes()).decode("ascii")
        image_blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        })

    if not image_blocks:
        return text

    # Remove @path tokens from text
    cleaned = _IMG_PATH_RE.sub("", text).strip()
    content: list[dict] = list(image_blocks)
    if cleaned:
        content.append({"type": "text", "text": cleaned})
    return content


class _SpinnerManager:
    """Manages a Rich Live spinner that shows while waiting for API/tool responses.

    Matches claude-code-main's spinner behavior: show a spinning indicator
    with contextual text while the model is thinking or tools are executing.
    """

    def __init__(self, console: Console):
        self._console = console
        self._live: Live | None = None
        self._spinner_text = "Thinking…"

    def start(self, text: str = "Thinking…"):
        self._spinner_text = text
        self._live = Live(
            Spinner("dots", text=Text(self._spinner_text, style="dim")),
            console=self._console,
            refresh_per_second=12,
        )
        self._live.start()

    def update(self, text: str):
        self._spinner_text = text
        if self._live is not None:
            self._live.update(
                Spinner("dots", text=Text(self._spinner_text, style="dim"))
            )

    def stop(self):
        if self._live is not None:
            # Clear spinner line: update to empty then stop
            self._live.update("")
            self._live.stop()
            self._live = None


def run_query(engine: Engine, user_input: str | list, print_mode: bool,
              permissions: PermissionChecker | None = None) -> None:
    """Run a single turn. Ctrl+C or Esc cancels the active turn."""
    listener = EscListener(on_cancel=engine.abort)
    if permissions:
        permissions.set_esc_listener(listener)

    spinner = _SpinnerManager(console)
    first_text = True
    streaming = False

    try:
        with listener:
            spinner.start("Thinking…")

            for event in engine.submit(user_input):
                if streaming and listener.check_esc_nonblocking():
                    spinner.stop()
                    engine.cancel_turn()
                    console.print("\n[dim yellow]⏹ Turn cancelled (Esc)[/dim yellow]")
                    return

                if event[0] == "text":
                    if first_text:
                        spinner.stop()
                        listener.pause()
                        streaming = True
                        first_text = False
                    if print_mode:
                        print(event[1], end="", flush=True)
                    else:
                        console.print(event[1], end="", markup=False)

                elif event[0] == "waiting":
                    streaming = False
                    listener.resume()
                    spinner.start("Preparing tool call…")

                elif event[0] == "tool_call":
                    spinner.stop()
                    streaming = False
                    listener.pause()
                    _, tool_name, tool_input = event
                    preview = _tool_preview(tool_name, tool_input)
                    console.print(f"\n[dim]↳ {tool_name}({preview}) …[/dim]")

                elif event[0] == "tool_result":
                    _, tool_name, tool_input, result = event
                    status = "[red]✗[/red]" if result.is_error else "[green]✓[/green]"
                    console.print(f"[dim]  {status} done[/dim]")
                    if result.is_error:
                        console.print(f"  [red]{result.content[:300]}[/red]")
                    streaming = False
                    listener.resume()
                    spinner.start("Thinking…")
                    first_text = True

                elif event[0] == "error":
                    spinner.stop()
                    console.print(f"\n[bold red]{event[1]}[/bold red]")

            spinner.stop()
    except (AbortedError, KeyboardInterrupt):
        spinner.stop()
        if not isinstance(sys.exc_info()[1], AbortedError):
            engine.cancel_turn()
        console.print("\n[dim yellow]⏹ Turn cancelled[/dim yellow]")
        return
    finally:
        spinner.stop()
        if permissions:
            permissions.set_esc_listener(None)

    if not print_mode:
        console.print()


def _run_dream(engine: Engine, memory_dir: Path,
               permissions: PermissionChecker) -> None:
    """Run dream consolidation: snapshot messages, submit dream prompt, restore."""
    console.print("[dim]Starting dream consolidation…[/dim]")
    saved_messages = list(engine.messages)
    engine.messages = []
    dream_prompt = build_dream_prompt(memory_dir)
    run_query(engine, dream_prompt, print_mode=False, permissions=permissions)
    engine.messages = saved_messages
    # Rebuild system prompt to pick up updated MEMORY.md
    engine.system_prompt = build_system_prompt(memory_dir=memory_dir)
    record_consolidation(memory_dir)
    console.print("[dim]Dream consolidation complete. Memory index updated.[/dim]")


def main() -> None:
    parser = argparse.ArgumentParser(prog="cc-mini",
                                     description="Minimal Python Claude Code")
    parser.add_argument("prompt", nargs="?", help="Prompt to send (optional)")
    parser.add_argument("-p", "--print", action="store_true",
                        help="Non-interactive: print response and exit")
    parser.add_argument("--auto-approve", action="store_true",
                        help="Auto-approve all tool permissions (dangerous)")
    parser.add_argument("--config", help="Path to a TOML config file")
    parser.add_argument("--api-key", help="Anthropic API key")
    parser.add_argument("--base-url", help="Anthropic-compatible API base URL")
    parser.add_argument("--model", help="Model name, e.g. claude-sonnet-4")
    parser.add_argument("--max-tokens", type=int,
                        help="Maximum output tokens for each model response")
    parser.add_argument("--resume", metavar="SESSION",
                        help="Resume a previous session (id or index)")
    parser.add_argument("--memory-dir", help="Override memory directory path")
    parser.add_argument("--no-auto-dream", action="store_true",
                        help="Disable automatic dream consolidation")
    parser.add_argument("--dream-interval", type=float,
                        help="Hours between auto-dream runs (default: 24)")
    parser.add_argument("--dream-min-sessions", type=int,
                        help="Minimum new sessions before auto-dream triggers (default: 5)")
    args = parser.parse_args()

    try:
        app_config = load_app_config(args)
    except ValueError as exc:
        parser.error(str(exc))

    # Memory setup
    memory_dir = app_config.memory_dir
    ensure_memory_dir(memory_dir)
    session_id = datetime.now().strftime("%Y%m%d-%H%M%S")

    tools = [FileReadTool(), GlobTool(), GrepTool(), FileEditTool(), FileWriteTool(), BashTool()]
    system_prompt = build_system_prompt(memory_dir=memory_dir)
    permissions = PermissionChecker(auto_approve=args.auto_approve)

    cwd = str(Path.cwd())

    # Session & compact services
    session_store: SessionStore | None = None
    if not args.print:
        session_store = SessionStore(cwd=cwd, model=app_config.model)

    engine = Engine(
        tools=tools,
        system_prompt=system_prompt,
        permission_checker=permissions,
        api_key=app_config.api_key,
        base_url=app_config.base_url,
        model=app_config.model,
        max_tokens=app_config.max_tokens,
        session_store=session_store,
    )
    compact_service = CompactService(client=engine._client, model=app_config.model)

    # Handle --resume
    if args.resume and session_store is not None:
        sessions = SessionStore.list_sessions(cwd)
        target = None
        try:
            idx = int(args.resume) - 1
            if 0 <= idx < len(sessions):
                target = sessions[idx]
        except ValueError:
            needle = args.resume.lower()
            for m in sessions:
                if m.session_id.lower().startswith(needle):
                    target = m
                    break
        if target:
            msgs = SessionStore.load_messages(target.session_id, cwd)
            if msgs:
                engine.set_messages(msgs)
                session_store = SessionStore(cwd=cwd, model=app_config.model,
                                            session_id=target.session_id)
                engine.set_session_store(session_store)
                console.print(f"[green]✓[/green] Resumed: {target.title[:50]}  "
                              f"({len(msgs)} messages)")
        else:
            console.print(f"[red]Session not found: {args.resume}[/red]")

    # Non-interactive / piped
    if args.print or args.prompt:
        prompt_text = args.prompt or sys.stdin.read()
        run_query(engine, _parse_input(prompt_text), print_mode=args.print, permissions=permissions)
        return

    # Interactive REPL
    config_note = f"[dim]{app_config.model} · max_tokens={app_config.max_tokens}[/dim]"
    session_note = f"[dim]session {session_store.session_id[:8]}[/dim]" if session_store else ""
    console.print("[bold cyan]Mini Claude Code[/bold cyan]  "
                  f"{config_note}  {session_note}  "
                  "[dim]Esc or Ctrl+C to cancel, Ctrl+C twice to exit[/dim]")
    console.print('[dim]Enter to send, Alt+Enter for newline, /help for commands[/dim]\n')

    kb = KeyBindings()

    @kb.add("escape", "enter")
    def _(event):
        event.current_buffer.insert_text("\n")

    session: PromptSession = PromptSession(
        history=FileHistory(str(_HISTORY_FILE)),
        key_bindings=kb,
        completer=_slash_completer,
        complete_while_typing=True,
    )

    # Track last Ctrl+C time for double-press exit (matches useDoublePress)
    last_ctrlc_time = 0.0

    # Companion animator — drives real-time idle animation in bottom_toolbar
    # Matches CompanionSprite.tsx tick-based animation system
    animator = None
    try:
        from .buddy.companion import get_companion
        from .buddy.storage import load_companion_muted
        from .buddy.animator import CompanionAnimator
        if not load_companion_muted():
            comp = get_companion()
            if comp:
                animator = CompanionAnimator(comp)
    except Exception:
        pass

    def _bottom_toolbar():
        """Return animated companion sprite for bottom_toolbar."""
        if animator is None:
            return None
        return animator.toolbar_text()

    def _set_reaction(text: str, print_to_terminal: bool = False) -> None:
        """Observer callback — delivers reaction to animator's toolbar bubble.

        For normal mode (reacting to Claude): only shows in toolbar bubble.
        For direct address mode: also prints to terminal scroll history.
        """
        if animator:
            animator.set_reaction(text)
        if print_to_terminal:
            try:
                from .buddy.companion import get_companion
                from .buddy.types import RARITY_COLORS
                from .buddy.sprites import render_face
                from .buddy.types import CompanionBones
                comp = get_companion()
                if comp:
                    color = RARITY_COLORS.get(comp.rarity, 'dim')
                    bones = CompanionBones(
                        rarity=comp.rarity, species=comp.species,
                        eye=comp.eye, hat=comp.hat, shiny=comp.shiny, stats=comp.stats,
                    )
                    face = render_face(bones)
                    console.print(f'\n[{color}]{face} {comp.name}:[/{color}] [{color} italic]{text}[/{color} italic]')
            except Exception:
                pass

    while True:
        # Start/restart animator before each prompt (picks up newly hatched companions)
        if animator is None:
            try:
                from .buddy.companion import get_companion
                from .buddy.storage import load_companion_muted
                from .buddy.animator import CompanionAnimator
                if not load_companion_muted():
                    comp = get_companion()
                    if comp:
                        animator = CompanionAnimator(comp)
            except Exception:
                pass

        try:
            if animator:
                animator.start()
            # Override default bottom-toolbar reverse-video background
            # so sprite and bubble render with normal terminal colors
            _toolbar_style = PTStyle.from_dict({
                'bottom-toolbar': 'noreverse',
                'bottom-toolbar.text': '',
            })
            user_input = session.prompt(
                "\n> ",
                bottom_toolbar=_bottom_toolbar if animator else None,
                refresh_interval=0.5 if animator else None,
                style=_toolbar_style if animator else None,
            ).strip()
        except KeyboardInterrupt:
            now = time.monotonic()
            if now - last_ctrlc_time <= _DOUBLE_PRESS_TIMEOUT_MS:
                if animator:
                    animator.stop()
                console.print("\n[dim]Goodbye.[/dim]")
                break
            last_ctrlc_time = now
            console.print("\n[dim yellow]Press Ctrl+C again to exit[/dim yellow]")
            continue
        except EOFError:
            if animator:
                animator.stop()
            console.print("\n[dim]Goodbye.[/dim]")
            break
        finally:
            if animator:
                animator.stop()

        # Reset double-press timer on any normal input
        last_ctrlc_time = 0.0

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
            console.print("[dim]Goodbye.[/dim]")
            break

        # Slash commands (session, compact, help, etc.)
        cmd = parse_command(user_input)
        if cmd is not None:
            cmd_name, cmd_args = cmd
            if cmd_name in ("exit", "quit"):
                console.print("[dim]Goodbye.[/dim]")
                break
            # /buddy is handled separately (companion pet)
            if cmd_name == "buddy":
                from .buddy.commands import handle_buddy_command
                handle_buddy_command(cmd_args, engine._client, console)
                # Refresh animator in case companion was just hatched
                try:
                    from .buddy.companion import get_companion
                    from .buddy.animator import CompanionAnimator
                    comp = get_companion()
                    if comp:
                        animator = CompanionAnimator(comp)
                except Exception:
                    pass
                continue
            cmd_ctx = CommandContext(
                engine=engine,
                session_store=session_store,
                compact_service=compact_service,
                console=console,
                app_config=app_config,
                memory_dir=memory_dir,
                permissions=permissions,
                run_dream=lambda: _run_dream(engine, memory_dir, permissions),
                new_session_store=lambda: SessionStore(cwd=cwd, model=app_config.model),
            )
            handle_command(cmd_name, cmd_args, cmd_ctx)
            session_store = cmd_ctx.session_store
            continue

        # Auto-compact when approaching token limits
        if should_compact(engine.get_messages()):
            console.print("[dim]Auto-compacting conversation…[/dim]")
            try:
                new_msgs, _ = compact_service.compact(
                    engine.get_messages(), engine.get_system_prompt())
                engine.set_messages(new_msgs)
                console.print(f"[dim]Context compressed to {estimate_tokens(new_msgs):,} tokens.[/dim]")
            except Exception as e:
                console.print(f"[dim red]Auto-compact failed: {e}[/dim red]")

        # Check if user is talking directly to companion — skip Claude, let
        # companion reply directly via observer (no awkward "." response)
        _companion_addressed = False
        try:
            from .buddy.companion import get_companion
            from .buddy.storage import load_companion_muted
            from .buddy.observer import fire_companion_observer, _is_addressed
            if not load_companion_muted():
                comp = get_companion()
                if comp and _is_addressed(user_input, comp.name):
                    _companion_addressed = True
                    import threading
                    reply_event = threading.Event()
                    def _direct_reply(text: str) -> None:
                        _set_reaction(text, print_to_terminal=True)
                        reply_event.set()
                    fire_companion_observer(
                        '', comp, engine._client, _direct_reply,
                        user_msg=user_input,
                    )
                    reply_event.wait(timeout=10)
        except Exception:
            pass

        if _companion_addressed:
            continue

        run_query(engine, _parse_input(user_input), print_mode=False, permissions=permissions)

        # Fire companion observer in background after each turn
        try:
            from .buddy.companion import get_companion
            from .buddy.storage import load_companion_muted
            from .buddy.observer import fire_companion_observer
            if not load_companion_muted():
                comp = get_companion()
                if comp and engine._messages:
                    last_msg = engine._messages[-1]
                    if last_msg.get("role") == "assistant":
                        content = last_msg.get("content", "")
                        if isinstance(content, str):
                            assistant_text = content
                        elif isinstance(content, list):
                            parts = []
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    parts.append(block.get("text", ""))
                                elif hasattr(block, "text"):
                                    parts.append(block.text)
                            assistant_text = ' '.join(parts)
                        else:
                            assistant_text = str(content)
                        if assistant_text.strip():
                            fire_companion_observer(
                                assistant_text, comp, engine._client, _set_reaction,
                                user_msg=user_input,
                            )
        except Exception:
            pass  # Non-essential

        # Post-turn: extract <memory> tags
        text = engine.last_assistant_text()
        for mem in extract_memory_tags(text):
            append_to_daily_log(memory_dir, mem)

        # Auto-dream gate check
        current_sid = session_store.session_id if session_store else session_id
        sessions_path = session_store._dir if session_store else None
        if app_config.auto_dream and should_auto_dream(
            memory_dir,
            min_hours=app_config.dream_interval_hours,
            min_sessions=app_config.dream_min_sessions,
            current_session_id=current_sid,
            sessions_dir=sessions_path,
        ):
            if try_acquire_lock(memory_dir):
                console.print("\n[dim]Auto-dream triggered (enough time + sessions since last consolidation)…[/dim]")
                _run_dream(engine, memory_dir, permissions)
                release_lock(memory_dir)


if __name__ == "__main__":
    main()
