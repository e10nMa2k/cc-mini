"""Context compression — summarise old messages to free token budget.

Modelled after claude-code's ``src/services/compact/compact.ts``.
"""

from __future__ import annotations

from typing import Any
from core.llm import LLMClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHARS_PER_TOKEN = 4
COMPACT_THRESHOLD_TOKENS = 100_000  # fallback threshold (no real usage data)
MIN_RECENT_MESSAGES = 6             # always keep at least this many messages
MIN_RECENT_TOKENS = 10_000          # keep at least this many tokens of recent context
COMPACT_MAX_OUTPUT_TOKENS = 4096
AUTOCOMPACT_BUFFER_TOKENS = 13_000  # matches official autoCompact.ts

# Model context windows (tokens).  First match wins.
_CONTEXT_WINDOWS: list[tuple[str, int]] = [
    ("claude-opus-4-6", 1_000_000),
    ("claude-opus-4-5", 1_000_000),
    ("claude-opus-4",   200_000),
    ("claude-sonnet-4-6", 1_000_000),
    ("claude-sonnet-4-5", 1_000_000),
    ("claude-sonnet-4", 200_000),
    ("claude-sonnet",   200_000),
    ("claude-3-7-sonnet", 200_000),
    ("claude-3-5-sonnet", 200_000),
    ("claude-haiku-4-5", 200_000),
    ("claude-3-5-haiku", 200_000),
]
_DEFAULT_CONTEXT_WINDOW = 200_000


def _context_window_for_model(model: str) -> int:
    model_lower = model.lower()
    for prefix, window in _CONTEXT_WINDOWS:
        if prefix in model_lower:
            return window
    return _DEFAULT_CONTEXT_WINDOW


def _auto_compact_threshold(model: str) -> int:
    """context_window - max_output_reserve - buffer (matches official)."""
    cw = _context_window_for_model(model)
    max_out_reserve = min(20_000, cw // 5)  # reserve for summary output
    return cw - max_out_reserve - AUTOCOMPACT_BUFFER_TOKENS

COMPACT_PROMPT = """\
Please provide a detailed summary of our conversation so far.  This summary \
will *replace* the earlier messages to free up context space, so it must \
preserve every detail needed to continue the work seamlessly.

Structure your response with these sections:

## Primary Request
What the user is trying to accomplish overall.

## Key Technical Concepts
Important technical details, patterns, frameworks, or constraints established.

## Files and Code
Key files discussed or modified, with brief notes on what was done to each.

## Errors and Fixes
Any errors encountered and how they were resolved.

## Current Work
What was being worked on most recently and its current status.

## Pending Tasks
Outstanding work items or next steps that have not yet been completed.

Focus on preserving information that will be needed to continue the work.  \
Be specific — include file paths, function names, error messages, and \
concrete decisions rather than vague summaries.\
"""

COMPACT_SYSTEM = "You are a conversation summarizer. Produce a structured, detailed summary following the user's requested format."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _text_of(content: Any) -> str:
    """Extract plain text from message content (str, list of blocks, etc.)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                # text block, tool_result, tool_use, etc.
                parts.append(block.get("text", ""))
                parts.append(block.get("content", "") if isinstance(block.get("content"), str) else "")
                parts.append(str(block.get("input", "")))
            elif hasattr(block, "text"):
                parts.append(getattr(block, "text", ""))
            elif hasattr(block, "input"):
                parts.append(str(getattr(block, "input", "")))
        return " ".join(parts)
    return str(content) if content else ""


def estimate_tokens(messages: list[dict]) -> int:
    """Rough token estimate: total chars / CHARS_PER_TOKEN."""
    total_chars = 0
    for msg in messages:
        total_chars += len(_text_of(msg.get("content", "")))
    return total_chars // CHARS_PER_TOKEN


def should_compact(messages: list[dict], model: str | None = None,
                   last_input_tokens: int | None = None) -> bool:
    """Return True when the conversation should be auto-compacted.

    If *last_input_tokens* (from the API response) is available, use it
    against a model-aware threshold (matches official ``autoCompact.ts``).
    Otherwise fall back to the character-based estimate.
    """
    if last_input_tokens and model:
        return last_input_tokens >= _auto_compact_threshold(model)
    return estimate_tokens(messages) > COMPACT_THRESHOLD_TOKENS


# ---------------------------------------------------------------------------
# Message splitting
# ---------------------------------------------------------------------------

def _split_recent(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split *messages* into (history_to_summarise, recent_to_keep).

    Walks backwards, accumulating recent messages until we meet both
    ``MIN_RECENT_MESSAGES`` and ``MIN_RECENT_TOKENS``.  Never splits a
    tool_use / tool_result pair.
    """
    if len(messages) <= MIN_RECENT_MESSAGES:
        return [], list(messages)

    keep_start = len(messages)
    kept_tokens = 0
    kept_msgs = 0

    for i in range(len(messages) - 1, -1, -1):
        kept_tokens += len(_text_of(messages[i].get("content", ""))) // CHARS_PER_TOKEN
        kept_msgs += 1
        keep_start = i

        if kept_msgs >= MIN_RECENT_MESSAGES and kept_tokens >= MIN_RECENT_TOKENS:
            break

    # Don't split tool_use from its tool_result.
    # If keep_start lands on a user message that is purely tool_results,
    # include the preceding assistant message (which contains the tool_use).
    if keep_start > 0:
        msg = messages[keep_start]
        content = msg.get("content", "")
        if (msg.get("role") == "user"
                and isinstance(content, list)
                and all(isinstance(b, dict) and b.get("type") == "tool_result"
                        for b in content)):
            keep_start -= 1

    history = messages[:keep_start]
    recent = messages[keep_start:]
    return history, recent


# ---------------------------------------------------------------------------
# Compact service
# ---------------------------------------------------------------------------

class CompactService:
    """Compress conversation context via API summarisation."""

    def __init__(self, client: LLMClient, model: str, effort: str | None = None):
        self._client = client
        self._model = model
        self._effort = effort

    def compact(
        self,
        messages: list[dict],
        system_prompt: str,
        custom_instructions: str = "",
    ) -> tuple[list[dict], str]:
        """Summarise *messages* and return ``(new_messages, summary_text)``.

        The returned message list has the structure::

            [user: summary] [assistant: ack] [recent messages …]
        """
        history, recent = _split_recent(messages)

        if not history:
            return list(messages), "(nothing to compact)"

        # Build the compact request
        prompt = COMPACT_PROMPT
        if custom_instructions:
            prompt += f"\n\nAdditional instructions: {custom_instructions}"

        # Strip images/documents from history to save tokens
        cleaned = _strip_media(history)
        cleaned.append({"role": "user", "content": prompt})

        # Ensure message list starts with user role
        if cleaned and cleaned[0].get("role") != "user":
            cleaned.insert(0, {"role": "user", "content": "(conversation start)"})

        # Ensure alternating roles
        cleaned = _fix_alternation(cleaned)

        response = self._client.create_message(
            model=self._model,
            max_tokens=COMPACT_MAX_OUTPUT_TOKENS,
            system=COMPACT_SYSTEM,
            messages=cleaned,
            effort=self._effort,
        )

        summary_text = ""
        for block in response.content:
            if isinstance(block, dict) and block.get("type") == "text":
                summary_text += block.get("text", "")
            elif hasattr(block, "text"):
                summary_text += block.text

        if not summary_text.strip():
            summary_text = "(compact produced empty summary)"

        # Build new message list
        new_messages: list[dict] = [
            {
                "role": "user",
                "content": (
                    "[This is a summary of the conversation so far — "
                    "the original messages have been compacted to save context space.]\n\n"
                    + summary_text
                ),
            },
            {
                "role": "assistant",
                "content": (
                    "Understood. I've reviewed the conversation summary above and I'm "
                    "ready to continue from where we left off. What would you like to "
                    "work on next?"
                ),
            },
        ]
        new_messages.extend(recent)

        return new_messages, summary_text


# ---------------------------------------------------------------------------
# Media stripping
# ---------------------------------------------------------------------------

def _strip_media(messages: list[dict]) -> list[dict]:
    """Return a copy of *messages* with images / documents replaced by text markers."""
    out: list[dict] = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            new_blocks: list[Any] = []
            for block in content:
                if isinstance(block, dict):
                    btype = block.get("type", "")
                    if btype == "image":
                        new_blocks.append({"type": "text", "text": "[image]"})
                    elif btype == "document":
                        new_blocks.append({"type": "text", "text": "[document]"})
                    else:
                        new_blocks.append(block)
                elif hasattr(block, "type"):
                    btype = getattr(block, "type", "")
                    if btype == "image":
                        new_blocks.append({"type": "text", "text": "[image]"})
                    elif btype == "document":
                        new_blocks.append({"type": "text", "text": "[document]"})
                    elif hasattr(block, "model_dump"):
                        new_blocks.append(block.model_dump())
                    else:
                        new_blocks.append(block)
                else:
                    new_blocks.append(block)
            out.append({"role": msg["role"], "content": new_blocks})
        else:
            out.append(dict(msg))
    return out


def _fix_alternation(messages: list[dict]) -> list[dict]:
    """Ensure strict user/assistant alternation required by the API."""
    if not messages:
        return messages
    fixed: list[dict] = [messages[0]]
    for msg in messages[1:]:
        if msg["role"] == fixed[-1]["role"]:
            # Merge into previous message
            prev_content = fixed[-1].get("content", "")
            cur_content = msg.get("content", "")
            if isinstance(prev_content, str) and isinstance(cur_content, str):
                fixed[-1]["content"] = prev_content + "\n" + cur_content
            else:
                # Convert both to list and concatenate
                def _as_list(c: Any) -> list:
                    if isinstance(c, list):
                        return list(c)
                    return [{"type": "text", "text": str(c)}]
                fixed[-1]["content"] = _as_list(prev_content) + _as_list(cur_content)
        else:
            fixed.append(msg)
    return fixed
