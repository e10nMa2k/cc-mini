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

from prompt_toolkit.application import Application as PTApp
from prompt_toolkit.application.current import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window, FloatContainer, Float
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown as RichMarkdown
from rich.spinner import Spinner
from rich.text import Text

from .config import load_app_config
from .coordinator import (
    current_session_mode,
    get_coordinator_system_prompt,
    get_coordinator_user_context,
    get_worker_system_prompt,
    is_coordinator_mode,
    match_session_mode,
    set_coordinator_mode,
)
from .context import build_system_prompt
from .cost_tracker import CostTracker
from .engine import AbortedError, Engine
from .session import SessionStore
from .compact import CompactService, estimate_tokens, should_compact
from .commands import parse_command, handle_command, CommandContext
from ._keylistener import EscListener
from .permissions import PermissionChecker
from .sandbox.config import load_sandbox_config
from .sandbox.manager import SandboxManager
from .tools.ask_user import AskUserQuestionTool
from .tools.agent import AgentTool, SendMessageTool, TaskStopTool
from .tools.bash import BashTool
from .tools.file_edit import FileEditTool
from .tools.file_read import FileReadTool
from .tools.file_write import FileWriteTool
from .tools.glob_tool import GlobTool
from .tools.grep_tool import GrepTool
from .worker_manager import WorkerManager
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
from .skills import discover_skills, list_skills, build_skills_prompt_section
from .skills_bundled import register_bundled_skills

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

    # Extra commands not in _COMMAND_TABLE (handled separately in the REPL)
    _EXTRA_COMMANDS: list[tuple[str, str]] = [
        ('buddy',            'Companion pet — hatch, pet, stats, mute/unmute, ia'),
        ('buddy pet',        'Pet your companion'),
        ('buddy stats',      'Show companion stats'),
        ('buddy new',        'Hatch a new random companion'),
        ('buddy list',       'View all companions'),
        ('buddy select',     'Switch active companion (e.g. /buddy select 2)'),
        ('buddy mute',       'Mute companion reactions'),
        ('buddy unmute',     'Unmute companion reactions'),
        ('buddy ia',         'Idle Adventure — roguelike world exploration game'),
        ('exit',    'Exit the REPL'),
    ]

    def _all_commands(self) -> list[tuple[str, str]]:
        """Merge _COMMAND_TABLE entries with extra commands for a single source of truth."""
        from .commands import _COMMAND_TABLE
        cmds: list[tuple[str, str]] = [(name, desc) for name, desc, _ in _COMMAND_TABLE]
        cmds.extend(self._EXTRA_COMMANDS)
        return cmds

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor.lstrip()
        if not text.startswith('/'):
            return

        query = text[1:].lower()
        all_commands = self._all_commands()

        # Built-in commands
        for name, desc in all_commands:
            if not query or name.startswith(query):
                yield Completion(
                    f'/{name}',
                    start_position=-len(text),
                    display=f'/{name}',
                    display_meta=desc,
                )

        # Dynamic skill commands
        try:
            from .skills import list_skills
            seen = {name for name, _ in all_commands}
            for skill in list_skills(user_invocable_only=True):
                # Skip if already covered by built-in commands
                if skill.name in seen:
                    continue
                if not query or skill.name.startswith(query):
                    yield Completion(
                        f'/{skill.name}',
                        start_position=-len(text),
                        display=f'/{skill.name}',
                        display_meta=skill.description[:40] if skill.description else 'skill',
                    )
        except Exception:
            pass


_slash_completer = _SlashCommandCompleter()


# ---------------------------------------------------------------------------
# Bordered input prompt — matches claude-code-main PromptInput.tsx
# borderStyle="round", borderLeft=false, borderRight=false
# Uses a custom prompt_toolkit Application so the bottom border sits
# directly below the input content (not at the screen bottom).
# ---------------------------------------------------------------------------

def _bordered_prompt(
    con: Console,
    history: FileHistory | None = None,
    completer: Completer | None = None,
    animator_toolbar=None,
    refresh_interval: float | None = None,
    terminal_mode_ref: list | None = None,
) -> str:
    """Prompt with bordered input box that adapts to content height.

    terminal_mode_ref is a mutable [bool] list so '!' can toggle it in-place.

    Raises KeyboardInterrupt on Ctrl+C, EOFError on Ctrl+D with empty buffer.
    """
    import os

    if terminal_mode_ref is None:
        terminal_mode_ref = [False]

    def _is_terminal():
        return terminal_mode_ref[0]

    def _accept(b):
        get_app().exit(result=b.text)
        return True  # keep text in buffer so final render preserves input

    buf = Buffer(
        history=history,
        completer=completer,
        complete_while_typing=False,
        accept_handler=_accept,
    )

    def _trigger_completion_next_tick():
        """Schedule start_completion on the next event-loop tick.

        This avoids the race with prompt_toolkit's internal completion reset
        that happens synchronously during text insertion.
        """
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            loop.call_soon(lambda: buf.start_completion(select_first=False))
        except RuntimeError:
            pass

    def _on_text_changed(_buf):
        """Trigger completion popup when input starts with '/'."""
        if _buf.text.lstrip().startswith('/'):
            _trigger_completion_next_tick()

    buf.on_text_changed += _on_text_changed

    def _top():
        try:
            w = os.get_terminal_size().columns
        except OSError:
            w = 80
        fill = "\u2500" * max(0, w - 1)
        if _is_terminal():
            return [('bold fg:ansiyellow', f'\u256d{fill}')]
        return [('bold fg:ansicyan', f'\u256d{fill}')]

    def _bot():
        try:
            w = os.get_terminal_size().columns
        except OSError:
            w = 80
        if _is_terminal():
            hints = "\u2500 TERMINAL MODE \u00b7 ! to exit \u00b7 Enter run "
            fill = "\u2500" * max(0, w - 1 - len(hints))
            parts: list[tuple[str, str]] = [('fg:ansiyellow', f'\u2570{hints}{fill}')]
        else:
            hints = "\u2500 Enter send \u00b7 Alt+Enter newline \u00b7 ! shell \u00b7 / commands "
            fill = "\u2500" * max(0, w - 1 - len(hints))
            parts: list[tuple[str, str]] = [('fg:ansicyan', f'\u2570{hints}{fill}')]

        if animator_toolbar:
            extra = animator_toolbar()
            if extra:
                parts.append(('', '\n'))
                parts.extend(extra)
        return parts

    def _line_prefix(lineno, wrap_count):
        """First visual line gets '> ' or '$ ', all others get '  ' padding."""
        if lineno == 0 and wrap_count == 0:
            if _is_terminal():
                return [('bold fg:ansiyellow', '$ ')]
            return [('bold fg:ansicyan', '> ')]
        return [('', '  ')]

    body = HSplit([
        Window(FormattedTextControl(_top), height=1, dont_extend_height=True),
        Window(
            BufferControl(buffer=buf),
            get_line_prefix=_line_prefix,
            height=Dimension(min=1),
            dont_extend_height=True,
            wrap_lines=True,
        ),
        Window(FormattedTextControl(_bot), dont_extend_height=True),
    ])

    root = FloatContainer(
        content=body,
        floats=[
            Float(
                xcursor=True, ycursor=True,
                content=CompletionsMenu(max_height=8, scroll_offset=1),
            ),
        ],
    )

    kb = KeyBindings()

    @kb.add('!')
    def _(event):
        if not buf.text:
            # Toggle terminal mode in-place, no submit
            terminal_mode_ref[0] = not terminal_mode_ref[0]
            event.app.invalidate()  # force UI refresh for color change
        else:
            buf.insert_text('!')

    @kb.add('enter')
    def _(event):
        # Feature: backslash + Enter = newline continuation
        # Check at key-binding level to avoid buffer.reset() clearing text
        if buf.text.endswith('\\'):
            buf.delete_before_cursor(1)
            buf.insert_text('\n')
        else:
            buf.validate_and_handle()

    @kb.add('escape', 'enter')
    def _(event):
        buf.insert_text('\n')

    @kb.add('c-c')
    def _(event):
        event.app.exit(exception=KeyboardInterrupt())

    @kb.add('c-d')
    def _(event):
        if not buf.text:
            event.app.exit(exception=EOFError())

    app = PTApp(
        layout=Layout(root),
        key_bindings=kb,
        full_screen=False,
        refresh_interval=refresh_interval,
    )
    app.layout.focus(buf)
    return app.run()


def _tool_preview(tool_name: str, tool_input: dict) -> str:
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        return cmd[:80] + ("…" if len(cmd) > 80 else "")
    if tool_name in ("Read", "Edit", "Write"):
        fp = tool_input.get("file_path", "")
        return fp[-60:] if len(fp) > 60 else fp
    if tool_name in ("Glob", "Grep"):
        return tool_input.get("pattern", "")
    if tool_name == "Agent":
        return tool_input.get("description", "")[:60]
    if tool_name == "SendMessage":
        return tool_input.get("to", "")
    return ""


def _collapsed_tool_summary(tool_names: list[str], done: bool = False) -> str:
    """Summarize tools by type, matching TS CollapsedReadSearchContent.

    E.g. active: "Reading 5 files…"  done: "Read 5 files"
    """
    from collections import Counter
    counts = Counter(tool_names)
    parts = []
    _ACTIVE = {
        "Read": ("Reading {n} files", "Reading file"),
        "Glob": ("Searching {n} patterns", "Searching"),
        "Grep": ("Searching {n} patterns", "Searching"),
        "Bash": ("Running {n} commands", "Running command"),
        "Edit": ("Editing {n} files", "Editing file"),
        "Write": ("Writing {n} files", "Writing file"),
    }
    _DONE = {
        "Read": ("Read {n} files", "Read file"),
        "Glob": ("Searched {n} patterns", "Searched"),
        "Grep": ("Searched {n} patterns", "Searched"),
        "Bash": ("Ran {n} commands", "Ran command"),
        "Edit": ("Edited {n} files", "Edited file"),
        "Write": ("Wrote {n} files", "Wrote file"),
    }
    labels = _DONE if done else _ACTIVE
    for name, n in counts.items():
        plural, singular = labels.get(name, (f"{name} ×{{n}}", name))
        parts.append(plural.format(n=n) if n > 1 else singular)
    suffix = "" if done else "…"
    return " · ".join(parts) + suffix


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


# ---------------------------------------------------------------------------
# Streaming Markdown Renderer
# ---------------------------------------------------------------------------

# Regex for top-level block boundaries: blank line, heading, fence, hr, list
_BLOCK_BOUNDARY_RE = re.compile(r"\n(?=\n|\#{1,6} |```|---|\* |- |\d+\. )")


class _StreamingMarkdown:
    """Accumulates streamed text and renders markdown incrementally.

    Matches TS StreamingMarkdown approach: splits at block boundaries,
    prints stable (complete) blocks as Rich Markdown, keeps the unstable
    trailing part in a Live widget for real-time updates.
    """

    def __init__(self, console: Console):
        self._console = console
        self._buf = ""
        self._stable_len = 0  # how much of _buf has been printed as stable
        self._live: Live | None = None

    def feed(self, chunk: str) -> None:
        """Add a streamed text chunk and update the display."""
        self._buf += chunk
        self._render()

    def _render(self) -> None:
        # Find the last block boundary in the full buffer
        text = self._buf
        boundary = self._stable_len
        for m in _BLOCK_BOUNDARY_RE.finditer(text, self._stable_len):
            boundary = m.start()

        # Print newly stable blocks
        if boundary > self._stable_len:
            # Stop live widget before printing stable content
            if self._live is not None:
                self._live.stop()
                self._live = None
            stable_text = text[self._stable_len:boundary]
            self._console.print(RichMarkdown(stable_text), end="")
            self._stable_len = boundary

        # Update live widget with the unstable trailing part
        unstable = text[self._stable_len:]
        if unstable:
            if self._live is None:
                self._live = Live(
                    RichMarkdown(unstable),
                    console=self._console,
                    refresh_per_second=8,
                    transient=True,
                )
                self._live.start()
            else:
                self._live.update(RichMarkdown(unstable))

    def flush(self) -> None:
        """Finalize: render any remaining text as stable markdown."""
        if self._live is not None:
            self._live.stop()
            self._live = None
        remaining = self._buf[self._stable_len:]
        if remaining:
            self._console.print(RichMarkdown(remaining), end="")
        self._buf = ""
        self._stable_len = 0


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
        # Stop existing Live instance if running
        if self._live is not None:
            self._live.stop()
            self._live = None
        self._live = Live(
            Spinner("dots", text=Text(self._spinner_text, style="dim")),
            console=self._console,
            refresh_per_second=12,
            transient=True,
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
            self._live.stop()
            self._live = None


def run_query(engine: Engine, user_input: str | list, print_mode: bool,
              permissions: PermissionChecker | None = None,
              quiet: bool = False) -> None:
    """Run a single turn. Ctrl+C or Esc cancels the active turn.

    If *quiet* is True, suppress all terminal output (spinner, tool calls, text).
    Used for background tasks like auto-dream.
    """
    listener = EscListener(on_cancel=engine.abort)
    if permissions:
        permissions.set_esc_listener(listener)

    spinner = _SpinnerManager(console)
    md_stream = _StreamingMarkdown(console)
    first_text = True
    streaming = False
    # Track pending tool calls for spinner display.
    # key: unique tool key, value: (tool_name, display_line)
    pending_tools: dict[str, tuple[str, str]] = {}

    try:
        with listener:
            if not quiet:
                spinner.start("Thinking…")

            for event in engine.submit(user_input):
                if not quiet and streaming and listener.check_esc_nonblocking():
                    md_stream.flush()
                    spinner.stop()
                    engine.cancel_turn()
                    console.print("\n[dim yellow]⏹ Turn cancelled (Esc)[/dim yellow]")
                    return

                if event[0] == "text":
                    if quiet:
                        continue
                    if first_text:
                        spinner.stop()
                        listener.pause()
                        streaming = True
                        first_text = False
                    if print_mode:
                        print(event[1], end="", flush=True)
                    else:
                        md_stream.feed(event[1])

                elif event[0] == "waiting":
                    if not quiet:
                        md_stream.flush()
                    streaming = False
                    if not quiet:
                        listener.resume()
                        spinner.start("Preparing tool call…")

                elif event[0] == "tool_call":
                    if not quiet:
                        spinner.stop()
                        streaming = False
                        listener.pause()
                        _, tool_name, tool_input, activity = event
                        preview = _tool_preview(tool_name, tool_input)
                        key = f"{tool_name}({preview})"
                        pending_tools[key] = (tool_name, f"↳ {key}")

                elif event[0] == "tool_executing":
                    if not quiet:
                        _, tool_name, tool_input, activity = event
                        n = len(pending_tools)
                        if n > 1:
                            names = [tn for tn, _ in pending_tools.values()]
                            spinner.start(_collapsed_tool_summary(names))
                        else:
                            _, line = next(iter(pending_tools.values()), ("", f"↳ {tool_name}"))
                            activity_text = activity or f"Running {tool_name}…"
                            spinner.start(f"{line} … {activity_text}")

                elif event[0] == "tool_result":
                    if not quiet:
                        spinner.stop()
                        _, tool_name, tool_input, result = event
                        preview = _tool_preview(tool_name, tool_input)
                        key = f"{tool_name}({preview})"
                        tname, line = pending_tools.pop(key, (tool_name, f"↳ {key}"))
                        if result.is_error:
                            console.print(f"[dim]{line}[/dim] [red]✗[/red]", highlight=False)
                            console.print(f"  [red]{result.content[:200]}[/red]")
                        else:
                            console.print(f"[dim]{line}[/dim] [green]✓[/green]", highlight=False)

                        if pending_tools:
                            names = [tn for tn, _ in pending_tools.values()]
                            spinner.start(_collapsed_tool_summary(names))
                        else:
                            streaming = False
                            listener.resume()
                            spinner.start("Thinking…")
                            first_text = True

                elif event[0] == "error":
                    if not quiet:
                        md_stream.flush()
                        spinner.stop()
                        console.print(f"\n[bold red]{event[1]}[/bold red]")

            md_stream.flush()
            spinner.stop()
    except (AbortedError, KeyboardInterrupt):
        md_stream.flush()
        spinner.stop()
        if not isinstance(sys.exc_info()[1], AbortedError):
            engine.cancel_turn()
        if not quiet:
            console.print("\n[dim yellow]⏹ Turn cancelled[/dim yellow]")
        return
    finally:
        md_stream.flush()
        spinner.stop()
        if permissions:
            permissions.set_esc_listener(None)

    if not print_mode:
        console.print()


def _run_dream(engine: Engine, memory_dir: Path,
               permissions: PermissionChecker, quiet: bool = False) -> None:
    """Run dream consolidation: snapshot messages, submit dream prompt, restore."""
    if not quiet:
        console.print("[dim]Starting dream consolidation…[/dim]")
    saved_messages = list(engine.messages)
    engine.messages = []
    dream_prompt = build_dream_prompt(memory_dir)
    run_query(engine, dream_prompt, print_mode=False, permissions=permissions, quiet=quiet)
    engine.messages = saved_messages
    # Rebuild system prompt to pick up updated MEMORY.md
    engine.system_prompt = build_system_prompt(memory_dir=memory_dir)
    record_consolidation(memory_dir)
    if not quiet:
        console.print("[dim]Dream consolidation complete. Memory index updated.[/dim]")


def main() -> None:
    parser = argparse.ArgumentParser(prog="cc-mini",
                                     description="Minimal AI coding assistant")
    parser.add_argument("prompt", nargs="?", help="Prompt to send (optional)")
    parser.add_argument("-p", "--print", action="store_true",
                        help="Non-interactive: print response and exit")
    parser.add_argument("--auto-approve", action="store_true",
                        help="Auto-approve all tool permissions (dangerous)")
    parser.add_argument("--config", help="Path to a TOML config file")
    parser.add_argument("--provider", choices=("anthropic", "openai"),
                        help="API provider / wire format")
    parser.add_argument("--api-key", help="API key for the selected provider")
    parser.add_argument("--base-url", help="Custom API base URL for the selected provider")
    parser.add_argument("--model", help="Model name, e.g. claude-sonnet-4")
    parser.add_argument("--max-tokens", type=int,
                        help="Maximum output tokens for each model response")
    parser.add_argument("--effort", choices=("low", "medium", "high"),
                        help="Optional reasoning effort for supported OpenAI models")
    parser.add_argument("--buddy-model",
                        help="Override the model used by buddy / companion side-features")
    parser.add_argument("--resume", metavar="SESSION",
                        help="Resume a previous session (id or index)")
    parser.add_argument("--memory-dir", help="Override memory directory path")
    parser.add_argument("--no-auto-dream", action="store_true",
                        help="Disable automatic dream consolidation")
    parser.add_argument("--dream-interval", type=float,
                        help="Hours between auto-dream runs (default: 24)")
    parser.add_argument("--dream-min-sessions", type=int,
                        help="Minimum new sessions before auto-dream triggers (default: 5)")
    parser.add_argument("--coordinator", action="store_true",
                        help="Enable coordinator mode with background workers")
    args = parser.parse_args()

    try:
        app_config = load_app_config(args)
    except ValueError as exc:
        parser.error(str(exc))

    # Sandbox initialization
    sandbox_config = load_sandbox_config(app_config.config_paths)
    sandbox_mgr = SandboxManager(config=sandbox_config)

    # Memory setup
    memory_dir = app_config.memory_dir
    ensure_memory_dir(memory_dir)
    session_id = datetime.now().strftime("%Y%m%d-%H%M%S")

    # Skill setup — register bundled + discover project/user skills
    register_bundled_skills()
    cwd = str(Path.cwd())
    discover_skills(cwd)
    skills_section = build_skills_prompt_section()

    if args.coordinator:
        set_coordinator_mode(True)

    def _build_base_tools() -> list:
        return [
            FileReadTool(), GlobTool(), GrepTool(),
            FileEditTool(), FileWriteTool(),
            BashTool(sandbox_manager=sandbox_mgr),
        ]

    worker_tool_names = [tool.name for tool in _build_base_tools()]

    def _build_system_prompt_for_mode(coordinator_enabled: bool) -> str:
        prompt = build_system_prompt(cwd=cwd, memory_dir=memory_dir)
        if skills_section:
            prompt += "\n\n" + skills_section
        if coordinator_enabled:
            extra = get_coordinator_user_context(worker_tool_names)
            worker_context = extra.get("workerToolsContext")
            if worker_context:
                prompt += "\n\n# Coordinator Context\n" + worker_context
            prompt += "\n\n" + get_coordinator_system_prompt()
        return prompt

    permissions = PermissionChecker(
        auto_approve=args.auto_approve,
        sandbox_manager=sandbox_mgr,
    )

    def _build_worker_engine() -> Engine:
        worker_permissions = PermissionChecker(
            auto_approve=True,
            sandbox_manager=sandbox_mgr,
        )
        worker_prompt = build_system_prompt(cwd=cwd, memory_dir=memory_dir)
        if skills_section:
            worker_prompt += "\n\n" + skills_section
        worker_prompt += "\n\n" + get_worker_system_prompt()
        return Engine(
            tools=_build_base_tools(),
            system_prompt=worker_prompt,
            permission_checker=worker_permissions,
            provider=app_config.provider,
            api_key=app_config.api_key,
            base_url=app_config.base_url,
            model=app_config.model,
            max_tokens=app_config.max_tokens,
            effort=app_config.effort,
        )

    worker_manager = WorkerManager(build_worker_engine=_build_worker_engine)

    # Plan mode manager
    from .plan import PlanModeManager
    from .tools.plan_tools import EnterPlanModeTool, ExitPlanModeTool
    plan_manager = PlanModeManager()

    def _build_tools_for_mode(coordinator_enabled: bool) -> list:
        tools = _build_base_tools()
        tools.append(AskUserQuestionTool())
        tools.extend([
            EnterPlanModeTool(plan_manager),
            ExitPlanModeTool(plan_manager),
        ])
        if coordinator_enabled:
            tools.extend([
                AgentTool(worker_manager),
                SendMessageTool(worker_manager),
                TaskStopTool(worker_manager),
            ])
        return tools

    coordinator_enabled = is_coordinator_mode()

    # Session & compact services
    cost_tracker = CostTracker()
    session_store: SessionStore | None = None
    if not args.print:
        session_store = SessionStore(
            cwd=cwd,
            model=app_config.model,
            mode=current_session_mode(),
        )

    engine = Engine(
        tools=_build_tools_for_mode(coordinator_enabled),
        system_prompt=_build_system_prompt_for_mode(coordinator_enabled),
        permission_checker=permissions,
        provider=app_config.provider,
        api_key=app_config.api_key,
        base_url=app_config.base_url,
        model=app_config.model,
        max_tokens=app_config.max_tokens,
        effort=app_config.effort,
        session_store=session_store,
        cost_tracker=cost_tracker,
    )
    plan_manager.bind_engine(engine)
    permissions.set_plan_manager(plan_manager)
    compact_service = CompactService(
        client=engine._client,
        model=app_config.model,
        effort=app_config.effort,
    )

    def _apply_session_mode(session_mode: str | None) -> str | None:
        warning = match_session_mode(session_mode)
        enabled = is_coordinator_mode()
        engine.set_tools(_build_tools_for_mode(enabled))
        engine.system_prompt = _build_system_prompt_for_mode(enabled)
        if session_store is not None:
            session_store.mode = current_session_mode()
        return warning

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
            meta, msgs = SessionStore.load_session(target.session_id, cwd)
            if msgs:
                warning = _apply_session_mode(meta.mode if meta is not None else None)
                engine.set_messages(msgs)
                session_store = SessionStore(
                    cwd=cwd,
                    model=app_config.model,
                    session_id=target.session_id,
                    mode=current_session_mode(),
                )
                engine.set_session_store(session_store)
                console.print(f"[green]✓[/green] Resumed: {target.title[:50]}  "
                              f"({len(msgs)} messages)")
                if warning:
                    console.print(f"[yellow]{warning}[/yellow]")
        else:
            console.print(f"[red]Session not found: {args.resume}[/red]")

    # Non-interactive / piped
    if args.print or args.prompt:
        prompt_text = args.prompt or sys.stdin.read()
        run_query(engine, _parse_input(prompt_text), print_mode=args.print, permissions=permissions)
        if worker_manager.has_running_tasks():
            console.print(
                "\n[dim]Background workers are still running. Use interactive mode "
                "to receive coordinator task notifications.[/dim]"
            )
        if cost_tracker.total_cost_usd > 0:
            console.print(f"\n[dim]{cost_tracker.format_cost()}[/dim]")
        return

    # Interactive REPL
    config_note = (
        f"[dim]{app_config.provider}:{app_config.model} · "
        f"max_tokens={app_config.max_tokens}[/dim]"
    )
    if is_coordinator_mode():
        config_note += " [dim yellow]· coordinator[/dim yellow]"
    session_note = f"[dim]session {session_store.session_id[:8]}[/dim]" if session_store else ""
    console.print("[bold cyan]cc-mini[/bold cyan]  "
                  f"{config_note}  {session_note}")
    console.print('[dim]Esc or Ctrl+C to cancel, Ctrl+C twice to exit[/dim]')

    _file_history = FileHistory(str(_HISTORY_FILE))

    # Track last Ctrl+C time for double-press exit (matches useDoublePress)
    last_ctrlc_time = 0.0

    # Terminal mode state — shared mutable ref toggled by "!" key binding
    _terminal_mode_ref = [False]

    def _run_shell(cmd: str) -> None:
        """Execute a shell command and print output."""
        import subprocess
        console.print(f"[dim]$ {cmd}[/dim]")
        try:
            result = subprocess.run(
                cmd, shell=True, text=True, encoding="utf-8", errors="replace",
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            if result.stdout:
                console.print(result.stdout, end="", markup=False)
            if result.returncode != 0:
                console.print(f"[red][exit {result.returncode}][/red]")
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/red]")

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

    _exiting = False

    def _drain_worker_notifications() -> None:
        if not is_coordinator_mode() or _exiting:
            return
        while True:
            notifications = worker_manager.drain_notifications()
            if not notifications:
                return
            for notification in notifications:
                # Extract summary info from XML notification
                import re as _re
                _desc = _re.search(r"<summary>(.*?)</summary>", notification)
                _uses = _re.search(r"<tool_uses>(\d+)</tool_uses>", notification)
                _dur = _re.search(r"<duration_ms>(\d+)</duration_ms>", notification)
                _status = _re.search(r"<status>(.*?)</status>", notification)
                desc = _desc.group(1) if _desc else "Worker update"
                uses = _uses.group(1) if _uses else "?"
                dur_s = f"{int(_dur.group(1)) / 1000:.1f}" if _dur else "?"
                status = _status.group(1) if _status else "completed"
                icon = "[green]●[/green]" if status == "completed" else "[red]●[/red]"
                console.print(f"\n{icon} [dim]{desc} ({uses} tool uses, {dur_s}s)[/dim]")
                try:
                    run_query(engine, notification, print_mode=False, permissions=permissions)
                except (KeyboardInterrupt, Exception):
                    return

    def _show_worker_status() -> None:
        """Show running worker status before prompt."""
        if not is_coordinator_mode():
            return
        statuses = worker_manager.get_running_status()
        if statuses:
            for s in statuses:
                uses = s["tool_uses"]
                activity = s["activity"] or "working"
                console.print(
                    f"[dim]  ● {s['description']} — "
                    f"{uses} tool use{'s' if uses != 1 else ''} · {activity}[/dim]"
                )

    while True:
        _drain_worker_notifications()
        _show_worker_status()

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
            console.print()
            _terminal_mode_ref[0] = False  # always start in chat mode
            user_input = _bordered_prompt(
                console,
                history=_file_history,
                completer=_slash_completer,
                animator_toolbar=animator.toolbar_text if animator else None,
                refresh_interval=0.5 if animator else None,
                terminal_mode_ref=_terminal_mode_ref,
            ).strip()
        except KeyboardInterrupt:
            now = time.monotonic()
            if now - last_ctrlc_time <= _DOUBLE_PRESS_TIMEOUT_MS:
                _exiting = True
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

        # ---------------------------------------------------------------------------
        # Terminal mode — "!" key toggles mode in-place (no submit needed).
        # In terminal mode every submitted input is a shell command.
        # Outside terminal mode "!cmd" runs a one-off shell command.
        # ---------------------------------------------------------------------------
        if _terminal_mode_ref[0]:
            _run_shell(user_input)
            continue

        if user_input.startswith("!") and len(user_input) > 1:
            _run_shell(user_input[1:].lstrip())
            continue
        if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
            console.print("[dim]Goodbye.[/dim]")
            break
        if user_input.startswith("/sandbox"):
            _handle_sandbox_command(user_input, sandbox_mgr, console)
            continue

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
                handle_buddy_command(
                    cmd_args,
                    engine._client,
                    console,
                    app_config.buddy_model or app_config.model,
                )
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
                cost_tracker=cost_tracker,
                new_session_store=lambda: SessionStore(
                    cwd=cwd,
                    model=app_config.model,
                    mode=current_session_mode(),
                ),
                reconfigure_mode=_apply_session_mode,
                plan_manager=plan_manager,
            )
            handle_command(cmd_name, cmd_args, cmd_ctx)
            session_store = cmd_ctx.session_store
            # If the command set a pending query (e.g. /plan <description>),
            # submit it to the model instead of continuing to the next prompt.
            if cmd_ctx.pending_query:
                user_input = cmd_ctx.pending_query
                cmd_ctx.pending_query = None
                # Fall through to normal query processing below
            else:
                continue

        # Auto-compact when approaching token limits
        if should_compact(engine.get_messages(), model=app_config.model,
                          last_input_tokens=cost_tracker.last_input_tokens):
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
                        model=app_config.buddy_model or app_config.model,
                        user_msg=user_input,
                    )
                    reply_event.wait(timeout=10)
        except Exception:
            pass

        if _companion_addressed:
            continue

        run_query(engine, _parse_input(user_input), print_mode=False, permissions=permissions)
        _drain_worker_notifications()

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
                            # Update companion mood based on this turn
                            try:
                                import time as _time
                                from .buddy.mood import classify_events, apply_events, apply_decay
                                from .buddy.storage import load_active_mood, save_active_mood
                                now_ms = int(_time.time() * 1000)
                                current_mood = load_active_mood()
                                current_mood = apply_decay(current_mood, now_ms)
                                events = classify_events(assistant_text, user_input)
                                if events:
                                    current_mood = apply_events(current_mood, events)
                                save_active_mood(current_mood)
                                # Refresh companion with updated mood
                                comp = get_companion()
                                if animator and comp:
                                    animator.update_companion(comp)
                            except Exception:
                                pass
                            fire_companion_observer(
                                assistant_text, comp, engine._client, _set_reaction,
                                model=app_config.buddy_model or app_config.model,
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
                console.print("[dim]Auto-dream running…[/dim]")
                _run_dream(engine, memory_dir, permissions, quiet=True)
                release_lock(memory_dir)

    # Print cost summary on exit
    if cost_tracker.total_cost_usd > 0:
        console.print(f"\n[dim]{cost_tracker.format_cost()}[/dim]")


def _handle_sandbox_command(
    user_input: str, mgr: SandboxManager, con: Console
) -> None:
    """Handle /sandbox REPL command.

    Corresponds to commands/sandbox-toggle/sandbox-toggle.tsx.

    Sub-commands:
    - /sandbox           -- interactive setup
    - /sandbox status    -- show current status
    - /sandbox exclude <pattern> -- add excluded command
    - /sandbox mode <auto-allow|regular|disabled> -- set mode
    """
    parts = user_input.strip().split(maxsplit=2)
    subcmd = parts[1] if len(parts) > 1 else ""

    if subcmd == "status" or subcmd == "":
        _show_sandbox_status(mgr, con)
    elif subcmd == "exclude" and len(parts) > 2:
        pattern = parts[2].strip("\"'")
        msg = mgr.add_excluded_command(pattern)
        mgr.save()
        con.print(f"[green]{msg}[/green]")
    elif subcmd == "mode" and len(parts) > 2:
        msg = mgr.set_mode(parts[2])
        mgr.save()
        con.print(f"[green]{msg}[/green]")
    else:
        _interactive_sandbox_setup(mgr, con)


def _show_sandbox_status(mgr: SandboxManager, con: Console) -> None:
    """Display sandbox status. Corresponds to SandboxConfigTab + SandboxDependenciesTab."""
    dep = mgr.check_dependencies()
    mode = (
        "auto-allow"
        if mgr.is_auto_allow()
        else ("regular" if mgr.config.enabled else "disabled")
    )
    con.print("[bold]Sandbox Status[/bold]")
    con.print(f"  Mode: [cyan]{mode}[/cyan]")
    con.print(f"  Enabled: {'yes' if mgr.is_enabled() else 'no'}")
    con.print(
        f"  Network isolation: {'yes' if mgr.config.unshare_net else 'no'}"
    )
    if dep.errors:
        con.print("[bold red]Dependency errors:[/bold red]")
        for e in dep.errors:
            con.print(f"  [red]{e}[/red]")
    if dep.warnings:
        for w in dep.warnings:
            con.print(f"  [yellow]{w}[/yellow]")
    if mgr.config.excluded_commands:
        con.print("[bold]Excluded commands:[/bold]")
        for cmd in mgr.config.excluded_commands:
            con.print(f"  - {cmd}")


def _interactive_sandbox_setup(mgr: SandboxManager, con: Console) -> None:
    """Interactive three-way mode selection. Corresponds to SandboxModeTab Select."""
    dep = mgr.check_dependencies()
    if dep.errors:
        con.print("[bold red]Cannot enable sandbox:[/bold red]")
        for e in dep.errors:
            con.print(f"  [red]{e}[/red]")
        return

    con.print("[bold]Configure sandbox mode:[/bold]")
    con.print("  [1] auto-allow -- bash commands auto-approved in sandbox")
    con.print("  [2] regular    -- bash commands still need confirmation")
    con.print("  [3] disabled   -- no sandbox")
    choice = input("  Select [1/2/3]: ").strip()
    mode_map = {"1": "auto-allow", "2": "regular", "3": "disabled"}
    mode = mode_map.get(choice)
    if mode:
        msg = mgr.set_mode(mode)
        mgr.save()
        con.print(f"[green]{msg}[/green]")
    else:
        con.print("[dim]Cancelled[/dim]")


if __name__ == "__main__":
    main()
