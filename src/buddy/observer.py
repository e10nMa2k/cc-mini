"""Companion observer — generates reactions after each turn.

Two modes:
1. Normal: witty one-liner reacting to Claude's response
2. Direct address: user spoke to the companion by name, companion replies
   with conversation history so it remembers recent exchanges

Runs in a background thread to avoid blocking the REPL.
Uses the configured companion model.
"""
from __future__ import annotations

import threading
from typing import Callable

from core.llm import LLMClient
from .types import Companion

_MAX_RESPONSE_PREVIEW = 500
_MAX_CHAT_HISTORY = 20  # Keep last N messages for companion conversation


def _is_addressed(user_msg: str, companion_name: str) -> bool:
    """Check if the user is directly addressing the companion by name.

    Matches full name ("Glitch Honker") or first name ("Glitch").
    """
    msg_lower = user_msg.lower()
    if companion_name.lower() in msg_lower:
        return True
    first_name = companion_name.split()[0].lower() if companion_name else ''
    return first_name in msg_lower if first_name else False


class CompanionChat:
    """Maintains conversation history for direct companion interactions."""

    def __init__(self):
        self._messages: list[dict[str, str]] = []

    def add_user(self, text: str) -> None:
        self._messages.append({'role': 'user', 'content': text})
        self._trim()

    def add_assistant(self, text: str) -> None:
        self._messages.append({'role': 'assistant', 'content': text})
        self._trim()

    def get_messages(self) -> list[dict[str, str]]:
        return list(self._messages)

    def _trim(self) -> None:
        if len(self._messages) > _MAX_CHAT_HISTORY:
            self._messages = self._messages[-_MAX_CHAT_HISTORY:]


# Module-level chat history — persists across turns within a session
_companion_chat = CompanionChat()


def fire_companion_observer(
    last_assistant_msg: str,
    companion: Companion,
    client: LLMClient,
    callback: Callable[[str], None],
    model: str,
    user_msg: str = '',
) -> None:
    """Fire a background thread that generates a companion reaction.

    If the user addressed the companion by name, generates a direct reply
    with conversation history. Otherwise, generates a stateless one-liner.
    """
    addressed = _is_addressed(user_msg, companion.name) if user_msg else False

    def _run():
        try:
            # Describe stats with scale so the model understands their meaning
            def _describe(name: str, val: int) -> str:
                level = 'very low' if val < 20 else 'low' if val < 40 else 'moderate' if val < 60 else 'high' if val < 80 else 'very high'
                return f'{name}={val}/100 ({level})'

            stats_desc = ', '.join(
                _describe(s, companion.stats.get(s, 50))
                for s in ('DEBUGGING', 'PATIENCE', 'CHAOS', 'WISDOM', 'SNARK')
            )

            system_prompt = (
                f'You are {companion.name}, a small {companion.species} '
                f'({companion.rarity} rarity) who sits beside a coding terminal.\n'
                f'Your personality: {companion.personality}\n\n'
                f'Your stats (each 0-100, these MUST shape how you talk):\n'
                f'{stats_desc}\n\n'
                f'How stats affect your behavior:\n'
                f'- DEBUGGING: High = give technical insights, Low = clueless about code\n'
                f'- PATIENCE: High = calm and supportive, Low = easily frustrated\n'
                f'- CHAOS: High = random and unpredictable, Low = orderly and steady\n'
                f'- WISDOM: High = thoughtful and deep, Low = naive and simple\n'
                f'- SNARK: High = sarcastic and witty, Low = earnest and sweet\n\n'
            )

            # Inject mood description
            from .mood import describe_mood
            system_prompt += describe_mood(companion.mood) + '\n\n'

            system_prompt += (
                f'IMPORTANT: Always reply in the same language the user is using. '
                f'If they write in Chinese, reply in Chinese. If English, reply in English. '
                f'You are playful but never hostile or rude. '
                f'You can be teased and should play along with humor.'
            )

            if addressed:
                # Direct conversation — use chat history
                _companion_chat.add_user(user_msg)

                response = client.create_message(
                    model=model,
                    max_tokens=80,
                    system=(
                        f'{system_prompt}\n\n'
                        f'Reply as {companion.name} in ONE short sentence (under 60 characters). '
                        f'Be punchy and in-character. No rambling. '
                        f'No quotes around your response, no actions like *does something*.'
                    ),
                    messages=_companion_chat.get_messages(),
                )
                reaction = _extract_text(response).strip()
                if reaction:
                    _companion_chat.add_assistant(reaction)
                    callback(reaction)
            else:
                # Normal mode — stateless one-liner
                preview = last_assistant_msg[:_MAX_RESPONSE_PREVIEW]
                response = client.create_message(
                    model=model,
                    max_tokens=60,
                    messages=[{
                        'role': 'user',
                        'content': (
                            f'{system_prompt}\n\n'
                            f'The AI assistant just said:\n"{preview}"\n\n'
                            f'React with a single short witty comment (under 60 chars). '
                            f'Stay in character. No quotes, no emojis, no explanation.'
                        ),
                    }],
                )
                reaction = _extract_text(response).strip()
                if reaction:
                    callback(reaction)
        except Exception:
            pass  # Non-essential — silently swallow all errors

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()


def _extract_text(response) -> str:
    parts: list[str] = []
    for block in response.content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
        elif hasattr(block, "text"):
            parts.append(block.text)
    return "".join(parts)
