"""Mood engine — rule-based mood updates for companion.

Pure functions, no IO, no LLM calls.
"""
from __future__ import annotations

import re

from .types import CompanionMood, MOOD_DIMENSIONS, MOOD_NEUTRAL

# ---------------------------------------------------------------------------
# Event classification (keyword matching)
# ---------------------------------------------------------------------------

_PATTERNS: dict[str, re.Pattern[str]] = {
    'task_success': re.compile(
        r'\b(done|complete[d]?|success|fixed|resolved|implemented|created|passed)\b',
        re.IGNORECASE,
    ),
    'error': re.compile(
        r'\b(error|failed|traceback|exception|bug|broken)\b',
        re.IGNORECASE,
    ),
    'exploration': re.compile(
        r'\b(reading|searching|found\s+\d+\s+files?|glob|grep)\b',
        re.IGNORECASE,
    ),
}


def classify_events(assistant_text: str, user_text: str) -> list[str]:
    """Classify conversation turn into mood-affecting events."""
    events: list[str] = []
    combined = assistant_text + ' ' + user_text
    for tag, pattern in _PATTERNS.items():
        if pattern.search(combined):
            events.append(tag)
    if len(assistant_text) > 2000:
        events.append('long_text')
    return events


# ---------------------------------------------------------------------------
# Event deltas
# ---------------------------------------------------------------------------

#                    happy  bored  excited  tired  grumpy  curious
_DELTAS: dict[str, tuple[int, ...]] = {
    'task_success': (  8,    -5,     5,      0,    -5,      0),
    'error':        ( -5,     0,     0,      3,    10,      0),
    'exploration':  (  0,    -5,     3,      0,     0,     10),
    'pet':          ( 15,   -10,    10,      0,   -10,      0),
    'long_text':    (  0,     0,     0,      5,     0,      0),
}


def _clamp(v: int) -> int:
    return max(0, min(100, v))


def apply_events(mood: CompanionMood, events: list[str]) -> CompanionMood:
    """Apply event deltas to mood, return new CompanionMood."""
    values = {dim: getattr(mood, dim) for dim in MOOD_DIMENSIONS}
    for event in events:
        deltas = _DELTAS.get(event)
        if not deltas:
            continue
        for dim, delta in zip(MOOD_DIMENSIONS, deltas):
            values[dim] += delta
    for dim in MOOD_DIMENSIONS:
        values[dim] = _clamp(values[dim])
    return CompanionMood(**values, last_updated=mood.last_updated)


# ---------------------------------------------------------------------------
# Time-based decay
# ---------------------------------------------------------------------------


def apply_decay(mood: CompanionMood, now_ms: int) -> CompanionMood:
    """Decay all mood dimensions toward neutral over elapsed time.

    Each elapsed minute moves each dimension 1 point toward MOOD_NEUTRAL.
    Bored drifts +1 per 5 minutes of idle time.
    If last_updated is 0 (first run), just sets the timestamp.
    """
    if mood.last_updated == 0:
        return CompanionMood(
            happy=mood.happy, bored=mood.bored, excited=mood.excited,
            tired=mood.tired, grumpy=mood.grumpy, curious=mood.curious,
            last_updated=now_ms,
        )

    elapsed_min = max(0, (now_ms - mood.last_updated)) // 60_000
    if elapsed_min == 0:
        return mood

    values: dict[str, int] = {}
    for dim in MOOD_DIMENSIONS:
        val = getattr(mood, dim)
        if val > MOOD_NEUTRAL:
            val = max(MOOD_NEUTRAL, val - elapsed_min)
        elif val < MOOD_NEUTRAL:
            val = min(MOOD_NEUTRAL, val + elapsed_min)
        values[dim] = val

    # Bored drifts up during idle
    bored_drift = elapsed_min // 5
    values['bored'] = _clamp(values['bored'] + bored_drift)

    return CompanionMood(**values, last_updated=now_ms)


# ---------------------------------------------------------------------------
# Mood description for system prompt
# ---------------------------------------------------------------------------

def _level(val: int) -> str:
    if val < 20:
        return 'very low'
    if val < 40:
        return 'low'
    if val < 60:
        return 'neutral'
    if val < 80:
        return 'high'
    return 'very high'


def describe_mood(mood: CompanionMood) -> str:
    """Generate mood description for injection into observer system prompt."""
    parts = ', '.join(
        f'{dim}={getattr(mood, dim)} ({_level(getattr(mood, dim))})'
        for dim in MOOD_DIMENSIONS
    )
    dominant = mood.dominant()
    return (
        f'Current mood: {parts}.\n'
        f'Dominant mood: {dominant.upper()}.\n\n'
        f'How mood affects your behavior:\n'
        f'- When HAPPY is high: cheerful, encouraging, celebratory\n'
        f'- When GRUMPY is high: short-tempered, complains more\n'
        f'- When TIRED is high: yawns, shorter responses, sleepy tone\n'
        f'- When BORED is high: distracted, suggests doing something different\n'
        f'- When EXCITED is high: energetic, uses exclamation marks\n'
        f'- When CURIOUS is high: asks questions, fascinated by details'
    )
