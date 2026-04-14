"""AskUserQuestion tool — lets the model ask the user multiple-choice questions.

Ported from claude-code-main's ``AskUserQuestionTool.tsx`` +
``QuestionView.tsx`` + ``use-select-input.ts``.

Uses prompt_toolkit for proper terminal handling (avoids EscListener cbreak
conflicts) and provides an arrow-key navigable menu with inline text input
for the "Other" option — matching the official behavior:

- Other is an inline text input, not a separate prompt
- Arrow up/down navigate even while in Other's text input
- Regular characters type into the Other buffer when focused
- Number keys: on regular options → quick-select; on Other → type into buffer
- Enter on regular option → immediate select; on Other → submit typed text
- Esc on Other with text → clear text, move up; without text → cancel
"""

from __future__ import annotations

from core.tool import Tool, ToolResult

# Internal sentinel — never surfaced to the model.
_OTHER = "__other__"


# ---------------------------------------------------------------------------
# Interactive selector built on prompt_toolkit
# ---------------------------------------------------------------------------

def _select_one(question: str, labels: list[str], descriptions: list[str]) -> str | None:
    """Arrow-key navigable single-select menu matching official Claude Code.

    The last option is always "Other" which shows an inline text input when
    focused.  Returns the selected label, the typed text (for Other), or
    ``None`` on cancellation.
    """
    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    other_idx = len(labels) - 1  # last item is always Other
    cursor = [0]
    text_buf: list[str] = [""]   # mutable buffer for Other text
    result: list[str] = []

    kb = KeyBindings()

    def _on_other() -> bool:
        return cursor[0] == other_idx

    # --- Arrow navigation (always works, even in Other input) ---------------

    @kb.add("up")
    def _up(event):
        cursor[0] = (cursor[0] - 1) % len(labels)

    @kb.add("down")
    def _down(event):
        cursor[0] = (cursor[0] + 1) % len(labels)

    # --- Enter: select regular option or submit Other text ------------------

    @kb.add("enter")
    def _enter(event):
        if _on_other():
            if text_buf[0]:
                result.append(text_buf[0])
            else:
                result.append(_OTHER)  # empty Other = cancelled
        else:
            result.append(labels[cursor[0]])
        event.app.exit()

    # --- Esc / Ctrl-C -------------------------------------------------------

    @kb.add("c-c")
    def _cancel(event):
        event.app.exit()

    @kb.add("escape")
    def _esc(event):
        if _on_other() and text_buf[0]:
            # Clear text and move cursor up (matches official: Esc exits input)
            text_buf[0] = ""
            cursor[0] = max(other_idx - 1, 0)
        else:
            event.app.exit()

    # --- Backspace in Other -------------------------------------------------

    @kb.add("backspace")
    def _bs(event):
        if _on_other():
            text_buf[0] = text_buf[0][:-1]

    # --- Printable characters -----------------------------------------------
    # On regular option: number keys quick-select; other chars jump to Other.
    # On Other: all printable chars type into the buffer.

    @kb.add("<any>")
    def _char(event):
        ch = event.data
        if not ch or not ch.isprintable():
            return

        if _on_other():
            # Typing into Other's text input
            text_buf[0] += ch
            return

        # Not on Other — check for number quick-select
        if ch.isdigit():
            idx = int(ch) - 1
            if 0 <= idx < len(labels):
                if idx == other_idx:
                    # Number key on Other: just focus it (matches official)
                    cursor[0] = other_idx
                else:
                    # Number key on regular option: immediate select
                    result.append(labels[idx])
                    event.app.exit()
            return

        # Non-digit char on regular option → jump to Other and start typing
        cursor[0] = other_idx
        text_buf[0] += ch

    # --- Rendering ----------------------------------------------------------

    def _get_tokens():
        tokens = [("bold", f"? {question}\n")]
        for i, (label, desc) in enumerate(zip(labels, descriptions)):
            is_cur = i == cursor[0]
            prefix = "  ❯ " if is_cur else "    "
            style = "ansibrightcyan" if is_cur else ""

            if i == other_idx:
                # "Other" row — inline text input (matches official)
                if text_buf[0]:
                    tokens.append((style, f"{prefix}{i+1}) "))
                    tokens.append(("ansibrightgreen bold", text_buf[0]))
                    if is_cur:
                        tokens.append(("ansigray", "█"))
                elif is_cur:
                    tokens.append((style, f"{prefix}{i+1}) "))
                    tokens.append(("ansigray", "Type something."))
                else:
                    tokens.append(("ansigray" if not is_cur else style, f"{prefix}{i+1}) {label}"))
            else:
                tokens.append((style, f"{prefix}{i+1}) {label}"))
                if desc:
                    tokens.append(("ansigray", f" — {desc}"))
            tokens.append(("", "\n"))

        # Hint bar
        if _on_other() and text_buf[0]:
            tokens.append(("ansigray", "  ↵ submit · esc clear"))
        else:
            tokens.append(("ansigray", "  ↑↓ navigate · ↵ select"))
        return tokens

    control = FormattedTextControl(_get_tokens)
    app: Application = Application(
        layout=Layout(Window(control)),
        key_bindings=kb,
        full_screen=False,
    )

    try:
        app.run()
    except (EOFError, KeyboardInterrupt):
        return None

    if not result:
        return None
    return result[0] if result[0] != _OTHER else None


def _select_multi(question: str, labels: list[str], descriptions: list[str]) -> list[str] | None:
    """Arrow-key navigable multi-select menu with inline Other text input.

    Space to toggle, Enter to confirm.  Matches official behavior:
    arrow keys always navigate, typing on Other goes into text buffer,
    space toggles checkmark.
    """
    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    other_idx = len(labels) - 1
    cursor = [0]
    checked: set[int] = set()
    text_buf: list[str] = [""]
    confirmed = [False]

    kb = KeyBindings()

    def _on_other() -> bool:
        return cursor[0] == other_idx

    @kb.add("up")
    def _up(event):
        cursor[0] = (cursor[0] - 1) % len(labels)

    @kb.add("down")
    def _down(event):
        cursor[0] = (cursor[0] + 1) % len(labels)

    @kb.add("space")
    def _toggle(event):
        if _on_other():
            # Space on Other → type space into buffer
            text_buf[0] += " "
            checked.add(other_idx)
            return
        idx = cursor[0]
        if idx in checked:
            checked.discard(idx)
        else:
            checked.add(idx)

    @kb.add("enter")
    def _confirm(event):
        confirmed[0] = True
        event.app.exit()

    @kb.add("c-c")
    def _cancel_cc(event):
        event.app.exit()

    @kb.add("escape")
    def _esc(event):
        if _on_other() and text_buf[0]:
            text_buf[0] = ""
            checked.discard(other_idx)
            cursor[0] = max(other_idx - 1, 0)
        else:
            event.app.exit()

    @kb.add("backspace")
    def _bs(event):
        if _on_other():
            text_buf[0] = text_buf[0][:-1]
            if not text_buf[0]:
                checked.discard(other_idx)

    @kb.add("<any>")
    def _char(event):
        ch = event.data
        if not ch or not ch.isprintable():
            return
        if _on_other():
            text_buf[0] += ch
            checked.add(other_idx)
            return
        # Non-Other: number key focuses option, other chars jump to Other
        if ch.isdigit():
            idx = int(ch) - 1
            if 0 <= idx < len(labels):
                cursor[0] = idx
            return
        cursor[0] = other_idx
        text_buf[0] += ch
        checked.add(other_idx)

    def _get_tokens():
        tokens = [("bold", f"? {question}\n")]
        for i, (label, desc) in enumerate(zip(labels, descriptions)):
            is_cur = i == cursor[0]
            mark = "✓" if i in checked else " "
            prefix = "  ❯ " if is_cur else "    "
            style = "ansibrightcyan" if is_cur else ""

            if i == other_idx:
                if text_buf[0]:
                    tokens.append((style, f"{prefix}[{mark}] {i+1}) "))
                    tokens.append(("ansibrightgreen bold", text_buf[0]))
                    if is_cur:
                        tokens.append(("ansigray", "█"))
                elif is_cur:
                    tokens.append((style, f"{prefix}[{mark}] {i+1}) "))
                    tokens.append(("ansigray", "Type something"))
                else:
                    tokens.append(("ansigray" if not is_cur else style, f"{prefix}[{mark}] {i+1}) {label}"))
            else:
                tokens.append((style, f"{prefix}[{mark}] {i+1}) {label}"))
                if desc:
                    tokens.append(("ansigray", f" — {desc}"))
            tokens.append(("", "\n"))

        tokens.append(("ansigray", "  ↑↓ navigate · space toggle · ↵ submit"))
        return tokens

    control = FormattedTextControl(_get_tokens)
    app: Application = Application(
        layout=Layout(Window(control)),
        key_bindings=kb,
        full_screen=False,
    )

    try:
        app.run()
    except (EOFError, KeyboardInterrupt):
        return None

    if not confirmed[0]:
        return None

    results: list[str] = []
    for i in sorted(checked):
        if i == other_idx:
            if text_buf[0]:
                results.append(text_buf[0])
        else:
            results.append(labels[i])
    return results


# ---------------------------------------------------------------------------
# Tool class
# ---------------------------------------------------------------------------

class AskUserQuestionTool(Tool):
    @property
    def name(self) -> str:
        return "AskUserQuestion"

    @property
    def description(self) -> str:
        return (
            "Use this tool when you need to ask the user questions during execution. "
            "This allows you to:\n"
            "1. Gather user preferences or requirements\n"
            "2. Clarify ambiguous instructions\n"
            "3. Get decisions on implementation choices as you work\n"
            "4. Offer choices to the user about what direction to take.\n\n"
            "Usage notes:\n"
            "- Users will always be able to select \"Other\" to provide custom text input\n"
            "- Use multiSelect: true to allow multiple answers to be selected for a question\n"
            "- If you recommend a specific option, make that the first option in the list "
            "and add \"(Recommended)\" at the end of the label\n\n"
            "Plan mode note: In plan mode, use this tool to clarify requirements or choose "
            "between approaches BEFORE finalizing your plan. Do NOT use this tool to ask "
            "\"Is my plan ready?\" or \"Should I proceed?\" - use ExitPlanMode for plan "
            "approval. Do not reference \"the plan\" in your questions because the user "
            "cannot see the plan until you call ExitPlanMode."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string"},
                            "options": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "label": {"type": "string"},
                                        "description": {"type": "string"},
                                    },
                                    "required": ["label", "description"],
                                },
                                "minItems": 2,
                                "maxItems": 4,
                            },
                            "multiSelect": {"type": "boolean", "default": False},
                        },
                        "required": ["question", "options"],
                    },
                    "minItems": 1,
                    "maxItems": 4,
                }
            },
            "required": ["questions"],
        }

    def is_read_only(self) -> bool:
        return True

    def execute(self, **kwargs) -> ToolResult:
        questions = kwargs.get("questions", [])
        if not questions:
            return ToolResult(content="No questions provided.", is_error=True)

        answers: list[str] = []

        for q in questions:
            question_text = q.get("question", "")
            options = q.get("options", [])
            multi = q.get("multiSelect", False)

            # Build label/description lists — append "Other" (input option, no description)
            labels = [o["label"] for o in options] + ["Other"]
            descs = [o.get("description", "") for o in options] + [""]

            if multi:
                selected = _select_multi(question_text, labels, descs)
                if selected is None:
                    return ToolResult(content="User cancelled the question.", is_error=True)
                answer = ", ".join(selected) if selected else "(no selection)"
            else:
                chosen = _select_one(question_text, labels, descs)
                if chosen is None:
                    return ToolResult(content="User cancelled the question.", is_error=True)
                answer = chosen

            answers.append(f"{question_text} => {answer}")

        result_text = "User answered:\n" + "\n".join(answers)
        return ToolResult(content=result_text)
