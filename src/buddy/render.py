"""Terminal rendering for companion cards and animations using rich.

Port of claude-code-main/src/buddy/CompanionSprite.tsx (adapted from React to rich).
"""
from __future__ import annotations

import time

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from .sprites import render_face, render_sprite
from .types import (
    MOOD_DIMENSIONS,
    RARITY_COLORS,
    RARITY_STARS,
    STAT_NAMES,
    Companion,
    CompanionBones,
    CompanionSoul,
)

from rich.table import Table


def _stat_bar(value: int, width: int = 20) -> str:
    filled = round(value / 100 * width)
    return '\u2588' * filled + '\u2591' * (width - filled)


def render_companion_card(companion: Companion, console: Console) -> None:
    """Display a full companion card with sprite, stats, and info."""
    color = RARITY_COLORS.get(companion.rarity, 'dim')
    stars = RARITY_STARS.get(companion.rarity, '\u2605')
    shiny_tag = ' \u2728 SHINY' if companion.shiny else ''

    sprite_lines = render_sprite(
        CompanionBones(
            rarity=companion.rarity,
            species=companion.species,
            eye=companion.eye,
            hat=companion.hat,
            shiny=companion.shiny,
            stats=companion.stats,
        ),
        frame=0,
    )

    # Build card content
    lines: list[str] = []
    lines.append(f'  {companion.name} the {companion.species}{shiny_tag}')
    lines.append(f'  {stars}  ({companion.rarity})')
    lines.append('')

    # Sprite
    for sl in sprite_lines:
        lines.append(f'  {sl}')
    lines.append('')

    # Personality
    lines.append(f'  "{companion.personality}"')
    lines.append('')

    # Stats
    for stat in STAT_NAMES:
        val = companion.stats.get(stat, 0)
        bar = _stat_bar(val)
        lines.append(f'  {stat:<10} {bar} {val:>3}')

    # Mood
    lines.append('')
    lines.append('  Mood:')
    mood = companion.mood
    for dim in MOOD_DIMENSIONS:
        val = getattr(mood, dim)
        bar = _stat_bar(val)
        lines.append(f'  {dim.capitalize():<10} {bar} {val:>3}')
    lines.append(f'  Feeling: {mood.dominant().lower()}')

    # Hatched date
    from datetime import datetime, timezone
    hatched = datetime.fromtimestamp(companion.hatched_at / 1000, tz=timezone.utc)
    lines.append('')
    lines.append(f'  Hatched: {hatched.strftime("%Y-%m-%d")}')

    content = '\n'.join(lines)
    panel = Panel(
        Text.from_ansi(content),
        title=f'[{color}]\u2605 Companion \u2605[/{color}]',
        border_style=color,
        padding=(1, 2),
    )
    console.print(panel)


def render_hatch_animation(
    bones: CompanionBones, soul: CompanionSoul, console: Console
) -> None:
    """Show egg wobble → crack → shatter → reveal animation.

    Higher rarity = longer wobble phase and more dramatic reveal.
    Matches the ceremony feel of the original claude-code-main.
    """
    color = RARITY_COLORS.get(bones.rarity, 'dim')
    stars = RARITY_STARS.get(bones.rarity, '\u2605')

    # Wobble frames — egg rocks side to side
    egg_left = [
        '            ',
        '    .--.    ',
        '   /    \\   ',
        '  |      |  ',
        '   \\    /   ',
        '    `--\u00b4    ',
    ]
    egg_center = [
        '            ',
        '     .--.   ',
        '    /    \\  ',
        '   |      | ',
        '    \\    /  ',
        '     `--\u00b4   ',
    ]
    egg_right = [
        '            ',
        '      .--. ',
        '     /    \\',
        '    |      |',
        '     \\    / ',
        '      `--\u00b4  ',
    ]

    # Crack frames — progressive damage
    crack1 = [
        '            ',
        '     .--.   ',
        '    / *  \\  ',
        '   |      | ',
        '    \\    /  ',
        '     `--\u00b4   ',
    ]
    crack2 = [
        '            ',
        '     .--*   ',
        '    /*   \\  ',
        '   * *  * | ',
        '    \\*  */  ',
        '     *--\u00b4   ',
    ]
    crack3 = [
        '            ',
        '     * --*  ',
        '    *  * \\  ',
        '   * ** * * ',
        '    ** * *  ',
        '     *--*   ',
    ]

    # Rarity determines wobble count: common=2, uncommon=3, rare=4, epic=5, legendary=6
    rarity_wobbles = {'common': 2, 'uncommon': 3, 'rare': 4, 'epic': 5, 'legendary': 6}
    wobble_count = rarity_wobbles.get(bones.rarity, 2)

    sprite = render_sprite(bones)
    shiny_tag = ' \u2728 SHINY!' if bones.shiny else ''

    with Live(console=console, refresh_per_second=8, transient=False) as live:
        # Phase 1: Wobble — egg rocks with increasing speed
        wobble_frames = [egg_center, egg_left, egg_center, egg_right]
        for i in range(wobble_count):
            speed = max(0.15, 0.4 - i * 0.05)  # Gets faster
            for wf in wobble_frames:
                text = Text('\n'.join(f'  {line}' for line in wf), style='dim')
                live.update(text)
                time.sleep(speed)

        # Phase 2: Crack sequence
        for crack, delay in [(crack1, 0.4), (crack2, 0.3), (crack3, 0.2)]:
            text = Text('\n'.join(f'  {line}' for line in crack), style='yellow')
            live.update(text)
            time.sleep(delay)

        # Phase 3: Shatter — brief flash
        shatter = [
            '            ',
            '    * * *   ',
            '   *     *  ',
            '  *  \u2726  \u2726  * ',
            '   *     *  ',
            '    * * *   ',
        ]
        text = Text('\n'.join(f'  {line}' for line in shatter), style='bold yellow')
        live.update(text)
        time.sleep(0.3)

        # Phase 4: Reveal — companion appears
        reveal_lines = [f'  {line}' for line in sprite]
        reveal_lines.append('')
        reveal_lines.append(f'  {soul.name} hatched! {stars}{shiny_tag}')
        reveal_lines.append(f'  {bones.rarity.upper()} {bones.species}')
        reveal_lines.append(f'  "{soul.personality}"')
        text = Text('\n'.join(reveal_lines), style=f'bold {color}')
        live.update(text)
        time.sleep(2.0)

    console.print()  # Clean newline after animation


def render_compact_status(companion: Companion) -> str:
    """One-liner companion status for display before the REPL prompt."""
    face = render_face(
        CompanionBones(
            rarity=companion.rarity,
            species=companion.species,
            eye=companion.eye,
            hat=companion.hat,
            shiny=companion.shiny,
            stats=companion.stats,
        )
    )
    stars = RARITY_STARS.get(companion.rarity, '\u2605')
    shiny = ' \u2728' if companion.shiny else ''
    dominant = companion.mood.dominant().lower()
    return f'  {face} {companion.name} the {companion.species} {stars}{shiny} ({dominant})'


def render_speech_bubble(text: str, color: str = 'dim') -> str:
    """Render a speech bubble with round corners matching CompanionSprite.tsx SpeechBubble.

    Uses Unicode round box-drawing characters for a polished look.
    """
    max_width = 30
    words = text.split()
    lines: list[str] = []
    current = ''
    for word in words:
        if current and len(current) + 1 + len(word) > max_width:
            lines.append(current)
            current = word
        else:
            current = f'{current} {word}'.strip() if current else word
    if current:
        lines.append(current)

    if not lines:
        return ''

    width = max(len(line) for line in lines)
    # Round corners: ╭ ╮ ╰ ╯ matching original's borderStyle="round"
    top = '\u256d' + '\u2500' * (width + 2) + '\u256e'
    bottom = '\u2570' + '\u2500' * (width + 2) + '\u256f'
    body = '\n'.join(f'\u2502 {line:<{width}} \u2502' for line in lines)
    return f'{top}\n{body}\n{bottom}'


def render_speech_bubble_rich(
    text: str, companion: Companion, console: Console, fading: bool = False,
) -> None:
    """Render a speech bubble using rich Panel with rarity color.

    Matches CompanionSprite.tsx SpeechBubble styling:
    - Round border (Panel default)
    - Rarity-colored border
    - Italic text
    - Dim when fading
    """
    color = RARITY_COLORS.get(companion.rarity, 'dim')
    border_color = 'dim' if fading else color
    text_style = 'dim italic' if fading else f'{color} italic'

    panel = Panel(
        Text(text, style=text_style),
        border_style=border_color,
        width=min(36, len(text) + 6),
        padding=(0, 1),
    )
    console.print(panel)


def render_companion_list(
    companions: list[Companion], active_index: int, console: Console
) -> None:
    """Render a table of all owned companions (仓库)."""
    if not companions:
        console.print('[dim]No companions yet. Type /buddy to hatch one![/dim]')
        return

    table = Table(title='Companion Collection', border_style='dim', padding=(0, 1))
    table.add_column('#', style='dim', width=3)
    table.add_column('Name', min_width=12)
    table.add_column('Species', min_width=10)
    table.add_column('Rarity', min_width=10)
    table.add_column('Face', min_width=8)
    table.add_column('Shiny', width=5)

    for i, comp in enumerate(companions):
        color = RARITY_COLORS.get(comp.rarity, 'dim')
        stars = RARITY_STARS.get(comp.rarity, '\u2605')
        face = render_face(
            CompanionBones(
                rarity=comp.rarity, species=comp.species,
                eye=comp.eye, hat=comp.hat,
                shiny=comp.shiny, stats=comp.stats,
            )
        )
        marker = '\u25b6' if i == active_index else ' '
        shiny_mark = '\u2728' if comp.shiny else ''
        table.add_row(
            f'{marker}{i + 1}',
            f'[{color}]{comp.name}[/{color}]',
            comp.species,
            f'[{color}]{stars} {comp.rarity}[/{color}]',
            face,
            shiny_mark,
        )

    console.print(table)
