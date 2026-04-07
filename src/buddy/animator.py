"""Real-time companion animation state manager.

Port of the tick-based animation system from CompanionSprite.tsx.

Drives idle animation (7.5s cycle with blink), excited mode (fast frame
cycling when speaking/petting), and speech bubble lifecycle (10s display,
3s fade). A background thread ticks every 500ms and invalidates the
prompt_toolkit app to refresh the bottom_toolbar.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from .sprites import render_face, render_sprite, sprite_frame_count
from .types import (
    RARITY_COLORS,
    RARITY_STARS,
    Companion,
    CompanionBones,
)

# Match CompanionSprite.tsx timing constants
TICK_MS = 500
BUBBLE_SHOW = 20        # ticks (10 seconds)
FADE_WINDOW = 6         # ticks (3 seconds fade)
PET_BURST_MS = 2500     # 2.5 seconds

# Match CompanionSprite.tsx line 23
IDLE_SEQUENCE = [0, 0, 0, 0, 1, 0, 0, 0, -1, 0, 0, 2, 0, 0, 0]

# Heart animation frames — match CompanionSprite.tsx PET_HEARTS
_H = '\u2764'
PET_HEARTS = [
    f'   {_H}    {_H}   ',
    f'  {_H}  {_H}   {_H}  ',
    f' {_H}   {_H}  {_H}   ',
    f'{_H}  {_H}      {_H} ',
    '\u00b7    \u00b7   \u00b7  ',
]


class CompanionAnimator:
    """Manages real-time animation state for the companion sprite.

    Call start() to begin the 500ms tick loop, stop() to halt it.
    The toolbar_text() method returns the current frame as formatted text
    for prompt_toolkit's bottom_toolbar.
    """

    def __init__(self, companion: Companion):
        self.companion = companion
        self._tick = 0
        self._timer: threading.Timer | None = None
        self._running = False
        self._invalidate: Callable[[], None] | None = None

        # Speech bubble state
        self._reaction: str | None = None
        self._reaction_tick: int = 0

        # Pet state
        self._pet_tick: int | None = None

        # Bones for rendering
        self._bones = CompanionBones(
            rarity=companion.rarity,
            species=companion.species,
            eye=companion.eye,
            hat=companion.hat,
            shiny=companion.shiny,
            stats=companion.stats,
        )

    def set_invalidate(self, fn: Callable[[], None]) -> None:
        """Set the app.invalidate callback for toolbar refresh."""
        self._invalidate = fn

    def update_companion(self, companion: Companion) -> None:
        """Refresh the companion reference (e.g. after mood update)."""
        self.companion = companion
        self._bones = CompanionBones(
            rarity=companion.rarity,
            species=companion.species,
            eye=companion.eye,
            hat=companion.hat,
            shiny=companion.shiny,
            stats=companion.stats,
        )

    def start(self) -> None:
        self._running = True
        self._schedule_tick()

    def stop(self) -> None:
        self._running = False
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def set_reaction(self, text: str) -> None:
        """Set a speech bubble reaction (from observer)."""
        self._reaction = text
        self._reaction_tick = self._tick

    def clear_reaction(self) -> None:
        self._reaction = None

    def pet(self) -> None:
        """Trigger the pet/heart animation."""
        self._pet_tick = self._tick

    # -- rendering ----------------------------------------------------------

    def toolbar_text(self) -> list[tuple[str, str]]:
        """Return formatted text tuples for prompt_toolkit bottom_toolbar.

        Returns list of (style, text) tuples.
        """
        comp = self.companion
        color = RARITY_COLORS.get(comp.rarity, 'dim')
        stars = RARITY_STARS.get(comp.rarity, '\u2605')

        # Determine animation state
        petting = (self._pet_tick is not None and
                   (self._tick - self._pet_tick) * TICK_MS < PET_BURST_MS)
        speaking = self._reaction is not None
        excited = petting or speaking

        # Determine sprite frame
        frame_count = sprite_frame_count(comp.species)
        if excited:
            sprite_frame = self._tick % frame_count
            blink = False
        else:
            step = IDLE_SEQUENCE[self._tick % len(IDLE_SEQUENCE)]
            if step == -1:
                sprite_frame = 0
                blink = True
            else:
                sprite_frame = step
                blink = False

        # Render sprite lines
        lines = render_sprite(self._bones, sprite_frame)
        if blink:
            lines = [line.replace(comp.eye, '-') for line in lines]

        # Heart overlay for petting
        heart_line = None
        if petting and self._pet_tick is not None:
            pet_age = self._tick - self._pet_tick
            heart_line = PET_HEARTS[pet_age % len(PET_HEARTS)]

        # Speech bubble
        bubble_lines: list[str] = []
        bubble_fading = False
        if self._reaction:
            age = self._tick - self._reaction_tick
            if age >= BUBBLE_SHOW:
                self._reaction = None
            else:
                bubble_fading = age >= BUBBLE_SHOW - FADE_WINDOW
                bubble_lines = self._wrap_bubble(self._reaction, bubble_fading)

        # Compose output: sprite on left, bubble on right
        result: list[tuple[str, str]] = []

        shiny_tag = ' \u2728' if comp.shiny else ''
        dominant = comp.mood.dominant().lower()
        name_line = f' {comp.name} the {comp.species} {stars}{shiny_tag}  [{dominant}]'

        # Assemble sprite lines (with optional heart on top)
        sprite_lines_full = []
        if heart_line:
            sprite_lines_full.append(heart_line)
        sprite_lines_full.extend(lines)

        max_sw = max((len(l) for l in sprite_lines_full), default=12)

        # Style names
        s_sprite = f'fg:{_rich_to_ansi(color)}'
        s_heart = 'fg:red bold'
        s_bubble = 'fg:gray italic' if bubble_fading else f'fg:{_rich_to_ansi(color)} italic'

        total_rows = max(len(sprite_lines_full), len(bubble_lines))
        for i in range(total_rows):
            # Sprite part
            if i < len(sprite_lines_full):
                sl = sprite_lines_full[i].ljust(max_sw)
                st = s_heart if (heart_line and i == 0) else s_sprite
                result.append((st, sl))
            else:
                result.append(('', ' ' * max_sw))

            # Bubble part
            if i < len(bubble_lines):
                result.append(('', '  '))
                result.append((s_bubble, bubble_lines[i]))

            result.append(('', '\n'))

        # Name line
        result.append((s_sprite, name_line))

        return result

    def _wrap_bubble(self, text: str, fading: bool) -> list[str]:
        """Wrap text into speech bubble lines with border."""
        max_w = 30
        words = text.split()
        wrapped: list[str] = []
        current = ''
        for word in words:
            if current and len(current) + 1 + len(word) > max_w:
                wrapped.append(current)
                current = word
            else:
                current = f'{current} {word}'.strip() if current else word
        if current:
            wrapped.append(current)
        if not wrapped:
            return []

        width = max(len(l) for l in wrapped)
        border = '\u256d' + '\u2500' * (width + 2) + '\u256e'  # round corners
        bottom = '\u2570' + '\u2500' * (width + 2) + '\u256f'
        lines = [border]
        for l in wrapped:
            lines.append(f'\u2502 {l:<{width}} \u2502')
        lines.append(bottom)
        return lines

    def _schedule_tick(self) -> None:
        if not self._running:
            return
        self._tick += 1
        if self._invalidate:
            try:
                self._invalidate()
            except Exception:
                pass
        self._timer = threading.Timer(TICK_MS / 1000, self._schedule_tick)
        self._timer.daemon = True
        self._timer.start()


def _rich_to_ansi(color: str) -> str:
    """Map rich style names to ANSI color names for prompt_toolkit."""
    mapping = {
        'dim': 'gray',
        'green': 'ansigreen',
        'blue': 'ansiblue',
        'magenta': 'ansimagenta',
        'yellow': 'ansiyellow',
        'red': 'ansired',
    }
    return mapping.get(color, color)
