"""Bordered input prompt — matches claude-code-main PromptInput.tsx.

Uses a custom prompt_toolkit Application so the bottom border sits
directly below the input content (not at the screen bottom).
"""
from __future__ import annotations

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


class SlashCommandCompleter(Completer):
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
        from commands import _COMMAND_TABLE
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
            from features.skills import list_skills
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


slash_completer = SlashCommandCompleter()


def bordered_prompt(
    con: Console,
    history: FileHistory | None = None,
    completer: Completer | None = None,
    animator_toolbar=None,
    refresh_interval: float | None = None,
    terminal_mode_ref: list | None = None,
    mode_hint: str | None = None,
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
        if mode_hint == "swarm":
            return [('bold fg:ansimagenta', f'\u256d{fill}')]
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
        elif mode_hint == "swarm":
            hints = "\u2500 Enter send \u00b7 / commands \u00b7 ! shell \u00b7 \u25c8 swarm "
            fill = "\u2500" * max(0, w - 1 - len(hints))
            parts: list[tuple[str, str]] = [('fg:ansimagenta', f'\u2570{hints}{fill}')]
        elif mode_hint == "coordinator":
            hints = "\u2500 Enter send \u00b7 / commands \u00b7 ! shell \u00b7 coordinator "
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
            if mode_hint == "swarm":
                return [('bold fg:ansimagenta', '\u25c8 ')]
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
